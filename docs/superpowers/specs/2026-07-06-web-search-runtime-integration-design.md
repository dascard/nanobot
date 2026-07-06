# Web Search Provider 全量测试与 Bot 接入设计

## 背景

现有 `docs/superpowers/specs/2026-07-04-web-search-provider-config-design.md`
只覆盖方案 A：管理后台配置 provider、保存 API Key/Base URL、对部分 provider 做连接测试。
这导致两个问题：

- 配置页里 `exa`、`firecrawl`、`linkup`、`you`、`jina` 显示“暂不测试”，体验像半成品。
- bot 运行时没有 `web_search` 工具，KT creature 也没有加载对应工具；管理员配置的 provider 不会被 Agent 消费。

本设计把 Web Search 推进到一个可用闭环：所有 catalog provider 都可测试，且 bot 能通过一个统一
`web_search` 工具调用已启用 provider。

## 目标

- 所有 catalog provider 都实现 smoke test，不再返回 `not_implemented`。
- 提供统一搜索运行时，admin 测试接口和 bot 工具共用 provider adapter。
- 新增 `web_search` KT 工具，暴露给 ToolPlan、Tools WebUI、Prompt V2 工具模板和 creature config。
- 搜索结果统一为结构化结果：`provider`、`title`、`url`、`snippet`、`published_at`、`score`、`raw`。
- 运行时按 catalog 顺序对已启用 provider 做 fallback；一个 provider 成功返回结果即停止。
- 密钥继续复用 `web_search.providers.<id>.api_key`，不回显、不写日志、不进错误响应。

## 非目标

- 不实现多 provider 并发聚合、成本权重、健康度趋势或预算管理。
- 不实现 deep research、rerank、正文抓取链路和 citation 验证。
- 不替换 `ai_daily` 的新闻日报 pipeline；`web_search` 是通用网页搜索工具。
- 不改 vendor/KohakuTerrarium 内置 `web_search`，而是在项目侧提供 `nanobot_kt.tools.web_search`。

## Provider 契约

| Provider | Endpoint | Auth | 响应判定 |
| --- | --- | --- | --- |
| `searxng` | `GET {base}/search?q=&format=json` | 无 | 顶层 `results` 为数组 |
| `serper` | `POST {base}/search` | `X-API-KEY` | 顶层 `organic` 为数组 |
| `brave` | `GET {base}/web/search?q=&count=` | `X-Subscription-Token` | `web.results` 为数组 |
| `tavily` | `POST {base}/search` | `Authorization: Bearer` | 顶层 `results` 为数组 |
| `ddgs` | 本地 `DDGS().text()` | 无 | 返回 list |
| `exa` | `POST {base}/search` | `x-api-key` | 顶层 `results` 为数组 |
| `firecrawl` | `POST {base}/v2/search` | `Authorization: Bearer` | `data.web` 或 `data.news` 为数组 |
| `linkup` | `POST {base}/v1/search` | `Authorization: Bearer` | 顶层 `results` 为数组 |
| `you` | `GET {base}/v1/search?query=&count=` | `X-API-Key` | `results.web`/`results.news` 为数组 |
| `jina` | `GET {base}/?q={encoded query}` | `Authorization: Bearer` | JSON 模式返回 list 或含 `data` 的 list |

默认 Base URL 以官方文档为准：

- `firecrawl`: `https://api.firecrawl.dev`
- `linkup`: `https://api.linkup.so`
- `you`: `https://ydc-index.io`
- `jina`: `https://s.jina.ai`

## 运行时架构

新增 `core/web_search/search_runtime.py`：

- 定义 `WebSearchResult`、`WebSearchProviderResult`、`WebSearchError`。
- 实现每个 provider 的 `_search_<provider>()` adapter。
- 提供 `search_provider(config, query, limit)`，用于 admin smoke test。
- 提供 `search_enabled_providers(db, query, limit, provider_id="")`，用于 bot 工具 fallback。
- 统一脱敏错误，复用 `provider_tests` 的错误码语义。

`core/web_search/provider_tests.py` 改为薄封装：

- 检查 provider 是否存在、API Key 是否缺失。
- 调 `search_provider(..., limit=3)`。
- 成功时返回 `sample_count=len(results)`；失败时把 `WebSearchError` 转为 `ProviderTestResult`。

## Bot 工具接入

新增文件：

- `creatures/nanobot/prompts/skills/web_search/tool.py`
- `nanobot_kt/tools/web_search.py`
- `prompts.v2.default/tools/web_search/usage.md`
- `data/prompts_v2/tools/web_search/usage.md`

修改：

- `creatures/nanobot/config.yaml` 加载 `web_search`。
- `core/tool_registry.py` 增加 `web_search` 元数据，私聊/群聊默认开启，风险等级 low。
- `core/tool_schema_preview.py` 增加静态 schema。
- `core/prompt_v2/template_registry.py` 增加 legacy alias。

工具参数：

- `query`：必填搜索词。
- `limit`：默认 5，最大 10。
- `provider`：可选，指定 provider id；不传则按已启用 provider fallback。

工具输出：

- 成功：文本列出命中的标题、URL、摘要；metadata 写入完整结构化结果。
- 无启用 provider：返回错误，提示去管理后台“搜索 API”启用 provider。
- 指定 provider 不可用或无结果：返回明确错误，不编造结果。

## 前端调整

- 全部 provider badge 显示“可测试”。
- 删除 `not_implemented` 对应文案。
- 统计 `可测试` 应等于 provider 总数。

## 测试策略

- `tests/test_admin_web_search_routes.py`
  - 所有 provider `testable=True`。
  - `exa/firecrawl/linkup/you/jina` smoke test 用 fake aiohttp 返回结构化响应。
  - 无 `not_implemented`。
- `tests/test_web_search_tool.py`
  - ToolPlan 默认暴露 `web_search`。
  - KT tool 调用统一运行时，返回结构化 metadata。
  - 无启用 provider 时给出错误。
- `tests/test_tool_schema_config.py`
  - `web_search` schema 可构建，`news_search` 仍不暴露。
- `tests/test_prompt_v2_tool_template_integration.py`
  - Prompt V2 能找到 `web_search` usage 模板。

## 验证

至少运行：

- `python -m pytest tests/test_admin_web_search_routes.py tests/test_web_search_tool.py tests/test_tool_schema_config.py -v`
- `python -m pytest tests/test_webui_app_split.py tests/test_prompt_v2_tool_template_integration.py -v`
- `cd webui && npm run build`
- `git diff --check`

全量提交前运行：

- `python -m pytest tests/ -v`
