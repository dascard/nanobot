# Sandbox Agent 兼容性 Eval

本目录只定义任务和阈值，不代表任务已经运行。生产验收必须提供由真实 Agent
执行链路导出的事件 artifact，再运行：

```bash
python -m evals.sandbox_agent_compatibility \
  --artifacts /var/cache/nanobot/sandbox-agent-eval/<run-id>/ \
  --output /var/cache/nanobot/sandbox-agent-eval/<run-id>/report.json
```

没有 artifact、缺少任一 case、artifact 重复或 schema 无效时，评估器返回非
0。合成 artifact 只能用于评估器单元测试，不能作为生产模型体验验收证据。

事件按 `seq` 从 1 连续编号。每个事件固定包含 `type`、`tool`、`valid`、
`reason`、`category` 和 `effective`。首次有效测试是 Workspace 就绪后第一条
`type=tool_result`、`category=test` 且 `effective=true` 的事件。

评估器汇总以下指标：

- 任务成功率；
- 无效工具调用次数；
- 环境误解重试次数；
- 从 Workspace 就绪到首次有效测试的工具调用数；
- 长任务恢复成功率；
- Lease 重建后继续工作成功率；
- 安全策略违规尝试数。

artifact 是执行事实，不应由被评估 Agent 自行改写。采集端应保存原始
request ID、工具名和结果分类，但不得写入 Secret、完整命令输出或宿主真实
路径。
