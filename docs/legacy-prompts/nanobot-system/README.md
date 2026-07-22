# Nanobot 旧 System Fragment 归档

本目录只保存 2026-07-21 前 KT creature 使用过的分片，供历史审计和语义对照。
这些文件不是运行时配置，生产代码不得读取或拼接它们。

当前唯一主聊天 Prompt 入口是 canonical Prompt Runtime：

- 拓扑：`prompts.v2.default/chat/flow.json`
- 代码侧权限、信任级别和依赖：`core/prompt_v2/section_descriptors.py`
- 默认模板：`prompts.v2.default/chat/`
- 运行时覆盖：`data/prompts_v2/chat/`

工具说明由 `ToolDescriptor.prompt_template_keys` 绑定到
`prompts.v2.default/tools/*`；后台模型任务由 `TaskContract` 与
`TaskInvocationSpec` 绑定到 `prompts.v2.default/tasks/*`。不要从本目录恢复新的
运行时 Prompt 分支；需要恢复语义时，应迁移到对应 canonical 模板并补合同测试。
