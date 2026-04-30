# News Search 富文本输出实现计划

1. 阅读 `news_search` 当前实现与 QQbot 渲染逻辑，确认 HTML 接线点。
2. 先补测试：
   - `news_search` 输出为 HTML 卡片
   - 输出保留标题、链接、摘要
   - QQbot 仍能识别这类输出为富文本
3. 实现 HTML 模板渲染函数，替换当前 Markdown 拼接。
4. QQbot 增加 HTML/Markdown 渲染分流。
5. 更新 `prompt.md`，要求模型保留 `news_search` 的 HTML 报告。
6. 跑相关 pytest，并做一次文件级校验。
