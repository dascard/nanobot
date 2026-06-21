# news_search/tool.py 拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」仍包含
`creatures/nanobot/prompts/skills/news_search/tool.py`。该文件拆分前约 1835 行，
同时承担代理感知 HTTP 访问、DuckDuckGo / RSS 搜索、旧版新闻 HTML 报告、LLM
版式摘要、V2 evidence bridge、AI 日报缓存和 `AiDailyTool` KT 工具适配等职责。

只读审计显示，`tool.py` 中 70-884 行集中为旧版新闻报告相关逻辑：

- 来源域名、来源质量和时效评分。
- 免费、低价、模型名等价值信号提取。
- 文本截断、HTML 转义和 Markdown 表格转义。
- 旧版 `search_and_extract_news()` 的不可用报告、结构化 layout fallback 和最终
  `<article class="news-brief">` HTML 渲染。
- `_parse_news_layout_payload()` 与 `_merge_layout_with_fallback()` 等 layout
  解析和补全 helper。

这些逻辑主要是纯函数，不直接依赖 `BaseTool`、`DDGS`、`trafilatura`、RSS 网络请求、
缓存状态或 KT 工具注册。因此第一刀适合把旧版报告渲染边界迁移到独立模块，同时保持
`tool.py` 的 public import 兼容和顶层运行入口 monkeypatch 兼容。

## 目标

第一阶段做无行为变化的报告模块拆分：

1. 新增 `creatures/nanobot/prompts/skills/news_search/legacy_report.py`。
2. 将旧版新闻报告、评分、价值信号和 layout fallback 的纯 helper 迁移到新模块。
3. `tool.py` 通过显式 import re-export 迁移后的下划线 helper，保持旧导入路径可用。
4. 保持 `search_and_extract_news()`、`WebTools`、`search_and_extract_news_v2()` 和
   `AiDailyTool` 的签名、工具名、返回契约不变。
5. 保持 `patch("creatures.nanobot.prompts.skills.news_search.tool._summarize_news_layout")`
   等现有测试 monkeypatch 入口可用。
6. 用 characterization tests 锁住新模块轻量导入、旧路径 re-export 和旧 HTML 输出契约。
7. `_parse_news_layout_payload()` 只允许依赖轻量 `core.json_utils`，不得通过
   `core.legacy_adapter` 反向导入 `news_search.tool` 或 runtime tool 依赖。

## 非目标

本阶段不做以下事情：

- 不迁移 `AiDailyTool`、`_build_ai_daily_tool_result()`、`_render_ai_daily_fallback()`。
- 不迁移 `WebTools`、`DDGS`、`trafilatura`、`_urlopen()`、RSS 抓取和查询变体逻辑。
- 不迁移 `_run_news_daily_pipeline()`、`search_and_extract_news_v2()` 或 `news_daily/` 子流水线。
- 不改变 `ai_daily` 是唯一模型可见日报工具的现状。
- 不恢复 `NewsSearchTool`，不重新暴露 `news_search` 工具名。
- 不调整缓存 key、缓存 TTL、DDG 默认开关、持久化格式或 source registry。
- 不改 prompt runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime 输入。
- 不新增 `asyncio.run()`，不新增同步函数包 awaitable。
- 不合并两个现存 `_is_daily_digest_query()` 定义，避免第一刀混入行为修正。

`_summarize_news_layout()` 当前包含 LLM 调用和既有同步桥接。第一刀不扩大这块范围：
它保留在 `tool.py`，继续作为 legacy 搜索流程里的 monkeypatch 目标；新模块只承接
被它调用的 layout 解析、fallback 和最终 HTML 渲染 helper。

## 方案比较

### 方案 A：拆出 `legacy_report.py`

迁移旧版报告、评分、价值信号、layout fallback 和 HTML 渲染 helper。
`tool.py` 保留搜索、缓存、AI 日报工具和 LLM 版式摘要入口，并 re-export 迁移后的 helper。

优点：

- 能一次移出约数百行纯函数，直接服务超大文件拆分目标。
- 不触碰网络搜索、工具注册、缓存状态和新日报主 pipeline。
- 现有 `tests/test_tools_package.py` 已覆盖主要 HTML 和 layout 行为。
- 旧路径 re-export 后，导入调用方不需要改；顶层搜索流程里仍被 `tool.py`
  直接引用的入口可以继续 patch 旧路径。

缺点：

- `tool.py` 第一刀后仍会超过 800 行，需要后续继续拆搜索后端或 AI 日报适配层。
- `_summarize_news_layout()` 仍留在旧文件，LLM 摘要与报告 helper 分属两个模块。

### 方案 B：拆出 `cache.py`

迁移 `_news_search_cache_key()`、缓存读写和锁，新增缓存 key contract tests。

优点：

- 依赖最少，红灯测试容易构造。
- 对网络、HTML、工具注册影响极小。

缺点：

