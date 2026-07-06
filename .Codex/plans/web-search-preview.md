# Web Search 预览测试实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在 WebUI 的“搜索 API”页面支持输入测试查询，展示真实搜索结果、bot 工具发送给模型的消息，以及每种搜索 API 的调用次数。

**架构：** 后端新增 admin preview endpoint，复用 `search_enabled_providers()` 执行搜索。模型消息格式化函数放入 `core.web_search.search_runtime`，`WebSearchTool` 和 admin preview 共同调用，避免两套输出格式。新增 `web_search_provider_usage` 聚合表记录 provider 调用次数，前端在现有 provider 列表上方新增测试面板，并在卡片中展示调用统计。

**技术栈：** FastAPI、Pydantic、SQLAlchemy、pytest、React/Vite、Tailwind。

---

## 文件结构

- 修改：`core/web_search/search_runtime.py`
  增加 `format_provider_result_for_model()`，并在搜索路径记录 provider usage。
- 修改：`core/database.py`
  增加 `WebSearchProviderUsage` ORM 模型。
- 修改：`core/schema_migrations.py`
  增加 `web_search_provider_usage` 表迁移。
- 创建：`core/web_search/usage_stats.py`
  聚合调用统计读写函数。
- 修改：`creatures/nanobot/prompts/skills/web_search/tool.py`
  改用共享格式化函数。
- 修改：`api/admin/web_search_routes.py`
  增加 `ProviderPreviewRequest` 和 `/preview` endpoint。
- 修改：`webui/src/features/web-search/api.js`
  增加 `previewWebSearch()`。
- 修改：`webui/src/features/web-search/WebSearchPage.jsx`
  增加搜索预览表单、结果列表和模型消息展示。
- 修改：`tests/test_admin_web_search_routes.py`
  增加 preview 成功、参数透传、失败和调用统计测试。
- 修改：`tests/test_schema_migrations.py`
  增加 usage 表迁移断言。
- 修改：`tests/test_web_search_tool.py`
  增加工具输出来自共享模型消息格式的断言。
- 修改：`tests/test_webui_app_split.py`
  增加前端预览面板结构断言。

## 任务 1：后端失败测试

- [ ] 在 `tests/test_admin_web_search_routes.py` 中增加 preview endpoint 成功测试，monkeypatch `api.admin.web_search_routes.search_enabled_providers` 返回一条 `WebSearchResult`。
- [ ] 增加 provider/limit 透传测试，断言 fake runtime 收到 `provider_id="searxng"` 和 `limit=3`。
- [ ] 增加失败测试，fake runtime 抛 `WebSearchError("no_enabled_provider", "...")`，断言响应 `ok:false`。
- [ ] 运行目标测试确认因为 endpoint 不存在或字段缺失而失败。

## 任务 2：共享模型消息格式

- [ ] 在 `core/web_search/search_runtime.py` 中实现 `format_provider_result_for_model(query, result, limit=5)`。
- [ ] 修改 `WebSearchTool` 使用该函数，并保持 `structured_content` 不变。
- [ ] 运行 `tests/test_web_search_tool.py` 通过。

## 任务 3：Provider 调用统计

- [ ] 在 `core/database.py` 中新增 `WebSearchProviderUsage`。
- [ ] 在 `core/schema_migrations.py` 中新增 `20260706_web_search_provider_usage` 迁移。
- [ ] 创建 `core/web_search/usage_stats.py`，实现 `get_provider_usage()` 和 `record_provider_usage()`。
- [ ] 在 `search_enabled_providers()` 和 `provider_tests.test_provider()` 中记录成功/失败调用。
- [ ] 运行 admin 路由测试和 schema migration 测试通过。

## 任务 4：Admin preview endpoint

- [ ] 在 `api/admin/web_search_routes.py` 中新增请求模型和 `/preview` endpoint。
- [ ] 成功时返回 `ok/provider_id/elapsed_ms/results/message`。
- [ ] 捕获 `WebSearchError` 返回 `ok:false/error_code/provider_id/message`。
- [ ] Provider 列表返回 `usage` 聚合字段。
- [ ] 运行 `tests/test_admin_web_search_routes.py` 通过。

## 任务 5：WebUI 预览面板

- [ ] 在 `webui/src/features/web-search/api.js` 中新增 `previewWebSearch(payload)`。
- [ ] 在 `WebSearchPage.jsx` 增加 query/provider/limit 状态和提交逻辑。
- [ ] 展示结果列表与“发送给模型的消息”文本块。
- [ ] 在 provider 卡片展示调用次数、成功次数、失败次数和最近错误。
- [ ] 运行 `tests/test_webui_app_split.py` 和 `cd webui && npm run build`。

## 验收

- Web 端可以输入 query、limit、provider 并发起搜索。
- 成功时页面展示标题、URL、摘要和 provider 信息。
- 页面展示与 bot 工具一致的模型消息文本。
- 每个 provider 卡片展示调用次数、成功次数、失败次数。
- 没有启用 provider 或 provider 失败时页面展示错误，不出现 500。
- 相关 pytest、WebUI build、`git diff --check` 通过。
