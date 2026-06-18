# client_meta 边界层校验设计

日期：2026-06-18

## 背景

P2-2 已完成响应信封兼容双写，`/chat`、`/chat` SSE done、`/group/message` 和 push 出口已经共享 `reply`、`messages`、`reply_meta`、`meta`。路线项 5 仍剩一个尾项：`client_meta` 目前主要靠文档约定，运行时只从中读取 `platform`，没有统一校验 `platform`、`chat_type` 和 `trace.request_id`。

`client_meta` 是多平台底座的入口边界。如果不做轻量解析，新平台 adapter 可能继续传入大小写不稳定的平台名、伪造的 `chat_type`、非对象 `trace` 或过长 `request_id`，后续 P2-3 出站渲染契约和 P2-4 platform prompt 分支都会继承不稳定输入。

## 目标

- `/chat` 和 `/group/message` 在进入业务流程前完成 `client_meta` 轻量校验。
- `platform` 缺省为 `qq`，传入时归一为小写稳定标识。
- `chat_type` 由服务端入口事实决定，外部传入时必须与入口一致，不能伪造覆盖。
- `trace.request_id` 若存在，必须是字符串，裁剪到安全长度，并进入响应 `meta`。
- 保持旧 QQ / NapCat 调用兼容：缺少 `client_meta` 不报错，群聊 `stickers` 等既有扩展字段不丢失。

## 非目标

- 不定义 QQ 出站 `segments`、CQ renderer、图片 / HTML 渲染策略；这些属于 P2-3。
- 不把 `client_meta` 做成严格白名单对象；现有 `stickers`、`raw`、`business` 等扩展仍需保留。
- 不修改鉴权、历史注入、工具 platform override 解析顺序。
- 不把 `client_meta.chat_type` 扩展为业务路由维度；本阶段只校验它不能与入口事实冲突。

## 方案比较

### 方案 A：只在文档中继续约定

成本最低，但不能阻止错误输入进入运行时。P2-3 / P2-4 会继续依赖不稳定 platform 和 trace 信息，不满足路线项 5 的“边界层解析 / 校验”目标。

### 方案 B：Pydantic 模型强约束整个 client_meta

可以获得严格 schema，但会破坏现有 QQbot 兼容扩展，例如 `stickers`、`message_type`、`raw_segment_types`，也会让后续平台新增字段必须频繁改模型。本阶段风险过高。

### 方案 C：共享轻量 helper 归一关键字段

新增 `core/client_meta.py`，只验证和归一化关键字段：`platform`、`chat_type`、`trace.request_id`。其它扩展字段原样保留。`api/routes.py` 在 `/chat` 和 `/group/message` 边界调用 helper，并把归一化结果回写到请求对象，现有业务代码继续通过 `req.client_meta` 读取。

采用方案 C。它覆盖路线项 5 的必需字段，同时避免把兼容扩展误删。

## 字段规则

### 顶层

- `client_meta` 缺省或 `None` 时按空对象处理。
- HTTP 请求中 `client_meta` 不是 JSON object 时，Pydantic 会返回 422；helper 仍处理直接构造模型或内部调用中的非 dict，按空对象降级。
- helper 返回新的 dict，不修改调用方传入对象本身。

### platform

- 缺省值：`qq`。
- 传入值必须是字符串。
- 归一化：`strip().lower()`。
- 格式：`^[a-z][a-z0-9_-]{0,31}$`。
- 无效值返回 400，错误信息使用稳定、非内部实现的文本。

### chat_type

- `/chat` 根据 `session_id` 推导：`private_` 前缀为 `private`，否则为 `group`。
- `/group/message` 固定为 `group`。
- `client_meta.chat_type` 缺省时写入服务端推导值。
- 传入值必须是字符串，归一化为小写后必须与服务端推导值一致；不一致返回 400。

### trace

- `trace` 缺省或 `None` 时省略。
- 传入时必须是对象。
- `trace.request_id`、`trace.correlation_id`、`trace.source` 若存在，必须是字符串。
- 字符串先 `strip()`，空字符串丢弃，非空值裁剪到 128 字符。
- 归一化后的 `trace` 保留在 `client_meta.trace`，并把 `request_id` 投影到响应 `meta.request_id`，便于客户端和日志关联。

## 数据流

1. `/chat` 入口收到 `ChatProxyRequest`。
2. 根据 `session_id` 推导入口 `chat_type`。
3. 调用 `normalize_client_meta(req.client_meta, expected_chat_type=chat_type)`。
4. 将返回值回写到 `req.client_meta`。
5. 后续 `_chat_request_platform()`、Bridge metadata、响应信封 `meta` 使用归一化后的值。

`/group/message` 同理，但 `expected_chat_type="group"`，校验发生在 `GroupIngressService` 创建前，避免错误输入进入 TimingGate 和 ambient log。

## 错误处理

边界 helper 抛出 `ClientMetaValidationError`。HTTP route 捕获后返回：

```json
{
  "detail": "invalid client_meta: <reason>"
}
```

错误只描述字段级原因，不回显原始 payload。

## 测试计划

- 单元测试覆盖 helper：
  - 缺省 `client_meta` 返回 `platform=qq` 和入口 `chat_type`。
  - `platform` 小写归一化。
  - `chat_type` 不一致会失败。
  - `trace.request_id` 裁剪并保留。
  - 非字符串 `trace.request_id` 会失败。
- API 测试覆盖：
  - `/chat` 成功响应 `meta.request_id` 来自归一化 `client_meta.trace.request_id`。
  - `/chat` 传入冲突 `client_meta.chat_type` 返回 400，且不调用 Bridge。
  - `/group/message` 传入冲突 `client_meta.chat_type` 返回 400，且不进入 TimingGate。
  - `/group/message` 合法 trace 会保留到 ambient log 的 `meta_json.client_meta.trace`。

## 验收标准

- `client_meta.platform`、`client_meta.chat_type`、`client_meta.trace.request_id` 有运行时校验和测试。
- 旧 QQ 调用不传 `client_meta` 仍默认 `platform=qq`。
- 群聊扩展字段如 `stickers` 不被删除。
- P2-2 响应信封旧字段兼容性不变。
- `docs/todo.md` 和 `docs/plan_walkthrough.md` 明确路线项 5 的剩余尾项状态。
