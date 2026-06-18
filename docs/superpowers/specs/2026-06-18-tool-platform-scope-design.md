# P2-1 工具 platform 维度配置设计

> 2026-06-18 · P2-1 目标是完成 `docs/todo.md` 路线项 4：让运行时工具配置支持 platform 维度，并让审计记录能解释「某平台为什么启用或禁用了某工具」。

---

## 背景

P1 收敛去债已经完成：Prompt Runtime 主路径收口、同步 IO 热路径隔离、模型能力校验和流式基础链路都已落地。下一阶段进入 P2 多平台底座，首个缺口是工具策略仍缺 platform 维度。

现有工具配置并不是全局单一开关。`build_tool_plan()` 会调用 `resolve_effective_tools()`，按默认模板、硬约束、运行时预设和 `ToolOverride` 覆盖生成每轮 ToolPlan；`resolve_final_tools()` 也复用同一套解析结果裁剪真实请求出口工具。问题是当前覆盖范围只有 `chat_type`、`group`、`user`，无法表达「Web 平台禁用图片生成」「QQ 平台保留贴纸工具」「synergy 平台禁用 QQ 专属工具」这类跨会话类型的平台策略。

本阶段只处理工具策略和审计，不把范围扩成响应信封、出站渲染契约或 Prompt Runtime 模板平台化。后者分别属于后续 P2-2、P2-3 和 P2-4。

## 当前事实

### 工具解析链路

当前主链路是：

- `nanobot_kt/bridge.py::NanobotBridge.handle_message()`
- `core/tool_plan.py::build_tool_plan()`
- `core/runtime_tool_service.py::resolve_effective_tools()`
- `core/runtime_tool_service.py::record_runtime_tool_decision()`
- `core/final_tools.py::resolve_final_tools()`

`resolve_effective_tools()` 的合并顺序是：

1. 从 `TOOL_METADATA` 和 `SystemSetting` 读取 `private` / `private_superuser` / `group` 默认值。
2. 应用初始硬约束：`force_enabled` 和群聊 `force_disabled_group`。
3. 应用 `runtime_preset`：`none`、`lightweight`、`full`。
4. 应用 DB 覆盖：`ToolOverride(scope_type=chat_type/group/user)`。
5. 再次应用硬约束兜底。

DB 覆盖排序为 `chat_type -> group -> user`，后者覆盖前者。因为 DB 覆盖在 `lightweight` 后执行，所以当前 group/user/chat_type 覆盖可以重新放开轻量预设禁用的工具；但 `runtime_preset=none` 会跳过整个 override 阶段，因此不会被覆盖放开。

### 数据模型

`ToolOverride` 当前字段包括：

- `tool_name`
- `scope_type`
- `scope_id`
- `enabled`
- `reason`
- `updated_at`
- `created_at`

唯一约束是 `(tool_name, scope_type, scope_id)`。这个结构已经是泛化 scope 表，首版无需新增 `platform` 列即可表达平台全局覆盖。

`RuntimeToolDecision` 当前字段包括：

- `session_id`
- `message_id`
- `chat_type`
- `group_id`
- `user_id`
- `runtime_preset`
- `enabled_tools_json`
- `disabled_tools_json`
- `disabled_reasons_json`
- `effective_tools_json`
- `created_at`

它没有 `platform` 字段。平台覆盖生效后，如果不记录 platform，Admin / WebUI 只能看到某工具被禁用，却无法解释是不是平台策略导致。

### 入口透传

`docs/message-field-standard.md` 已定义 `client_meta.platform`。群聊入口已经从 `client_meta.platform` 解析 `platform` 并传给 TimingGate，但 `_continue_to_bridge()` 构造的 `bridge_meta` 没有带 platform。私聊 `/chat` 有 `client_meta`，但构造 `bridge_meta` 时也没有写入 platform。`NanobotBridge.handle_message()` 当前只从 `meta` 推导 `chat_type`、`group_id`、`user_id` 和 `runtime_preset`。

因此只改 `resolve_effective_tools()` 不足以让真实请求按平台生效；入口必须显式把平台传到 Bridge，再由 Bridge 传给 ToolPlan 和 runtime decision。

### Admin / WebUI

Admin API 当前状态：

