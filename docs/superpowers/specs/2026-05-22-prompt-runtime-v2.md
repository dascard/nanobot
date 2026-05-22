# Prompt Runtime V2 设计

## 背景

现有主回复提示词链路存在 legacy、managed、shadow 三种模式。`shadow` 作为常驻运行模式会让真实请求处在旧提示词发送、新提示词渲染审计的混合态，容易让 Web 有效预览、Reply Eval 和真实 LLM 请求出现语义差异。

V2 的目标是把运行模式收敛为 `prompt_runtime.engine = v1 | v2`：

- `v1`：旧系统，只作为紧急回滚和对比测试来源。
- `v2`：新系统，完整接管主回复 PromptPlan 构建。
- 对比、dry-run、A/B 评估属于测试工具，不再作为线上运行模式。

## 设计原则

1. V2 另起炉灶，新增 `core/prompt_v2/`，内部不调用 `core.legacy_prompt_runtime`、`nanobot_kt.bridge._load_prompt_fragment`、`scripts.build_nanobot_prompt`，也不把 `PromptManager.render` 作为主回复编排入口。
2. V2 输出不可变 `PromptPlan`。bridge 只消费 `plan.messages_without_current_user` 和 `plan.current_user_content`，不再手写追加 identity、runtime、persona、history、tool system blocks。
3. 模板只写规则，运行时数据由 compiler 注入。历史和当前输入都用 role message 表达，不再塞进巨大 system prompt。
4. 旧 prompt 只作为迁移内容来源、v1 回滚和对比测试，不参与 V2 内部拼装。
5. Web effective-preview、Reply dry-run、Reply Eval、真实 bridge 使用同一个 V2 compiler。

## 新增模块

- `core/prompt_v2/schema.py`：定义不可变 `PromptPlan`、编译请求对象、消息访问属性。
- `core/prompt_v2/template_loader.py`：加载 `prompts.v2.default/` Markdown 模板，解析 frontmatter，返回纯模板内容。
- `core/prompt_v2/section_renderer.py`：把规则模板和运行时 section 渲染为 OpenAI-compatible messages，并生成 section hash。
- `core/prompt_v2/context_adapters.py`：把 DB、persona、tool schema、chat context、runtime meta 转换成 compiler 输入。
- `core/prompt_v2/compiler.py`：唯一 V2 编译入口 `compile_prompt_plan(...)`。
- `core/prompt_v2/audit.py`：检查重复 current user input、runtime tool prompt、persona reference、群私聊规则串线等硬条件。
- `core/prompt_v2/preview.py`：admin preview 入口的轻包装，保证和真实 compiler 同构。

## 默认模板

新增 `prompts.v2.default/`：

- `chat_group.md`
- `chat_private.md`
- `timing_gate.md`
- `reply_contract_retry.md`
- `memory_extract.md`
- `sql_analysis.md`

本次主回复先接入 `chat_group.md` 和 `chat_private.md`。其他模板先落盘，保留给后续迁移，避免本次扩大运行面。

## PromptPlan 顺序

群聊：

1. system: V2 base contract
2. system: group policy
3. system: runtime_context
4. system: identity_context
5. system: persona_reference
6. system: conversation_context_header
7. history: ChatLog recent messages
8. system: group profile / expression / jargon
9. system: runtime_tool_prompt
10. user: current input

私聊：

1. system: V2 base contract
2. system: private policy
3. system: runtime_context
4. system: identity_context
5. system: persona_reference
6. system: conversation_context_header
7. history: ConversationTurn messages
8. system: runtime_tool_prompt
9. user: current input

## 接入策略

- `prompt_runtime.engine=v1`：保留现有旧链路。旧 `prompt_system.mode` 仍可在 v1 内部过渡使用，但 V2 不读取它。
- `prompt_runtime.engine=v2`：bridge 只调用 `compile_prompt_plan`，用 `plan.messages_without_current_user` reset KT conversation，用 `plan.current_user_content` 创建 user event。
- `/prompt/effective-preview` 增加 `engine=v2` 参数。v2 时返回 `PromptPlan` 的 `messages`、`tool_schemas`、`prompt_sha256`、`section_hashes`、`warnings`、`debug`，并保持 `request_json.messages` 与真实 LLMApiRequestLog 同构。
- `/reply-test/run` 支持 `prompt_engine=v2`，并保留旧 `variant` 兼容。
- Reply Eval 新 variant 命名为 `v1_baseline`、`v2_prompt_only`、`v2_code_retry`。

## 审计与验收

V2 compiler 必须生成以下审计字段：

- `prompt_sha256`
- `section_hashes`
- `warnings`
- `debug`
- `token_estimate`

硬性验收：

- V2 请求里 current user input 只出现一次。
- `runtime_tool_prompt` 只出现一次。
- `persona_reference` 只出现一次。
- 群聊 V2 包含群聊规则，不包含私聊规则。
- 私聊 V2 包含私聊规则，不包含群聊规则。
- V2 不调用 `core.legacy_prompt_runtime`。
- V2 bridge 不调用旧 fragment builder。
- Web preview 与真实 LLM request messages 同构。
- Reply Eval 能比较 `reply_call_rate`、`expected_action_accuracy`、`no_tool_call_rate`、`fake_tool_claim_rate`。

## 测试策略

先写失败测试：

- `tests/test_prompt_v2.py` 覆盖 PromptPlan schema、群私聊消息顺序、重复注入审计、模板隔离、legacy import 禁止。
- `tests/test_prompt_trace_admin.py` 增加 V2 effective-preview 断言。
- `tests/test_reply_admin.py` 增加 `prompt_engine=v2` 和新 eval variant 映射断言。
- `tests/test_bridge_prompt_v2.py` 覆盖 bridge 在 engine=v2 时只消费 PromptPlan，不手写追加旧 dynamic blocks。

验证命令：

- `python -m pytest tests/test_prompt_v2.py -v`
- `python -m pytest tests/test_prompt_trace_admin.py tests/test_reply_admin.py tests/test_bridge_prompt_v2.py -v`
- `python -m pytest tests/ -v`

真实链路验证必须在代理变量清空后执行，并检查最新 `LLMApiRequestLog.request_json.messages` 与 `/prompt/effective-preview?engine=v2` 同构。
