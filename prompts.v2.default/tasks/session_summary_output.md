---
name: 会话滚动摘要输出合同
version: 2
kind: task
tool_name: session_summary
description: Session Summary JSON 字段、继承审计与输出预算合同。
---
请输出严格 JSON，业务字段严格为 summary、open_threads、decisions、important_user_requests、resolved_items、artifacts、participants、keywords、quality，并额外输出仅用于审计的 inheritance 数组。
每次都必须完整输出上述全部字段。quality 必须严格为 `"quality":{"score":0.9,"issues":[]}` 这一结构：score 是 0 到 1 的数字，issues 是字符串数组；没有问题时必须输出空数组，quality 不允许 reason 或其他字段。
inheritance 每项字段为 source_id、disposition、target_field、target_index；disposition 只允许 carried、updated、resolved。
carried 仅表示目标文本与 obligation.normalized_text 完全一致；改写、压缩、合并或改述都必须使用 updated；确认事项已完成才使用 resolved。
target_index 从 0 开始；合并多个 obligation 到同一目标时，每个 source_id 都必须单独写一项 updated，并允许共享同一个 target_field 和 target_index。
每个 available obligation 必须恰好处置一次，resolved 只能指向 resolved_items，legacy_summary 只能指向 summary；target 必须存在且非空。
summary 不超过 400 字；open_threads、decisions、important_user_requests、artifacts 四个可继承数组合计最多 7 项，每项不超过 60 字，优先合并同类事项并把必要背景压缩进 summary。
resolved_items、participants、keywords 也必须保持简洁。
整份紧凑 JSON 必须简洁并同时控制在 6000 字符、约 3000 tokens 以内，排版缩进和换行不计入预算；quality.score 必须是 0 到 1 的有限数字，只衡量摘要的忠实度、完整性以及角色和状态归因是否准确；不得因为源对话是闲聊、信息稀疏或缺少长期价值而降低分数。不要把 pending_fragments 当日志转写，不要保留 turn_id、时间戳、role 或 fragment 标签。
如果只能摘录，请改写为简洁要点。必须完整合并 previous_summary，不能只输出 pending_fragments 的摘要。
