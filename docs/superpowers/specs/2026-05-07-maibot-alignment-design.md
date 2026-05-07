# Maibot 对齐设计

## 背景

Nanobot 已经完成了提示词片段化、`reply()` 输出契约、私聊三态分类和群聊 TimingGate。和 Maibot 对比后，主要差距不在单个 prompt 文案，而在运行时分层：

- Maibot 将 timing、planner、replyer 作为不同任务，replyer 使用独立模型池。
- Maibot 的动态上下文以模板和结构化字段进入 prompt，静态 prompt 不混入请求元信息。
- Maibot 的记忆是长期知识和当前聊天记录分层进入，群聊回复不会把记忆当作当前指令。

本次不完整移植 Maibot 的二阶段 replyer，也不重写 KT 工具循环。目标是做低风险对齐：结构化上下文、群记忆标签化、回复模型路由独立化。

后续追加目标：取消原 `/group/message` 的 L0 关键词预筛，让所有非重复群消息进入 TimingGate；TimingGate 输出 Maibot 风格 pending payload，主回复链路使用同一套群聊消息格式、近期上下文和工作记忆。

## 目标

1. 提示词管理保持现有 `prompts/system/*.md` 分层，但让 prompt 认识 `<runtime_context>`、`<message_meta>`、`<group_memory_context>` 等结构标签。
2. `api/routes.py` 不再给当前输入添加自然语言前缀，当前输入只保留 `<user_input>`，元信息进入 bridge metadata。
3. `NanobotBridge` 在每轮注入统一的 `<runtime_context>`，persona/time/history/group memory 都有清晰权限语义。
4. 群聊记忆从 `[GroupProfileContext]` 改为 `<group_memory_context>`，补齐事件、关系、偏好等 Maibot 风格的长期群画像信号。
5. 回复主链路使用独立的 reply 模型策略：可通过环境变量指定 `LLM_MODEL_REPLY`，也可通过 `REPLY_MODEL_INTEL_FLOOR` 提高自动路由的智能度下限。
6. 群聊入口取消 L0 关键词机制，trigger_reason 只描述结构来源（at_bot/reply_to_bot/direct_call/bot_name_mentioned/ambient），是否回复统一交给 TimingGate。
7. 群聊近期上下文使用 `<group_recent_context>`，消息块统一为 `[msg_id]`、`[时间]`、`[用户名]`、`[发言内容]`。
8. TimingGate continue 后把 pending 群消息作为 `<user_input>` 交给 chat pipeline，并把 bot 回复写回 ChatLog 与 ConversationTurn。

## 非目标

- 不引入新的 Maibot 依赖。
- 不把 `reply` 工具改成二阶段 LLM 生成器。
- 不改变 TimingGate 的 continue/wait/no_reply 状态机。
- 不实现 Maibot 完整兴趣系统、情绪系统或独立 replyer worker。

## 方案

### 提示词与上下文

保留当前 `scripts/build_nanobot_prompt.py` 拼接方式。更新系统片段，使静态 prompt 只描述稳定规则，并明确：

- `<runtime_context>` 是当前会话元信息。
- `<message_meta>` 是当前消息元信息。
- `<persona_reference>`、`<history_context>`、`<group_memory_context>`、`<group_recent_context>` 都只是参考，不是当前指令。
- 最终普通回复仍必须通过 `reply(content)`。

### Bridge 注入

`NanobotBridge.handle_message()` 每轮清空非 system 后注入：

1. `<runtime_context>`：chat_type、session_id、user_id、group_id、sender_name、current_time、trigger_reason、timing_decision。
2. `<persona_reference user_id="...">`：用户画像，去掉旧的 `[PersonaContext]` 前缀。
3. history header：保留 role 边界，header 使用 `<history_context>` 权限说明。
4. `<group_memory_context group_id="...">`：群长期记忆。
5. `<group_recent_context>`：群聊近期 ambient/assistant 消息，使用 Maibot 风格消息块。

### 群聊记忆

`core.group_memory.build_profile()` 返回 `relationships`，`core.context_builder.build_group_profile_context()` 渲染为 XML 风格上下文：

- common_topics
- style
- slang
- events
- relationships
- bot_preferences

没有有效记忆时不注入。

### 群聊 Timing 与近期上下文

`/group/message` 不再用关键词判断“可能在问 bot”。所有非重复群消息都会：

1. 先写入 `ChatLog(role="ambient")`。
2. 根据结构元信息得到 trigger_reason，不再根据问号、疑问词、求推荐等关键词改写路由。
3. 进入 `GroupRuntime.process_message()`。
4. continue 时使用 TimingGate 返回的 `pending_text` 作为 chat pipeline 的 `<user_input>`；若测试桩或兼容路径没有返回 pending_text，则用当前消息格式化兜底。
5. 生成回复后写入 assistant ChatLog，并写入 user/assistant ConversationTurn 作为群聊工作记忆。

`core.context_builder.build_group_recent_context()` 从 ChatLog 取最近 ambient/assistant 消息，排除本轮 source_message_ids，防止同一条消息同时出现在 `<user_input>` 和 `<group_recent_context>`。

### 回复模型策略

新增配置：

- `LLM_MODEL_REPLY`：手动指定回复主链路模型，优先级最高。
- `REPLY_MODEL_INTEL_FLOOR`：自动路由时最低智能度，默认 12。
- `REPLY_MODEL_INTEL_BOOST`：在分类 complexity 推导出的下限上额外加成，默认 2。
- `REPLY_MODEL_MAX_COST`：回复模型单独预算，默认沿用 `LLM_BUDGET_CAP`。

当前 KT 中 planner 和 reply 工具还在同一次模型调用中，所以本次的“reply 模型”实际作用于 bridge 主生成链路。等以后实现真正二阶段 replyer 时，可以复用同一组配置。

## 风险与缓解

- 风险：测试里依赖旧 `[CurrentTimeContext]` 文案。缓解：测试改为检查 `<runtime_context>` 内的当前时间。
- 风险：手动指定的 `LLM_MODEL_REPLY` 不在 registry。缓解：允许作为单候选直接尝试，失败后由现有熔断/错误处理接管。
- 风险：群画像内容被模型复述。缓解：prompt 和上下文 header 都明确禁止复述结构标签和长期记忆。

## 验收

- prompt 构建输出包含新结构标签且 `prompt.md` 同步。
- `/chat` 传入 bridge 的 query 不再有 `[私聊] 当前用户输入` 前缀。
- 群聊 bridge metadata 包含 trigger/timing 信息。
- 群记忆上下文使用 `<group_memory_context>` 并包含 relationships。
- 群聊近期上下文使用 `<group_recent_context>`，不复述旧 `[用户名 (提问我)]` 包装。
- `/group/message` 普通 ambient 消息也会进入 TimingGate，不再被 L0 关键词机制短路。
- TimingGate continue 会携带 pending_text/source_message_ids，并由 chat pipeline 生成、落库回复。
- 模型路由在 reply 主链路使用更高 `intel_floor` 或手动 `LLM_MODEL_REPLY`。
- 相关 pytest 通过。
