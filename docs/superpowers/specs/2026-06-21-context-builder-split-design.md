# Context Builder 超大文件拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 中的「超大文件 >800 行拆分」仍有多个目标文件未处理。当前
`core/context_builder.py` 为 903 行，负责从 `ConversationTurn`、`ChatLog`、
rolling summary 和群记忆构建注入 prompt 的上下文消息。

只读审计结果显示，`core/context_builder.py` 的尾部包含一段已标记 deprecated
的旧群聊上下文兼容入口：

- `build_group_recent_context()`：构建旧 `<group_recent_context>` 文本块。
- `_lookup_evidence_snippets()`：按 evidence log ID 回查 `ChatLog` 摘要。
- `build_group_profile_context()`：构建旧群画像上下文。
- `_evidence_for()`：从 evidence map 中提取并净化证据摘要。

这组函数集中在文件末尾，主要服务旧测试、手工排查和 rollback 场景。真实回复链路已经
通过 `build_chat_context()` 和 `GroupMemoryInjectionService` 注入群聊上下文，
因此这是一处适合作为第一刀的低风险边界。

## 目标

第一阶段只做无行为变化的模块拆分：

1. 新增 `core/context_legacy.py`，承接旧群聊 context 兼容逻辑。
2. 保留 `core.context_builder` 作为兼容 facade，原有导入路径继续可用。
3. 让 `core/context_builder.py` 降到 800 行以下。
4. 不改变 prompt 文案、XML tag、返回格式、异常吞吐行为和真实运行时上下文来源。
5. 通过聚焦测试证明旧 API 行为未变。

## 非目标

本阶段不做以下事情：

- 不重写 rolling summary 逻辑。
- 不调整 `build_chat_context()`、`build_session_memory()` 或群聊真实上下文构造。
- 不删除 deprecated API。
- 不修改 prompt runtime 模板。
- 不改群记忆注入策略。
- 不合并或重命名 `core.token_utils.estimate_tokens()`。
- 不拆 `core/persona_preprocess.py`、`core/group_runtime/runtime.py`、`api/routes.py`
  或 `api/admin_routes.py`。

如果实现过程中必须改变 `build_chat_context()` 的输出结构、history 注入方式、runtime
变量或 prompt 文案，则需要暂停本阶段并同步检查 canonical Prompt Runtime 模板：

- `prompts.v2.default/chat/*`
- `prompts.v2.default/tasks/*`
- `prompts.v2.default/tools/*/usage.md`
- `core/prompt_v2/variables.py`
- `core/prompt_v2/template_registry.py`
- 必要时同步 `data/prompts_v2/` 运行时模板

按当前设计，第一阶段不会触碰这些模板。

## 推荐方案

采用「legacy 模块 + facade 委托」方案。

### 模块边界

新增模块：

- `core/context_legacy.py`

该模块负责旧群聊上下文兼容能力：

- `build_group_recent_context(db, session_id, *, limit, max_per_msg, max_total, exclude_message_ids)`
- `build_group_profile_context(group_id)`
- `_lookup_evidence_snippets(db, evidence_ids, max_per_item)`
- `_evidence_for(evidence_map, content, max_chars)`

保留模块：

- `core/context_builder.py`

该模块继续负责真实上下文构造，并保留旧导入路径：

- `sanitize_prompt_text`
- `estimate_tokens`
- `build_session_memory`
- `build_chat_context`
- `build_group_recent_context`
- `build_group_profile_context`
- `GROUP_PROFILE_CONTEXT_DEPRECATED`
- `GROUP_PROFILE_CONTEXT_DEPRECATED_REASON`
- `_strip_speaker_prefix`
- `format_group_planner_message`
- `build_timing_recent_context`
- `build_group_recent_messages`

### Facade 策略

`core.context_builder` 中的 `build_group_recent_context()` 和
`build_group_profile_context()` 保留同名函数。函数体只做局部 import，然后委托给
`core.context_legacy`。

这样做有 3 个目的：

1. 保持 `from core.context_builder import build_group_recent_context` 不变。
2. 避免 `core.context_legacy` 在 import-time 反向拉入过多主模块状态。
3. 降低旧测试、手工脚本和 rollback 场景的迁移成本。

`core.context_legacy` 可以从 `core.context_builder` 导入以下稳定 helper：

- `GROUP_CONTEXT_MAX_AGE_MIN`
- `MAX_GROUP_RECENT_ROWS`
- `format_group_planner_message`
- `sanitize_prompt_text`

这会产生单向依赖：`context_builder` 的 facade 函数在运行时局部导入
`context_legacy`，`context_legacy` 在模块加载时导入 `context_builder` 中已定义的
稳定 helper。为避免循环导入，`context_builder` 中 facade 的局部 import 必须放在文件
尾部函数体内，不放在模块顶层。

### 数据流

`build_group_recent_context()` 的数据流保持不变：

1. 从 `ChatLog` 按 `session_id`、`role`、时间窗口读取最近消息。
2. 排除 `exclude_message_ids` 中的消息。
3. 使用 `sanitize_prompt_text()` 净化内容。
4. 使用 `format_group_planner_message()` 渲染单条消息。
5. 拼装 `<group_recent_context>` 文本块。

`build_group_profile_context()` 的数据流保持不变：

