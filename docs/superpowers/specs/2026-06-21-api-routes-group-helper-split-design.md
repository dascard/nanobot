# API Routes 群消息 Helper 拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」仍剩 `api/routes.py`、`api/admin_routes.py`
和 `core/persona_preprocess.py`。当前 `api/routes.py` 约 3434 行，其中 `/group/message`
主流程已经委托给 `app.group_ingress.service.GroupIngressService`，但旧的群消息 helper 仍
滞留在 `api/routes.py` 中。

这些 helper 与 `app/group_ingress/helpers.py` 已有实现高度重复，包括 OneBot segments
归一化、mentions / reply_to / directed 元数据构造、表情包 payload 识别、群消息文本渲染、
表情包注册、TimingGate meta 标注、重复回复检测和群回复持久化。继续保留两套实现会扩大维护面：
后续修复如果只改 app 层 helper，旧 route 私有 helper 容易漂移。

## 目标

本阶段做 `api/routes.py` 的低风险第一刀：

- 将 `api/routes.py` 中已由 `app.group_ingress.helpers` 承担的群消息 helper 重复实现删除。
- 在 `api/routes.py` 保留同名 underscore 兼容别名，满足既有测试和外部灰度脚本可能的私有导入。
- 让 `group_timing_timer()`、私聊流式错误路径等仍可通过原 `_pop_bridge_reply_meta()` 等名称工作。
- 不迁移 `/group/message` 路由本身，不改变 `GroupMessageRequest`、`GroupTimingRequest` 或 HTTP 路径。

完成后，`api/routes.py` 应下降约 450 行以上，并继续保留旧私有 helper 导入兼容。

## 非目标

- 不拆 `proxy_chat()` 私聊主流程。
- 不拆 `group_timing_timer()` 的业务流程。
- 不修改 `app/group_ingress/service.py` 的主流程语义。
- 不改 prompt runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime 输入。
- 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。
- 不处理 `api/admin_routes.py` 和 `core/persona_preprocess.py`，它们留给后续阶段。

## 方案

### 推荐方案：API 层兼容 re-export

在 `api/routes.py` 导入 `app.group_ingress.helpers`，并将旧 underscore 私有 helper 绑定到 app 层实现：

- `_normalize_onebot_segments = group_ingress_helpers.normalize_onebot_segments`
- `_extract_mentions_from_segments = group_ingress_helpers.extract_mentions_from_segments`
- `_normalize_group_mentions = group_ingress_helpers.normalize_group_mentions`
- `_normalize_group_reply_to = group_ingress_helpers.normalize_group_reply_to`
- `_derive_group_direction = group_ingress_helpers.derive_group_direction`
- `_detect_group_bot_sender = group_ingress_helpers.detect_group_bot_sender`
- `_build_group_message_meta = group_ingress_helpers.build_group_message_meta`
- `_safe_group_client_meta = group_ingress_helpers.safe_group_client_meta`
- `_group_sticker_payloads = group_ingress_helpers.group_sticker_payloads`
- `_render_segments_to_text = group_ingress_helpers.render_segments_to_text`
- `_build_group_message_text = group_ingress_helpers.build_group_message_text`
- `_register_group_stickers_from_message = group_ingress_helpers.register_group_stickers_from_message`
- `_annotate_group_timing_event = group_ingress_helpers.annotate_group_timing_event`
- `_normalize_reply_for_duplicate = group_ingress_helpers.normalize_reply_for_duplicate`
- `_pop_bridge_reply_meta = group_ingress_helpers.pop_bridge_reply_meta`
- `_derive_group_agent_result = group_ingress_helpers.derive_group_agent_result`
- `_find_recent_duplicate_group_reply = group_ingress_helpers.find_recent_duplicate_group_reply`
- `_log_group_no_reply = group_ingress_helpers.log_group_no_reply`
- `_persist_group_bridge_reply = group_ingress_helpers.persist_group_bridge_reply`
- `_derive_group_trigger_reason = group_ingress_helpers.derive_group_trigger_reason`

同时删除 `api/routes.py` 中对应重复函数和 `_MAX_*` / `_ALLOWED_SEGMENT_KEYS` 重复常量。这样
`api.routes` 的旧私有导入仍能用，但真实实现只有 `app/group_ingress/helpers.py` 一份。

`_read_client_meta_from_log()` 当前只看到定义未看到引用。本阶段将它作为删除候选：如果计划执行时
再次搜索仍无引用，直接删除；如果发现外部灰度脚本依赖，再补到 `app.group_ingress.helpers` 并保留
同名兼容别名。