- 只能减少很少行数，对「超大文件」治理收益不足。
- `AiDailyTool` 对旧路径缓存 helper 的 monkeypatch 兼容需要额外 facade 设计。
- 不能解决 `tool.py` 中最大块职责混杂问题。

### 方案 C：拆出 `ai_daily_adapter.py`

迁移 `AiDailyTool` 及其 reply 包装、缓存调用和 pipeline fallback。

优点：

- 直接把当前模型可见工具从旧搜索文件里分离出来。
- 有利于后续把旧 `search_and_extract_news()` 下线。

缺点：

- KT 注册、reply payload、`asyncio.to_thread()`、入库 metadata 和旧路径 monkeypatch
  都集中在这块，兼容风险最高。
- 用户已明确反感同步 wrapper 包 awaitable，本阶段不应把异步边界和文件拆分揉在一起。

推荐采用方案 A。方案 B 可作为后续“小而纯”的缓存拆分，方案 C 应等报告和搜索后端
边界稳定后再做。

## 模块边界

新增模块：

- `creatures/nanobot/prompts/skills/news_search/legacy_report.py`

该模块负责：

- `TRUSTED_NEWS_DOMAINS`
- `VALUE_ALERT_KEYWORDS`
- `MODEL_NAME_HINTS`
- `_domain()`
- `_source_score()`
- `_freshness_score()`
- `_combined_score()`
- `_value_signal_score()`
- `_extract_model_hints()`
- `_build_value_alert()`
- `_truncate_text()`
- `_normalize_summary_text()`
- `_escape_md_table_cell()`
- `_escape_html()`
- `_build_news_conclusion()`
- `_build_news_brief_items()`
- `_format_news_unavailable_report()`
- `_coerce_layout_text()`
- `_coerce_layout_list()`
- `_parse_news_layout_payload()`
- `_specificity_score()`
- `_merge_specific_items()`
- `_merge_layout_with_fallback()`
- `_build_news_layout_fallback()`
- `_format_news_html_report()`

保留模块：

- `creatures/nanobot/prompts/skills/news_search/tool.py`

该模块继续负责：

- 代理感知 `_urlopen()`、`_ddgs_kwargs()`。
- `NEWS_SEARCH_CACHE_TTL_SECONDS`、`NEWS_SEARCH_DDG_ENABLED` 和 `_NEWS_SEARCH_CACHE`。
- RSS / Juya / DDG 搜索后端。
- `_heuristic_should_deepen()`、`_model_should_deepen()` 和 `_summarize_news_layout()`。
- `WebTools`、`search_and_extract_news()`、`search_and_extract_news_v2()`。
- `AiDailyTool`、AI 日报 fallback、reply wrapping 和 ingest metadata。
- 从 `legacy_report.py` re-export 旧版报告 helper。

## 兼容导出

`tool.py` 中需要显式导入并暴露迁移后的对象，例如：

```python
from .legacy_report import (
    MODEL_NAME_HINTS,
    TRUSTED_NEWS_DOMAINS,
    VALUE_ALERT_KEYWORDS,
    _build_news_brief_items,
    _build_news_conclusion,
    _build_news_layout_fallback,
    _build_value_alert,
    _coerce_layout_list,
    _coerce_layout_text,
    _combined_score,
    _domain,
    _escape_html,
    _escape_md_table_cell,
    _extract_model_hints,
    _format_news_html_report,
    _format_news_unavailable_report,
    _freshness_score,
    _merge_layout_with_fallback,
    _merge_specific_items,
    _normalize_summary_text,
    _parse_news_layout_payload,
    _source_score,
    _specificity_score,
    _truncate_text,
    _value_signal_score,
)
```

旧路径必须保持可用：

- `from creatures.nanobot.prompts.skills.news_search.tool import AiDailyTool`
- `from creatures.nanobot.prompts.skills.news_search.tool import WebTools`
- `from creatures.nanobot.prompts.skills.news_search.tool import search_and_extract_news`
- `from creatures.nanobot.prompts.skills.news_search.tool import _parse_news_layout_payload`
- `from creatures.nanobot.prompts.skills.news_search.tool import _merge_layout_with_fallback`
- `from creatures.nanobot.prompts.skills.news_search import tool as news_tool`

现有顶层流程 monkeypatch 路径必须保持有效：

- `creatures.nanobot.prompts.skills.news_search.tool._summarize_news_layout`
- `creatures.nanobot.prompts.skills.news_search.tool.WebTools.search`
- `creatures.nanobot.prompts.skills.news_search.tool.WebTools.extract_web_content`
- `creatures.nanobot.prompts.skills.news_search.tool._run_news_daily_pipeline`
- `creatures.nanobot.prompts.skills.news_search.tool._fetch_multi_rss`
- `creatures.nanobot.prompts.skills.news_search.tool._fetch_juya_rss`
- `creatures.nanobot.prompts.skills.news_search.tool._urlopen`
- `creatures.nanobot.prompts.skills.news_search.tool.DDGS`
- `creatures.nanobot.prompts.skills.news_search.tool.trafilatura.fetch_url`

