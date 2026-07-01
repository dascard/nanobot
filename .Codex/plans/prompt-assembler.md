# PromptAssembler 实现计划

> 面向 AI 代理的工作者：按小步 TDD 执行，每步先写或更新测试，再实现最小代码。

**目标：** 让 PromptManager / PromptAssembler 成为真实请求和 Web 预览的唯一提示词编排入口。

**架构：** 新增 `core/prompt_assembler.py` 承接当前 `PromptCompiler` 职责；`core/prompt_compiler.py` 保留兼容导出。bridge、admin preview 和 Reply Eval 只依赖 assembler 产物。

**技术栈：** Python、FastAPI、SQLAlchemy、pytest、PromptManager Markdown 模板。

---

## 文件结构

- 创建：`core/prompt_assembler.py`
- 修改：`core/prompt_compiler.py`
- 修改：`nanobot_kt/bridge.py`
- 修改：`api/admin_routes.py`
- 修改：`core/context_builder.py`
- 修改：`core/legacy_prompt_runtime.py`
- 修改：`core/database.py`
- 修改：`prompts.default/group_chat.md`
- 修改：`prompts.default/private_chat.md`
- 创建：`scripts/migrate_legacy_fragments_to_managed.py`
- 修改/新增测试：`tests/test_prompt_assembler.py`、`tests/test_prompt_manager.py`、`tests/test_prompt_trace_admin.py`、`tests/test_reply_admin.py`

## 任务 1：新增 PromptAssembler 红灯测试

- [ ] 添加测试：managed messages 中 `user_input`、`persona_reference`、`runtime_tool_prompt`、`conversation_context` 各只出现一次。
- [ ] 添加测试：shadow 模式 active messages 使用 legacy，managed 仅用于 diff/render。
- [ ] 添加测试：managed group/private 默认模板规则隔离。
- [ ] 运行 `python -m pytest tests/test_prompt_assembler.py -v`，确认新增行为在实现前失败。

## 任务 2：实现 PromptAssembler

- [ ] 创建 `PromptBuildContext`、`PromptBuildResult`、`PromptAssembler`。
- [ ] 实现 managed/legacy/shadow 三种模式。
- [ ] 复用 PromptManager render，记录 prompt source/path/sha/warnings。
- [ ] 为 `request_json`、`diff`、`tool_schemas` 提供稳定输出。
- [ ] 让 `core/prompt_compiler.py` 兼容导出新类。
- [ ] 运行 prompt assembler 单测。

## 任务 3：收敛 bridge 与 preview

- [ ] bridge 删除真实运行中的旧 group/private fragment 手工注入，改用 `PromptAssembler.build()`。
- [ ] bridge 依据 settings 或 metadata override 决定 `legacy` / `shadow` / `managed`。
- [ ] `/prompt/effective-preview` 改用同一 `PromptAssembler.build()`，返回 legacy vs managed diff、warnings、最终 messages、request_json、tool_schemas。
- [ ] 运行相关 admin/bridge 单测。

## 任务 4：群聊上下文与 legacy 标记

- [ ] 确认 bridge 和 preview 只调用 `build_chat_context()`。
- [ ] 将 `build_group_recent_context()` 标记 deprecated，不再用于真实链路。
- [ ] 将 legacy fragment builder 文档和返回 metadata 标记 deprecated。
- [ ] 添加迁移脚本生成 managed 模板候选内容。

## 任务 5：默认 managed 模板合并

- [ ] 更新 `prompts.default/group_chat.md`：合并通用规则、工具纪律、群聊规则。
- [ ] 更新 `prompts.default/private_chat.md`：合并通用规则、工具纪律、私聊规则。
- [ ] 确认 group 不含私聊章节，private 不含群聊章节。
- [ ] 运行 prompt contract 测试。

## 任务 6：Reply Eval 实验切换与追溯

- [ ] `baseline` 使用 legacy 且 retry=false。
- [ ] `prompt_only` 使用 managed 且 retry=false。
- [ ] `code_retry` 使用 legacy 且 retry=true。
- [ ] `ReplyEvalResult` 增加 `prompt_sha256`，热迁移补列。
- [ ] 结果写入 `prompt_sha256`、`trace_id`、`agent_run_id`。
- [ ] 运行 reply admin 测试。

## 任务 7：完整验证

- [ ] 运行 `python -m pytest tests/ -v`。
- [ ] 核对硬性验收项：预览与真实 messages 一致、managed 无重复、模板规则隔离、reply/no_reply 契约存在、PromptRenderLog 有来源字段、三种 eval 变体 prompt_sha/retry 符合预期。
