# news_search 搜索后端拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」仍包含
`creatures/nanobot/prompts/skills/news_search/tool.py`。该文件已经完成两刀拆分：

- 旧版报告、评分、价值信号和 layout fallback 已迁到 `legacy_report.py`。
- 运行时缓存、日期解析、日报查询识别和缓存 key 已迁到 `runtime_cache.py`。

当前 `tool.py` 仍有 1110 行，剩余职责包括代理感知 HTTP、RSS / Juya / DDG 搜索、
`WebTools`、V2 evidence bridge、旧 `search_and_extract_news()`、LLM 版式摘要和
`AiDailyTool`。其中搜索后端约覆盖 `_urlopen()`、RSS/Juya 抓取、query variants、
DDG 检索、trafilatura 正文提取和 `WebTools.search()` / `extract_web_content()`。
这块职责边界完整，外部主要契约是：

- `WebTools.search(query, max_results=5, deep=False) -> list[dict]`
- `WebTools.extract_web_content(url) -> str`
- `WebTools.last_error`
- 旧路径 monkeypatch 入口：`news_search.tool._fetch_multi_rss`、
  `news_search.tool._fetch_juya_rss`、`news_search.tool._urlopen`、
  `news_search.tool.DDGS`、`news_search.tool.trafilatura` 和
  `news_search.tool.NEWS_SEARCH_DDG_ENABLED`

## 目标

本阶段做无行为变化的搜索后端拆分：

1. 新增 `creatures/nanobot/prompts/skills/news_search/search_backend.py`。
2. 将代理感知 HTTP、RSS/Juya 抓取、query 判定、query variants、DDG 检索、
   stale 过滤、domain diversity rerank 和 trafilatura 正文提取迁移到新模块。
3. `tool.py` 保留旧符号和旧 patch 路径，通过薄 facade 调用 `search_backend.py`。
4. `WebTools.search()` 继续读取 `tool.NEWS_SEARCH_DDG_ENABLED`、`tool.DDGS`、
   `tool._fetch_multi_rss` 和 `tool._fetch_juya_rss`，保证现有测试 monkeypatch
   旧路径仍然生效。
5. `tool._fetch_juya_rss()` 和 `tool._fetch_multi_rss()` 继续使用 `tool._urlopen`，
   保证 `patch("...tool._urlopen")` 仍能截获 RSS 请求。
6. `tool.WebTools.extract_web_content()` 继续通过 `tool.trafilatura` 调用，保证
   `patch("...tool.trafilatura.fetch_url")` 仍能截获正文提取。
7. 保持 `search_and_extract_news()`、`search_and_extract_news_v2()` 和 `AiDailyTool`
   的签名、返回契约和缓存行为不变。
8. 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。

## 非目标

本阶段不做以下事情：

- 不迁移 `AiDailyTool`、`_build_ai_daily_tool_result()`、`_render_ai_daily_fallback()`。
- 不迁移 `_summarize_news_layout()`、`_model_should_deepen()`、
  `_call_llm_simple()` 或 `search_and_extract_news()`。
- 不迁移 `_run_news_daily_pipeline()`、`search_and_extract_news_v2()` 或
  `news_daily/` 子流水线。
- 不改变 DDG 默认开关 `NEWS_SEARCH_DDG_ENABLED=0`。
- 不改变 RSS source 列表、Juya RSS URL、stale 过滤时间窗、query variants 或
  domain diversity 排序语义。
- 不改变 `WebTools.last_error` 的写入规则：只有搜索结果为空且存在后端错误时才写入。
- 不改 prompt runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime 输入。
- 不引入新的外部依赖、DB schema 或落盘缓存。

## 方案比较

### 方案 A：拆出 `search_backend.py`，`tool.py` 保留 facade

将 RSS/Juya/DDG/trafilatura 真实实现迁移到新模块，`tool.py` 保留同名函数和
`WebTools` 薄 wrapper。wrapper 将旧路径可 monkeypatch 的对象作为依赖传给新模块。

优点：

- 行数收益最大，能直接推进 `tool.py` 向 800 行以下收敛。
- 搜索后端的输入输出边界清楚，核心契约集中在 `search()` 和 `extract_web_content()`。
- 旧测试仍可 patch `news_search.tool` 下的符号，不需要同步改大量测试。
- 后续可以继续把 V2 evidence bridge 或 AI 日报适配层拆出。

缺点：

- 新模块仍会导入 `duckduckgo_search` 和 `trafilatura`，不是轻量模块。
- facade 必须显式透传依赖，否则旧路径 monkeypatch 会失效。