- `ToolOverrideBody.scope_type` 注释只允许 `group`、`user`、`chat_type`。
- `PUT /tools/{tool}/override` 只接受 `group/user/chat_type`。
- `GET /tools` 和 `GET /tools/effective` 没有 `platform` query 参数。
- `GET /tools/decisions` 不返回 platform。
- `GET /tools/targets` 只按 group/user 搜目标；其它值会被当作 group。

WebUI 工具页当前只支持默认模板和 group/user 指定覆盖。后端先支持 platform 后，旧 WebUI 不传 platform 仍可按 QQ fallback 运行；但如果不补最小 selector，管理员无法从页面配置平台覆盖。

## 方案选择

### 方案 A：复用 `ToolOverride(scope_type="platform")`

在现有 `tool_overrides` 表中新增合法 scope：

```text
scope_type = "platform"
scope_id = "qq" | "web" | "synergy" | ...
```

解析顺序改为：

```text
chat_type -> platform -> group -> user
```

`RuntimeToolDecision` 新增 `platform` 列用于审计，入口负责把标准化 platform 传入 ToolPlan。

结论：采用。它复用现有泛化表结构和唯一约束，迁移面小，能覆盖 P2-1 的平台全局策略目标。

### 方案 B：给 `ToolOverride` 新增 `platform` 列

把 `platform` 变成独立列，并把唯一约束改成 `(tool_name, platform, scope_type, scope_id)`。这样可以表达「某平台下某群 / 某用户」的组合策略。

结论：暂不采用。组合 scope 是更完整的权限矩阵，但迁移、Admin API、WebUI 和 precedence 都会明显变复杂。P2-1 当前目标是补平台全局维度，不需要一次性支持复合覆盖。

### 方案 C：只在 WebUI 或文档层增加平台说明

不改运行时解析，只在工具页增加平台字段或在文档里约定不同平台应使用不同 group/user ID。

结论：不采用。这样无法在真实 ToolPlan 中生效，也无法审计平台策略。把平台揉进 group/user ID 还会污染身份、记忆和权限边界。

## 选定方案

采用方案 A：复用 `ToolOverride(scope_type="platform")`，并给运行时决策审计补 `platform` 字段。

首版规则：

- 函数默认参数使用 `platform: str = ""`，保持直接调用方兼容。
- 真实入口统一解析 platform，缺省为 `qq`。
- 只有 platform 非空时才匹配 `scope_type="platform"`，避免空 `scope_id` 脏数据误命中。
- override precedence 固定为 `chat_type < platform < group < user`。
- `runtime_preset=none` 继续跳过所有 override，platform 不能放开 `none`。
- `force_enabled` 和群聊 `force_disabled_group` 继续最终兜底，platform 不能绕过硬约束。
- `ToolPlan.sha256` 不直接加入 platform；当 platform 改变工具启停结果时，hash 会随 enabled / disabled / prompt / schema 变化。如果不同平台最终工具集合完全一致，则 hash 相同是可接受结果。

## 数据模型与迁移

### `ToolOverride`

不新增列，只更新注释、校验和 Admin API：

```text
scope_type: "chat_type" | "platform" | "group" | "user"
scope_id:
  - chat_type: "private" | "private_superuser" | "group"
  - platform: "qq" | "web" | "synergy" | 自定义非空平台名
  - group: 群 ID
  - user: 用户 ID
```

平台名首版只做轻量归一：

- `strip()`。
- 转小写。
- 空值拒绝写入 override。

不在数据库层加 CHECK。SQLite 迁移成本和历史数据兼容性不值得为首版增加复杂度，合法性由 API 和运行时 helper 控制。

### `RuntimeToolDecision`

新增列：

```python
platform = Column(String, default="")
```

需要在 `core/schema_migrations.py` 增加补列迁移。原因是 `Base.metadata.create_all()` 不会给已有表添加新列；如果只改 ORM 模型，线上旧库写入或 Admin 查询会在旧表结构上失败。

Admin `/tools/decisions` 响应新增：

```json
{
  "platform": "qq"
}
```

本阶段不要求按 platform 筛选 decisions；可作为后续 WebUI 优化。

## 运行时改动

### 函数签名

需要新增 `platform` 参数：

- `resolve_effective_tools(..., platform: str = "")`
- `build_tool_plan(..., platform: str = "")`
- `resolve_final_tools(..., platform: str = "")`
- `record_runtime_tool_decision(..., platform: str = "")`

