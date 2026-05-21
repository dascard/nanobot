# Legacy prompt common base 设计

## 背景

`docs/goal.md` Phase 4 要求 `creatures/nanobot/prompt.md` 不再是群聊、私聊、工具说明和报告规则混在一起的巨型 prompt。真实链路已经由 bridge 按 chat_type 动态注入 `20_group_rules.md`、`25_context_control.md` 和 `26_private_behavior.md`，因此默认 legacy prompt 应该只保留 common base。

## 目标

- 默认 `prompt.md` 只由 common fragments 构建。
- common base 包含身份/自然口吻、reply/no_reply 输出契约、上下文权限、安全规则、当前输入优先级和最小工具契约。
- 群聊/私聊规则只在显式 `chat_type` 构建或 bridge 动态注入时出现。
- 工具路由、工具长说明、报告工具结束规则不进入默认 `prompt.md`。
- 旧 Prompt 页 `/prompt/build` 生成的 runtime prompt 也默认使用 common base，避免 runtime 链路继续污染真实请求。

## 构建规则

`scripts/build_nanobot_prompt.py`：

- `base`：`00_`、`05_`、`10_`、`30_` -> `prompt.md`
- `group`：base + `20_`、`25_` -> `prompt_group.md`
- `private`：base + `26_` -> `prompt_private.md`

`core.legacy_prompt_runtime.build_prompt_from_runtime()` 同步使用相同白名单，默认 `chat_type="base"`。

## 非目标

- 本阶段不删除旧 fragment 文件，WebUI 仍可查看和编辑它们。
- 本阶段不重写 PromptManager 模板。
- 本阶段不改变 bridge 的动态片段注入顺序。

## 验证

- Prompt contract 测试验证 `prompt.md` 精简且与构建输出一致。
- Legacy runtime 测试验证 runtime build 默认排除 group/private/tool routing fragment。
- PromptManager / Prompt trace / request sanitizer / final tools / KT / history 回归。
