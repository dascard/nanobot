# Maibot 对齐实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 或等效流程逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 Nanobot 的提示词注入、群聊记忆和回复模型路由向 Maibot 的 timing/planner/replyer 分层靠拢。

**架构：** 保留 KT Agent 单轮工具循环，先在 bridge 层结构化 runtime context，并在模型路由层给回复主链路单独配置高智能模型策略。群聊记忆继续使用本地 `GroupMemory`，但输出改为 XML 风格结构上下文。

**技术栈：** FastAPI、KT Agent、pytest、SQLite、New-API model registry。

---

## 文件结构

- 修改：`config.py`，新增回复模型路由配置。
- 修改：`nanobot_kt/bridge.py`，统一 runtime/persona/history/group context 注入，应用 reply 模型策略。
- 修改：`core/group_memory.py`，在群画像中暴露 relationships。
- 修改：`core/context_builder.py`，更新 history header 与 group memory context 渲染。
- 修改：`api/routes.py`，去掉自然语言输入前缀，补齐 bridge metadata。
- 修改：`clients/classifier_client.py`，将 TimingGate prompt 改为 Maibot 式群聊节奏判断。
- 修改：`core/timing_runtime.py`，continue 时返回 pending_text/source_message_ids。
- 修改：`creatures/nanobot/prompts/system/*.md`，更新结构标签、记忆语义和 Maibot 式分层说明。
- 生成：`creatures/nanobot/prompt.md`，由 prompt builder 同步。
- 测试：`tests/test_prompt_contract.py`、`tests/test_group_memory.py`、`tests/test_kt_framework.py`、`tests/test_api.py`、`tests/test_timing_runtime.py`。

## 任务 1：写失败测试锁定新契约

- [x] 在 `tests/test_prompt_contract.py` 增加 prompt 必须包含 `<runtime_context>`、`<group_memory_context>`，且不再引用 `[GroupProfileContext]`。
- [x] 在 `tests/test_group_memory.py` 增加 group profile 输出 relationships 的断言。
- [x] 在 `tests/test_kt_framework.py` 增加 bridge 注入 `<runtime_context>` 的断言，并增加 reply 模型 intel floor 的断言。
- [x] 在 `tests/test_api.py` 增加 bridge query 不再以 `[私聊] 当前用户输入` 开头的断言。
- [x] 增加群聊 L0 取消、Maibot 消息块、TimingGate pending payload 的失败测试。
- [x] 运行定向 pytest，确认至少一个测试因功能未实现而失败。

## 任务 2：实现结构化上下文

- [x] 修改 `NanobotBridge`，新增 `_build_runtime_context()`。
- [x] 将 persona 注入改成 `<persona_reference user_id="...">`，移除用户可见旧 marker。
- [x] history header 改成 `<history_context>` 权限说明。
- [x] 群聊注入使用 `<group_memory_context>`。
- [x] 修改 `api/routes.py`，当前 query 仅保留 `<user_input>`，元信息放入 metadata。

## 任务 3：实现群记忆上下文升级

- [x] `build_profile()` 返回 `relationships`。
- [x] `build_group_profile_context()` 渲染 topics/style/slang/events/relationships/preferences。
- [x] 确保无有效 profile 时仍返回空字符串。

## 任务 4：实现回复模型独立策略

- [x] 在 `config.py` 增加 `LLM_MODEL_REPLY`、`REPLY_MODEL_INTEL_FLOOR`、`REPLY_MODEL_INTEL_BOOST`、`REPLY_MODEL_MAX_COST`。
- [x] 在 bridge 模型路由中计算 `reply_intel_floor = max(complexity - 1 + boost, floor)`。
- [x] 如果 `LLM_MODEL_REPLY` 非空，候选列表优先只用该模型。
- [x] 日志中输出 `[ReplyModel]` 路由原因和候选。

## 任务 5：取消 L0 并接入 Maibot 群聊链路

- [x] `_derive_group_trigger_reason()` 只返回结构化触发来源，不再使用关键词判断是否入场。
- [x] `/group/message` 所有非重复群消息都进入 `GroupRuntime.process_message()`。
- [x] `GroupRuntime` continue 结果返回 Maibot 风格 `pending_text` 和 `source_message_ids`。
- [x] `build_group_recent_context()` 输出 `<group_recent_context>` 和 `[msg_id]/[时间]/[用户名]/[发言内容]`。
- [x] bridge 注入 `group_recent_context`，主回复 prompt 认识 planner/replyer 职责边界。
- [x] 群聊 bridge 回复写入 assistant ChatLog 与 ConversationTurn 工作记忆。

## 任务 6：同步 prompt 与验证

- [x] 修改 prompt system fragments。
- [x] 运行 `python scripts/build_nanobot_prompt.py` 同步 `prompt.md`。
- [x] 运行定向 pytest。
- [x] 运行 `python scripts/build_nanobot_prompt.py --check`。
- [x] 运行 `python -m pytest tests/ -q`。