`build_runtime_tool_prompt()` 不需要新增 platform 参数。RuntimeTool prompt 只描述当前轮启停结果和原因；平台维度通过 disabled reason 和 decision 审计解释即可。

### 解析顺序

DB 查询条件改为按非空 scope 拼装：

```text
chat_type == normalized_chat_type
platform == normalized_platform（非空才加入）
group == group_id（非空才加入）
user == user_id（非空才加入）
```

排序权重：

```python
{"chat_type": 1, "platform": 2, "group": 3, "user": 4}
```

原因文案沿用现有格式：

```text
被 platform:web 覆盖禁用
```

若 `row.reason` 非空，仍优先使用管理员填写的 reason。

### 入口透传

私聊 `/chat`：

- 从 `req.client_meta` 读取 `platform`。
- 缺省为 `qq`，兼容旧 QQ 调用和测试。
- 写入 `bridge_meta["platform"]`。

群聊 `/group/message`：

- 复用已解析的 `platform = client_meta.get("platform") or "qq"`。
- `_continue_to_bridge()` 写入 `bridge_meta["platform"]`。

Bridge：

- `NanobotBridge.handle_message()` 从 `meta.get("platform")` 读取并归一。
- 调用 `build_tool_plan(platform=platform, ...)`。
- 调用 `record_runtime_tool_decision(platform=platform, ...)`。
- 如有 `resolve_final_tools()` 调用点，也传入同一 platform。

Timer / deprecated 路径：

- 仍保留的定时 / timer 回调路径如果构造 `bridge_meta`，应从 runtime state 或 `client_meta` 取 platform。
- 找不到时使用 `qq` fallback。

## Admin API 设计

### `PUT /tools/{tool_name}/override`

允许：

```json
{
  "scope_type": "platform",
  "scope_id": "web",
  "enabled": false,
  "reason": "Web 平台暂不开放图片生成"
}
```

校验规则：

- `scope_type` 必须是 `group/user/chat_type/platform`。
- `scope_type="platform"` 时 `scope_id` 必须非空，写入前小写归一。
- `scope_type="chat_type"` 仍限制为 `private/private_superuser/group`。
- `force_enabled` 工具继续拒绝 override。

### `GET /tools`

新增 query 参数：

```text
platform=qq
```

返回值新增或更新：

- 顶层返回 `platform`，明确本次预览的平台上下文。
- `configured_enabled` / `runtime_effective` 计算时传入 platform。
- `override_present` / `override_enabled` 在指定 `user_id`、`group_id`、`platform` 时按最具体 scope 展示。首版推荐展示当前最高优先级命中的覆盖；详细覆盖链可后续再做。

兼容策略：

- 不传 platform 时默认 `qq`，与真实入口一致。
- 旧前端不传 platform 仍返回 QQ 语义。

### `GET /tools/effective`

新增 query 参数：

```text
platform=qq
```

返回值新增：

```json
{
  "platform": "qq"
}
```

计算 `enabled`、`disabled`、`prompt` 和 `tool_schemas` 时都使用 platform-aware ToolPlan 结果。

### `GET /tools/targets`

首版支持 `scope_type=platform`，返回已知平台候选。

候选来源按优先级：

1. 固定内置候选：`qq`、`web`、`synergy`。
2. 已存在的 `ToolOverride(scope_type="platform")`。
3. ChatLog / runtime meta 中能稳定解析出的 platform（如实现成本低则加入；实现成本高可后置）。

即使暂不从历史日志收集，也要保证平台覆盖 UI 有基础候选，不需要管理员手输常见平台名。

### `GET /tools/decisions`

返回 `platform`。首版可不增加过滤参数。

## WebUI 设计

WebUI 本阶段做最小可用更新：

- 工具页预览上下文增加 platform selector。
- 默认值为 `qq`。
- 指定覆盖对象增加「指定平台」。
- 当选择「指定平台」时，调用 `PUT /tools/{tool}/override` 写入 `scope_type="platform"`。
- `GET /tools` 和 `GET /tools/effective` 带上当前 platform。

不做完整权限矩阵视图，不做 platform + group/user 组合覆盖 UI。

如果实现阶段需要进一步拆分，允许后端先行、WebUI 作为独立阶段提交；但 API 必须在本设计内保持稳定。

