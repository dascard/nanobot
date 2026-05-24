# 群体记忆提取触发设计

## 目标

群体记忆不能只依赖模型在对话中偶然调用 `group_analysis`。后台管理端需要能看到哪些群有聊天语料、哪些群已生成记忆，并能手动触发一次记忆提取。

## 现状

`group_memories` 只由 `group_analysis` 工具的日报流程写入。真实数据库中只有少数群有记忆，且当前注入门槛要求非事件类至少两次证据、高置信度 0.7，导致一次分析产出的 topic/style/relationship 大多无法注入 GroupProfile。

WebUI 的群体记忆页只能输入 `group_id` 查询，没有群列表、覆盖情况、提取入口。

## 方案

新增一个可复用的群记忆提取服务，复用 `GroupAnalysisRepository`、preprocess、analyzer 和 `memory_candidates.extract_and_persist`，不在 Admin route 中重写分析逻辑。`group_analysis` 工具后续也可以迁移到该服务，但本次只保证 Web 触发和接口可用。

新增 Admin API：

- `GET /api/v1/admin/group-memories/overview`：返回所有有群聊日志或已有记忆的群，包含日志数、最新日志、记忆数、active 数、可注入数。
- `POST /api/v1/admin/groups/{group_id}/memories/extract`：同步触发一次提取，参数包含 `window_hours` 和 `instructions`，返回预处理计数和写入统计。

调整注入口径：active、证据存在、decay 未过期、confidence 不低于 `CONFIDENCE_FLOOR` 即允许注入。这样一次群分析产生的稳定 topic/style/relationship 不会全部沉在库里。

WebUI 群体记忆页改为主从视图：左侧群概览，右侧记忆列表和提取操作。保留手动输入 group_id 的能力。

## 边界

本次不做自动定时全量抽取，不把群体记忆服务改成后台 worker，也不重新设计记忆类型体系。`slang/preference` 的抽取质量以后单独做。

## 验收

1. 有群聊日志但无记忆的群会出现在 Web 概览中。
2. Web 端能对选中群触发提取并看到写入统计。
3. 后端提取接口不直接拼 prompt，复用 group_analysis 的现有分析管线。
4. 一次分析写入的 active topic 具备证据后可被 `query_injectable()` 返回。
5. 新增接口和前端入口有测试约束。
