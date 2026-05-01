# News Search 富文本输出实现计划

1. 阅读 `news_search` 当前实现与 QQbot 渲染逻辑，确认 HTML 接线点。
2. 先补测试：
   - 结构化 JSON 解析成功
   - 结构化字段能灌进固定 HTML 模板
   - 输出仍保留标题、链接、摘要
3. 新增小 schema 的摘要层：
   - `title`
   - `subtitle`
   - `summary`
   - `highlights`
   - `alerts`
   - `closing`
4. 保留固定 HTML 模板，把顶部摘要区改为由 schema 驱动。
5. 保留 QQbot HTML 渲染分流，不再要求模型直接写整页 HTML。
6. 跑 `news_search + bridge` 相关 pytest 回归。