迁移后的报告内部 helper 只承诺旧路径导入兼容，不承诺内部依赖的旧路径 monkeypatch
语义。需要替换 `_parse_news_layout_payload()`、`_merge_layout_with_fallback()` 或
`_format_news_html_report()` 等报告 helper 的内部行为时，应 patch
`creatures.nanobot.prompts.skills.news_search.legacy_report` 路径；旧 `tool.py`
re-export 用于兼容直接导入和直接调用。

## 行为契约

本阶段必须保持以下行为不变：

- `search_and_extract_news(query, max_results=3, *, persist=False, user_id="", session_id="")`
  成功时返回包含 `<article class="news-brief">` 的 HTML。
- 搜索后端失败时返回 `_format_news_unavailable_report()` 生成的不可用 HTML，不返回空字符串。
- 旧版 HTML 中保留 `news-brief`、`hero`、`section`、`来源索引`、`延伸阅读` 等既有结构。
- layout 小 JSON schema 仍可由 `_parse_news_layout_payload()` 解析，并由
  `_merge_layout_with_fallback()` 补足具体模型和更多条目。
- `AiDailyTool.tool_name == "ai_daily"`，`execution_mode == ExecutionMode.DIRECT`。
- `AiDailyTool` 输出仍由 `build_reply_tool_result()` 包装，并保留 `ai_daily_ingest` metadata。
- `NEWS_SEARCH_DDG_ENABLED` 默认仍由环境变量控制，测试可 monkeypatch。
- schema preview 不能因为拆分而导入 runtime tool 模块。

## TDD 策略

第一刀先补 characterization tests，再迁移代码：

1. 新增 `tests/test_news_search_legacy_report.py`。
2. 红灯测试 `legacy_report` 模块存在，并可在全新 Python 进程中导入，且不导入
   `duckduckgo_search`、`trafilatura`、`kohakuterrarium.modules.tool.base`。
3. 红灯测试 `tool.py` re-export 的 layout helper 与 `legacy_report.py` 是同一函数对象。
4. 迁移前运行红灯命令，预期因模块不存在失败。
5. 迁移 helper 到 `legacy_report.py`，在 `tool.py` 显式 import re-export。
6. 跑新测试、legacy HTML 定向测试、AI 日报相邻测试、`asyncio.run` 策略测试和全量测试。

审查修复补充：

7. 红灯测试 `_parse_news_layout_payload()` 在全新 Python 进程中调用时，不导入
   `news_search.tool`、`duckduckgo_search`、`trafilatura` 或 `BaseTool`。
8. 抽出 `core.json_utils.json_repair()`，让 `EvolutionUtils.json_repair()` 代理到
   轻量 helper；`legacy_report.py` 直接使用该 helper。

## 验证计划

红灯：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest \
  tests/test_news_search_legacy_report.py::test_legacy_report_imports_without_runtime_tool_dependencies \
  tests/test_news_search_legacy_report.py::test_tool_reexports_legacy_report_helpers \
  -v
```

绿灯和定向回归：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/test_news_search_legacy_report.py -v
```

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest \
  tests/test_tools_package.py::test_parse_news_layout_payload_accepts_small_json_schema \
  tests/test_tools_package.py::test_merge_layout_with_fallback_backfills_specific_models_and_more_items \
  tests/test_tools_package.py::test_combined_news_tool_renders_fixed_html_template_from_structured_layout \
  tests/test_tools_package.py::test_combined_news_tool_returns_unavailable_html_when_search_backends_fail \
  -v
```

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest \
  tests/test_tools_package.py::test_web_search_mock \
  tests/test_tools_package.py::test_web_search_news_query_uses_daily_timelimit_and_merges_rss_with_web \
  tests/test_tools_package.py::test_web_search_latest_query_filters_out_obviously_stale_dated_results \
  tests/test_tools_package.py::test_web_search_preserves_partial_results_when_later_variant_fails \
  tests/test_ai_daily_tool_and_sources.py::test_ai_daily_is_only_model_facing_daily_tool \
  tests/test_ai_daily_tool_and_sources.py::test_ai_daily_tool_returns_fallback_html_when_pipeline_empty \
  tests/test_ai_daily_tool_and_sources.py::test_ai_daily_tool_returns_fallback_html_when_pipeline_plain_text \
  tests/test_kt_framework.py::TestAiDailyTool \
  -v
```

策略和全量：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/test_asyncio_run_policy.py::test_asyncio_run_only_appears_under_main_guard -v
```

```bash
git diff --check
```

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

## 回滚方式

如果迁移后 legacy HTML、AI 日报工具或 monkeypatch 兼容出现不可接受的回归，回滚本阶段
生产提交即可恢复旧单文件实现。因为本阶段不改变数据库、配置、工具名、prompt runtime
模板和外部 HTTP 路径，不需要数据迁移或运行时配置回滚。
