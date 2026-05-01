# Group Analysis 对齐外部实现计划

1. 读取本地 `group_analysis` 与外部项目关键实现，整理剩余平台无关差异。
2. 先补测试：
   - 群统计新增 `emoji_count`
   - 活跃度分布和聊天质量区块
   - QQbot 默认不自动撤回，显式临时消息才撤回
3. 实现：
   - 增加第四路聊天质量分析
   - 报告补活跃度分布、表情统计、质量锐评
   - QQbot 消息发送拆成“常规消息/临时消息”
4. 分别运行 nanobot-server 与 QQbot 相关 `pytest` 验证。