### 方案 B：只拆 AI 日报适配层

将 `AiDailyTool`、reply 包装、fallback HTML 和 ingest metadata 迁移到新模块。

优点：

- 触碰网络搜索后端较少。
- 工具适配层职责更清晰。

缺点：

- 行数收益较小，`tool.py` 仍明显超过 800 行。
- `AiDailyTool._execute()` 直接耦合旧路径 `_run_news_daily_pipeline`、缓存 helper、
  `asyncio.to_thread` 和 reply 包装，旧路径 patch 兼容同样需要额外 facade。

### 方案 C：直接迁移 `search_and_extract_news*()`

把旧搜索入口和 V2 evidence bridge 一并迁出。

优点：

- `tool.py` 会快速变成纯工具 facade。

缺点：

- 同时移动 LLM 摘要、legacy HTML、V2 evidence、fallback 和 AI 日报运行路径，
  单阶段影响面过大。
- 更容易混入行为变化，不适合作为 P3 文件拆分的小步提交。

推荐采用方案 A。

## 模块边界

### `search_backend.py`

职责：

- 代理感知 `_urlopen()` 和 `_ddgs_kwargs()`。
- `NEWS_SEARCH_DDG_ENABLED` 默认值。
- `JUYA_RSS_URL`、`RSS_KEYWORDS`、`RSS_SOURCES`。
- `_is_rss_first_query()`、`_should_use_juya_direct()`、`_is_news_query()`、
  `_infer_timelimit()`、`_is_urgent_news_query()`、`_tokenize_query()`。
- `_extract_item_date()`、`_is_recent_enough()`、`_normalize_search_result()`、
  `_filter_stale_news_results()`、`_match_query()`。
- `_fetch_rss_source()`、`_fetch_multi_rss()`、`_fetch_juya_rss()`。
- `_build_query_variants()`、`_rerank_with_domain_diversity()`。
- `search()`：执行 RSS/Juya/DDG 聚合搜索，返回 `(results, last_error)`。
- `extract_web_content()`：执行 trafilatura 下载与抽取。

依赖：

- 标准库：`logging`、`os`、`re`、`datetime`、`timezone`、`timedelta`、
  `urllib.request`、`xml.etree.ElementTree`、`email.utils`。
- 运行时依赖：`duckduckgo_search.DDGS`、`trafilatura`。
- 项目内轻量依赖：`runtime_cache._extract_date`、`runtime_cache._is_daily_digest_query`、
  `legacy_report._combined_score`、`legacy_report._domain`。

建议接口：

```python
def search(
    query: str,
    max_results: int = 5,
    deep: bool = False,
    *,
    ddg_enabled: bool | None = None,
    ddgs_factory: Any = DDGS,
    ddgs_kwargs_fn: Callable[[], dict[str, Any]] = _ddgs_kwargs,
    multi_rss_fetcher: Callable[..., list[dict[str, Any]]] = _fetch_multi_rss,
    juya_fetcher: Callable[..., list[dict[str, Any]]] = _fetch_juya_rss,
) -> tuple[list[dict[str, Any]], str]:
    ...
```

`ddg_enabled=None` 时读取 `search_backend.NEWS_SEARCH_DDG_ENABLED`。`tool.py`
wrapper 会显式传入 `tool.NEWS_SEARCH_DDG_ENABLED`，以保留旧路径 monkeypatch 语义。

### `tool.py`

职责：

- 从 `search_backend.py` 绑定 `DDGS`、`trafilatura`、`RSS_KEYWORDS`、
  `RSS_SOURCES`、`JUYA_RSS_URL` 和 `NEWS_SEARCH_DDG_ENABLED` 旧符号。
- 保留 `_urlopen()`、`_ddgs_kwargs()`、`_fetch_multi_rss()` 和 `_fetch_juya_rss()`
  wrapper，供旧路径 patch。
- 保留 `WebTools` 类，但类方法只调用 `search_backend.search()` 和
  `search_backend.extract_web_content()`。
- 保留 `search_and_extract_news()`、`search_and_extract_news_v2()`、
  `_run_news_daily_pipeline()`、`AiDailyTool` 和缓存 facade。

建议 facade：