1. 通过 `SessionLocal()` 创建数据库会话。
2. 调用 `build_profile_with_evidence(group_id, db)`。
3. 渲染 `<group_memory_context>`。
4. 通过 `_evidence_for()` 渲染证据摘要。
5. 任意异常继续返回空字符串。

## 兼容性要求

本阶段必须保持以下兼容性：

- `tests/test_group_memory.py` 可以继续从 `core.context_builder` 导入
  `build_group_profile_context`、`build_group_recent_context` 和
  `GROUP_PROFILE_CONTEXT_DEPRECATED`。
- `tests/test_token_utils.py` 可以继续从 `core.context_builder` 导入
  `estimate_tokens`。
- `core/expression_learner.py` 和相关测试可以继续从 `core.context_builder`
  导入 `_strip_speaker_prefix`。
- `api/routes.py`、`app/group_ingress/helpers.py`、`app/group_ingress/service.py`、
  `core/group_runtime/runtime.py` 和 `nanobot_kt/reply_contract.py` 不需要改导入。
- deprecated 函数仍然保留旧注释语义：真实运行时不得依赖
  `build_group_profile_context()`。

## 错误处理

错误处理保持现状：

- `build_group_recent_context()` 没有额外吞异常逻辑；数据库查询或渲染异常继续向上传播。
- `build_group_profile_context()` 捕获所有异常并返回空字符串。
- `_evidence_for()` 对证据先做 HTML escape，再通过 `sanitize_prompt_text()` 做标签净化。

本阶段不把异常处理改得更严格，也不新增日志。原因是这些函数已经是旧兼容入口，拆分的目标
是降低文件体积，而不是改变运行时语义。

## 测试计划

第一阶段定向测试：

- `python -m pytest tests/test_group_memory.py::TestBuildProfile::test_profile_includes_relationships_in_context -v`
- `python -m pytest tests/test_group_memory.py::TestGroupRecentContext::test_recent_context_uses_maibot_message_prefix -v`
- `python -m pytest tests/test_token_utils.py::test_remaining_token_estimators_share_same_formula -v`

如果实现时触碰了 group formatting 或 active group context，还需要追加：

- `python -m pytest tests/test_expression_and_timing_context.py::test_build_timing_recent_context_strips_speaker_prefix -v`
- `python -m pytest tests/test_history.py::test_build_chat_context_group_uses_unified_chatlog_messages -v`
- `python -m pytest tests/test_history.py::test_build_chat_context_group_injects_active_rolling_summary -v`
- `python -m pytest tests/test_session_memory.py::test_build_chat_context_group_rolls_up_pending_conversation_turns -v`
- `python -m pytest tests/test_session_memory.py::test_build_session_memory_large_session_uses_latest_raw_window -v`
- `python -m pytest tests/test_session_memory.py::test_build_session_memory_injects_best_llm_summary -v`

提交生产代码前仍按项目规则运行：

- `python -m pytest tests/ -v`

## 验收标准

第一阶段实现完成后必须满足：

1. `core/context_legacy.py` 存在并包含旧群聊 context 兼容实现。
2. `core/context_builder.py` 低于 800 行。
3. `core.context_builder` 原有导入路径保持可用。
4. 上述定向测试通过。
5. 全量测试 `python -m pytest tests/ -v` 通过后再提交实现阶段改动。
6. 没有新增除 `main` guard 以外的 `asyncio.run()`。
7. 没有引入同步函数包装 awaitable 的新模式。

## 后续阶段排序

本阶段完成后，建议继续按以下顺序推进超大文件拆分：

1. `api/admin_routes.py`：先抽 DB Browser 到 `api/admin/db_browser_routes.py`。
2. `news_search/tool.py`：抽 legacy report/layout 到独立 legacy report 模块，并保留
   `tool.py` monkeypatch facade。
3. `api/routes.py`：先抽群聊旧 helper 或 tasks/sticker service，避免第一轮触碰
   `/chat` 和 SSE 主流程。
4. `core/persona_preprocess.py`：先抽 prompt/formatting，暂不移动
   `PersonaStateMachine` 和 `embed_text` monkeypatch 路径。
5. `core/group_runtime/runtime.py`：最后拆，先设计共享 clock 和 `core.timing_runtime`
   兼容层。

## 子 agent 分工约定

实现阶段如果继续使用子 agent，按以下边界分派：

- `context_builder` 第一刀只允许一个 writer 修改 `core/context_builder.py` 和
  `core/context_legacy.py`，避免同文件冲突。
- 其他子 agent 可以只读审查测试覆盖、导入路径和 prompt runtime 影响，但不得修改同一批文件。
- 后续拆 `api/admin_routes.py`、`news_search/tool.py`、`api/routes.py` 时，按模块 owner
  分派互不重叠的写入范围。
- 子 agent 输出必须包含：变更文件、兼容路径、验证命令、风险点。

## 回滚策略

第一阶段是纯代码搬迁和 facade 委托，没有数据迁移。若验证失败，可以把
`core/context_legacy.py` 中的函数体搬回 `core/context_builder.py`，并删除 facade 委托。
回滚不涉及数据库、配置或 prompt 模板。

## 规格自检

- 占位符：无。
- 范围：仅覆盖 `core/context_builder.py` 第一刀拆分。
- 兼容性：列出必须保留的旧导入路径和 facade 策略。
- 测试：列出定向测试和实现提交前全量测试要求。
- 风险：明确循环导入、prompt runtime 和 monkeypatch 相关边界。
