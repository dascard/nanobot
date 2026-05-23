# Prompt Runtime V2 切换验收清单

## 背景

`prompt_runtime.engine` 默认仍保持 `v1`，这是保守上线策略。V2 已具备独立
`PromptPlan`、flow 编排、preview 和 bridge 接入能力，但切成默认引擎前必须
证明线上真实请求、管理端预览和评估链路都只消费同一份 V2 编译结果。

## 必须满足

- Live bridge 在 `prompt_runtime.engine=v2` 下只调用
  `core.prompt_v2.compiler.compile_prompt_plan()`。
- Live bridge 调用 V2 compiler 时启用 strict audit；审计失败必须 fail-fast
  或显式 fallback，不能把带结构性错误的 PromptPlan 发给模型。
- `/prompt/effective-preview` 在 `engine=v2` 下只调用 V2 preview/compiler，
  返回的 `request_json.messages` 与真实 `LLMApiRequestLog.request_json.messages`
  同构。
- `/reply-test/run` 的 `prompt_engine=v2` 只通过 bridge 的 V2 路径运行，
  不在 admin route 中拼接 messages。
- Reply Eval 的 `v2_prompt_only`、`v2_code_retry` 只通过 reply-test/bridge 的
  V2 路径运行，并记录 `prompt_sha256`、`prompt_mode=v2`、`prompt_source`。
- V2 请求中 `current_user_event`、`runtime_tool_prompt`、`persona_reference`
  均只出现一次。
- 群聊 V2 不混入私聊分支，私聊 V2 不混入群聊分支。
- V2 工具说明和 tools schema description 均来自 `tools/<tool>/usage`。
- group_analysis、news_search、ai_daily、image_summary 内部二次 LLM prompt
  均来自 `prompts.v2.default/tools/<tool>/...` 或 `data/prompts_v2/tools/<tool>/...`。
- AgentRun、PromptRenderLog、LLMApiRequestLog 均可追踪同一轮的 prompt_sha256。

## V1 收口边界

- `core.prompt_assembler.PromptAssembler` 只作为 v1 回滚、迁移对比和旧测试入口保留。
- `core.legacy_prompt_runtime` 只作为 v1 legacy prompt.md 回滚入口保留。
- `/prompts` 和 `/prompt/fragments` 只能标记为 v1/legacy 页面，不再作为 V2 主编辑入口。
- 新增模板和工具 prompt 只写入 `prompts.v2.default/` 与 `data/prompts_v2/`。

## 推荐切换步骤

1. 在测试环境设置 `prompt_runtime.engine=v2`，跑 reply-test 的 group/private 干跑。
2. 跑 Reply Eval 三组 variant：`v1_baseline`、`v2_prompt_only`、`v2_code_retry`。
3. 对比关键指标：`reply_call_rate`、`expected_action_accuracy`、
   `no_tool_call_rate`、`fake_tool_claim_rate`、`empty_output_rate`。
4. 抽样检查真实 `LLMApiRequestLog.request_json.messages`，确认和
   `/prompt/effective-preview?engine=v2` 同构。
5. 打开生产 canary，只对指定群或测试用户通过 metadata override 使用 v2。
6. canary 通过后再把 `prompt_runtime.engine` 默认值从 `v1` 切到 `v2`。

## 暂不切默认值

在上述验收完成前，`prompt_runtime.engine` 的默认值继续保持 `v1`。
这样 V2 可以继续通过 preview、reply-test、Reply Eval 和 metadata override
验证，同时保留紧急回滚入口。
