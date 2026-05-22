# Reply 测试与评估 UI 收口

## 背景

Reply 合约链路已经具备 dry-run、重试、评估集和日志展示能力，但验收反馈指出：

- dry-run 可能写入群聊运行时 cooldown 状态。
- 群聊 no_reply 调试日志可能被误归类为 no_tool_call。
- Reply 测试页真实运行入口和 dry-run 入口没有明显区分。
- 评估集预览、编辑、结果展示以原始 JSON 为主，信息层级弱。
- 管理后台部分统计卡字号偏大，不符合运维工具的高密度阅读场景。

## 目标

- dry-run 不修改真实群聊运行时状态。
- no_reply/no_tool_call 统计口径稳定。
- Reply 测试页采用数据密集型后台布局，入口、风险和结果有明确区分。
- 测试集支持预览勾选、编辑、保存选中；已有 case 支持编辑。
- 评估结果展示指标表和逐条结果，并保留 AgentRun/trace 追溯字段。

## 设计约束

- 不引入新的前端组件库。
- 保持单文件 WebUI 结构，局部新增小型展示组件。
- 不改 `vendor/KohakuTerrarium`。
- 真实运行入口必须独立按钮，并使用浏览器确认框二次确认。

## 验收

- `tests/test_reply_admin.py` 覆盖评估结果 trace/run_id。
- `tests/test_kt_framework.py` 覆盖 dry-run 不触发 note_bot_replied。
- 前端构建通过。
- 重点页面在 1365px 宽度下不出现明显大字卡片堆叠。