## 测试计划

### 后端工具解析

目标文件：`tests/test_tool_plan.py`。

新增测试：

- platform override 可以禁用默认启用工具。
- platform override 可以启用默认禁用或 lightweight 禁用的工具。
- precedence 为 `chat_type < platform < group < user`。
- `runtime_preset=none` 不会被 platform override 放开。
- `force_enabled` 和群聊 `force_disabled_group` 不会被 platform override 绕过。
- `record_runtime_tool_decision()` 写入 platform。

### 入口透传

目标文件：`tests/test_api.py`、`tests/test_kt_framework.py` 或现有群入口测试文件。

新增测试：

- `/chat` 把 `client_meta.platform` 写入 Bridge metadata。
- `/group/message` 把 `client_meta.platform` 传给 TimingGate 后，也传给 Bridge metadata。
- Bridge 调用 `build_tool_plan(platform=...)` 和 `record_runtime_tool_decision(platform=...)`。

### Admin API

目标文件：`tests/test_admin_api.py`。

新增测试：

- `PUT /tools/{tool}/override` 接受 `scope_type="platform"`。
- `GET /tools/effective?platform=web` 反映平台覆盖。
- `GET /tools?platform=web` 的 runtime preview 和 override 状态反映平台覆盖。
- `GET /tools/targets?scope_type=platform` 返回 `qq/web/synergy` 和已配置平台覆盖。
- `GET /tools/decisions` 返回 platform。

### 迁移

目标文件：`tests/test_schema_migrations.py`。

新增测试：

- 旧库已有 `runtime_tool_decisions` 表时，迁移会补 `platform` 列。
- 迁移可重复执行。

### WebUI

目标文件：现有 WebUI 静态测试或新增工具页测试。

新增测试：

- 工具页包含 platform selector。
- 指定覆盖可选择「平台」。
- 调用工具 API 时带上 platform 参数。

## 验收标准

- [ ] `resolve_effective_tools()` 支持 platform scope，并有 precedence 测试。
- [ ] `ToolOverride(scope_type="platform")` 可通过 Admin API 写入、删除和预览。
- [ ] `RuntimeToolDecision.platform` 有 ORM 字段、迁移、写入和 Admin 查询输出。
- [ ] `/chat`、`/group/message` 和 Bridge 主链路都会把 platform 传给 ToolPlan。
- [ ] WebUI 工具页能选择 platform 并配置平台覆盖，或明确拆成后端先行阶段并在计划中单独列出。
- [ ] `docs/message-field-standard.md` 补充工具策略会消费标准化 `client_meta.platform`，缺省 `qq` 兼容旧 QQ。
- [ ] 定向测试、全量测试、`git diff --check` 和文档扫描通过。

## 非目标

- 不实现 platform + group/user 复合覆盖。
- 不修改 Prompt Runtime 的 platform × chat_type 模板拆分。
- 不重构响应信封或 QQ 出站渲染契约。
- 不把 `TOOL_METADATA` 的默认字段升级为 platform 维度矩阵。首版通过 `ToolOverride(scope_type="platform")` 表达平台策略。
- 不清理历史 `qq:` / `group_` ID 兼容格式。

## 风险与控制

- **默认平台语义不一致。** 控制：函数默认空值保持兼容，真实入口和 Admin preview 默认 `qq`。
- **空 platform 误命中脏数据。** 控制：只有 platform 非空时才加入 platform override 查询条件。
- **硬约束被覆盖。** 控制：保留初始和最终硬约束兜底，新增测试覆盖 `force_enabled`、`force_disabled_group` 和 `runtime_preset=none`。
- **旧库迁移遗漏。** 控制：在 `core/schema_migrations.py` 增加补列迁移，并用旧表测试验证。
- **WebUI 一次性改动过大。** 控制：先实现最小 selector 和平台覆盖入口，不做完整矩阵视图。
- **范围滑向 Prompt 平台化。** 控制：Prompt Runtime platform 模板拆分明确放到 P2-4。

## 后续

P2-1 完成后，下一条主线应进入 P2-2「标准化请求 / 响应信封」，为私聊、群聊、SSE done 和 QQ push 统一响应结构，并把 `reply_meta` 作为出站渲染契约的稳定载体。