```python
from . import search_backend as _search_backend

DDGS = _search_backend.DDGS
trafilatura = _search_backend.trafilatura
NEWS_SEARCH_DDG_ENABLED = _search_backend.NEWS_SEARCH_DDG_ENABLED

def _urlopen(url, timeout=10):
    return _search_backend._urlopen(url, timeout=timeout)

def _fetch_multi_rss(query: str, max_results: int) -> list[dict[str, Any]]:
    return _search_backend._fetch_multi_rss(
        query=query,
        max_results=max_results,
        urlopen_fn=_urlopen,
    )

class WebTools:
    last_error: str = ""

    @staticmethod
    def search(query: str, max_results: int = 5, deep: bool = False) -> list[dict[str, Any]]:
        results, last_error = _search_backend.search(
            query,
            max_results=max_results,
            deep=deep,
            ddg_enabled=NEWS_SEARCH_DDG_ENABLED,
            ddgs_factory=DDGS,
            ddgs_kwargs_fn=_ddgs_kwargs,
            multi_rss_fetcher=_fetch_multi_rss,
            juya_fetcher=_fetch_juya_rss,
        )
        WebTools.last_error = last_error
        return results
```

## 兼容契约

旧路径必须继续可用：

- `from creatures.nanobot.prompts.skills.news_search.tool import WebTools`
- `from creatures.nanobot.prompts.skills.news_search.tool import search_and_extract_news`
- `from creatures.nanobot.prompts.skills.news_search import tool as news_tool`

旧路径 monkeypatch 必须继续影响运行：

- `monkeypatch.setattr(news_tool, "NEWS_SEARCH_DDG_ENABLED", True)`
- `monkeypatch.setattr(news_tool, "_fetch_multi_rss", ...)`
- `monkeypatch.setattr(news_tool, "_fetch_juya_rss", ...)`
- `patch("creatures.nanobot.prompts.skills.news_search.tool._urlopen", ...)`
- `patch("creatures.nanobot.prompts.skills.news_search.tool.DDGS", ...)`
- `patch("creatures.nanobot.prompts.skills.news_search.tool.trafilatura.fetch_url", ...)`
- `patch("creatures.nanobot.prompts.skills.news_search.tool.WebTools.search", ...)`

新模块路径也可以直接测试：

- `from creatures.nanobot.prompts.skills.news_search import search_backend`
- `search_backend.search(..., ddg_enabled=False)` 只返回 RSS/Juya 结果。
- `search_backend.extract_web_content(url, trafilatura_module=fake_module)` 可独立验证正文提取。

## TDD 策略

先补 characterization tests，再迁移代码：

1. 新增 `tests/test_news_search_backend_split.py`。
2. 红灯测试 `search_backend` 模块存在，并暴露 `search()`、`extract_web_content()` 和
   RSS/Juya helper。
3. 红灯测试 `tool.WebTools.search()` 在拆分后仍会使用旧路径 monkeypatch 的
   `_fetch_multi_rss`、`DDGS` 和 `NEWS_SEARCH_DDG_ENABLED`。
4. 红灯测试 `tool._fetch_juya_rss()` 仍使用旧路径 `_urlopen`。
5. 迁移搜索后端实现到 `search_backend.py`。
6. 将 `tool.py` 改为 facade。
7. 跑新测试、搜索后端定向回归、AI 日报相邻回归、`asyncio.run` 策略测试和全量测试。

## 验证计划

红灯：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/test_news_search_backend_split.py -v
```

绿灯和定向回归：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/test_news_search_backend_split.py -v
```

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest \
  tests/test_tools_package.py::test_web_search_mock \
  tests/test_tools_package.py::test_web_extract_mock \
  tests/test_tools_package.py::test_web_search_news_query_uses_daily_timelimit_and_merges_rss_with_web \
  tests/test_tools_package.py::test_web_search_news_query_prefers_ddgs_news_results \
  tests/test_tools_package.py::test_juya_rss_preserves_pubdate_for_freshness_filter \
  tests/test_tools_package.py::test_web_search_latest_query_filters_out_obviously_stale_dated_results \
  tests/test_tools_package.py::test_web_search_preserves_partial_results_when_later_variant_fails \
  -v
```

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest \
  tests/test_news_search_legacy_report.py \
  tests/test_news_search_runtime_cache.py \
  tests/test_ai_daily_tool_and_sources.py \
  tests/test_ai_daily_ingest.py \
  tests/test_kt_framework.py::TestAiDailyTool \
  tests/test_asyncio_run_policy.py::test_asyncio_run_only_appears_under_main_guard \
  -v
```

全量：

```bash
git diff --check
```

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

## 回滚方式

如果拆分后搜索后端、AI 日报工具或旧路径 monkeypatch 出现不可接受的回归，回滚本阶段
生产提交即可恢复旧单文件搜索实现。因为本阶段不改变数据库、配置、工具名、prompt
runtime 模板、缓存 key 或外部 HTTP API，不需要数据迁移或运行时配置回滚。
