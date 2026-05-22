# Reply 测试与评估 UI 收口计划

1. 增加后端回归测试。
   - dry-run 群聊回复不调用 `note_bot_replied`。
   - no_reply 已弹出 meta 后仍能被识别为 `no_reply_tool`。
   - ReplyEvalResult 返回 `agent_run_id` / `trace_id`。

2. 修后端逻辑。
   - bridge 收尾时 `meta.dry_run` 跳过 cooldown。
   - routes 用 `reply_meta` 判定 agent_result。
   - ReplyEvalResult 增加追溯字段和热迁移。

3. 重构 Reply 测试 UI。
   - 单条测试拆成 dry-run 与真实运行两个按钮。
   - 结果用指标卡、尝试列表、日志入口替代整块 JSON 优先展示。
   - 预览支持勾选、编辑、保存选中。
   - case 支持编辑和选中后评估。
   - 评估结果展示指标表和逐条结果表。

4. 调整通用后台视觉密度。
   - `MiniStat` 降低字号和高度。
   - 恢复可见焦点样式。
   - LLM 日志基础信息改为紧凑 key/value 网格。

5. 验证。
   - 跑相关 pytest。
   - 跑 WebUI build。
   - 检查 git 状态和 vendor 状态。
