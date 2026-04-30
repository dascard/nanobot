# News Search 富文本输出实现计划

1. 阅读 `news_search` 当前实现与 QQbot 渲染逻辑，确认接线点。
2. 先补测试：
   - `news_search` 输出为富 Markdown
   - 输出保留标题、链接、摘要
   - 输出可命中 QQbot 复杂 Markdown 检测
3. 实现新的报告格式化函数，替换当前日志式文本拼接。
4. 视需要补强 QQbot 的复杂 Markdown 检测。
5. 更新 `prompt.md`，要求模型尽量保留 `news_search` 的结构化报告。
6. 跑相关 pytest，并做一次样例输出检查。