### 为什么不直接删除所有旧名

`tests/test_api.py` 和 `tests/test_reply_admin.py` 仍直接导入部分 `_persist_group_bridge_reply`、
`_find_recent_duplicate_group_reply` 和 `_derive_group_agent_result`。直接删除旧名会造成不必要的
兼容断裂。保留薄别名更符合本阶段「拆重复实现，不改行为面」的目标。

### 为什么不先拆 `api/admin_routes.py`

`api/admin_routes.py` 行数更大，但它包含多个 admin 子域和旧路由注册模式，下一刀需要先确认
路由注册顺序、鉴权依赖和旧 monkeypatch 入口。`api/routes.py` 的群消息 helper 已有 app 层
承接模块，第一刀更明确，验证成本更低。

## 测试策略

### 红灯

新增 `tests/test_api_routes_group_helper_facade.py`：

- 断言 `api.routes._build_group_message_text` 与
  `app.group_ingress.helpers.build_group_message_text` 是同一对象。
- 断言 `_persist_group_bridge_reply`、`_find_recent_duplicate_group_reply`、
  `_derive_group_agent_result`、`_normalize_onebot_segments`、`_extract_mentions_from_segments`、
  `_safe_group_client_meta`、`_render_segments_to_text`、`_derive_group_trigger_reason` 等旧
  underscore 名称指向 app 层 helper。
- 断言 `api/routes.py` 行数低于 3000 行。

实现前运行该测试应失败：旧函数仍是 `api.routes` 本地定义，且文件行数约 3434 行。

### 绿灯与回归

实现后运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_api_routes_group_helper_facade.py \
  tests/test_api.py::test_persist_group_bridge_reply_uses_runtime_bot_name \
  tests/test_api.py::test_find_recent_duplicate_group_reply_detects_long_repeat \
  tests/test_api.py::test_find_recent_duplicate_group_reply_ignores_short_repeat \
  tests/test_reply_admin.py::test_group_agent_result_uses_popped_no_reply_meta \
  tests/test_reply_admin.py::test_group_agent_result_preserves_prompt_v2_audit_failure \
  -q
```

相邻回归运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_api.py \
  tests/test_group_response_envelope.py \
  tests/test_api_push_envelope.py \
  tests/test_reply_admin.py::test_group_agent_result_uses_popped_no_reply_meta \
  tests/test_reply_admin.py::test_group_agent_result_preserves_prompt_v2_audit_failure \
  -q
```

最终运行：

```bash
python -m compileall api/routes.py app/group_ingress/helpers.py -q
rg -n "def _(normalize_onebot_segments|extract_mentions_from_segments|normalize_group_mentions|normalize_group_reply_to|derive_group_direction|detect_group_bot_sender|build_group_message_meta|safe_group_client_meta|group_sticker_payloads|render_segments_to_text|build_group_message_text|register_group_stickers_from_message|annotate_group_timing_event|normalize_reply_for_duplicate|pop_bridge_reply_meta|derive_group_agent_result|find_recent_duplicate_group_reply|log_group_no_reply|persist_group_bridge_reply|derive_group_trigger_reason)" api/routes.py
wc -l api/routes.py
git diff --check
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/ -v
```

`rg` 预期无匹配；`wc -l` 预期低于 3000；全量 pytest 预期 0 failures。

## 风险与缓解

- **风险：旧私有 helper 行为与 app helper 有细微差异。**
  缓解：优先用现有旧导入测试和 group response envelope / API 回归验证；app helper 已被
  `GroupIngressService` 线上主路径使用，作为唯一实现更合理。
- **风险：测试 monkeypatch 旧私有名后不影响 app helper。**
  缓解：本阶段不承诺旧私有名 monkeypatch 会影响 app service；旧私有名只保证导入兼容。搜索结果
  未发现测试 monkeypatch 这些私有名。
- **风险：`group_timing_timer()` 仍在 `api/routes.py` 内调用旧私有名。**
  缓解：旧名变成 app helper 别名后调用保持可用；后续若拆 timer，再单独设计。

## 完成标准

- `api/routes.py` 行数低于 3000。
- `api/routes.py` 中不再定义上述重复群消息 helper。
- 旧 underscore helper 名称仍可从 `api.routes` 导入。
- 定向、相邻和全量测试通过。
- `docs/todo.md`、`docs/plan_walkthrough.md` 和本阶段计划文件记录执行状态。
