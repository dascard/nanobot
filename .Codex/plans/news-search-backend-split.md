# news_search 搜索后端拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `creatures/nanobot/prompts/skills/news_search/tool.py` 中的 RSS / Juya / DDG / trafilatura 搜索后端拆到 `search_backend.py`，保持 `tool.py` 的旧导入路径、旧 monkeypatch 入口和 `WebTools` 行为不变。

**架构：** `search_backend.py` 持有搜索后端真实实现，并提供依赖注入式 `search()` / `extract_web_content()`；`tool.py` 保留旧符号和薄 facade，把 `NEWS_SEARCH_DDG_ENABLED`、`DDGS`、`_fetch_multi_rss`、`_fetch_juya_rss`、`_urlopen`、`trafilatura` 等旧路径可 patch 对象传入新模块。

**技术栈：** Python 3.12、pytest、duckduckgo_search、trafilatura、项目既有 `news_search` 工具模块。

---

## 文件职责

- 创建：`creatures/nanobot/prompts/skills/news_search/search_backend.py`
  - 持有代理感知 `_urlopen()`、`_ddgs_kwargs()`、`NEWS_SEARCH_DDG_ENABLED`。
  - 持有 `JUYA_RSS_URL`、`RSS_KEYWORDS`、`RSS_SOURCES`。
  - 实现 RSS/Juya 抓取、query 判定、timelimit 推断、stale 过滤、query variants、
    domain diversity rerank、DDG 聚合搜索和 trafilatura 正文提取。
  - 提供 `search()`，返回 `(results, last_error)`。
  - 提供 `extract_web_content()`，支持注入 `trafilatura_module`。
- 修改：`creatures/nanobot/prompts/skills/news_search/tool.py`
  - 删除搜索后端真实实现，导入 `search_backend`。
  - 保留 `DDGS`、`trafilatura`、`NEWS_SEARCH_DDG_ENABLED`、`RSS_*`、`JUYA_RSS_URL`
    等旧符号。
  - 保留 `_urlopen()`、`_ddgs_kwargs()`、`_fetch_multi_rss()`、`_fetch_juya_rss()`
    和 query helper wrapper。
  - 保留 `WebTools` 类；`search()` / `extract_web_content()` 作为薄 facade。
  - 不移动 `AiDailyTool`、`search_and_extract_news*()`、`_run_news_daily_pipeline()`。
- 创建：`tests/test_news_search_backend_split.py`
  - 锁定新模块存在、旧路径 search monkeypatch、旧路径 `_urlopen` monkeypatch 和
    旧路径 trafilatura monkeypatch。
- 修改：`.Codex/plans/news-search-backend-split.md`
  - 实现后记录红灯、绿灯、相邻回归、全量回归和提交号。
- 修改：`docs/todo.md`
  - 实现后补充第三刀状态和 `tool.py` 行数变化。
- 修改：`docs/plan_walkthrough.md`
  - 实现后追加第三刀执行记录、验证证据和下一步建议。

## 任务 1：补搜索后端拆分红灯测试

**文件：**
- 创建：`tests/test_news_search_backend_split.py`

- [ ] **步骤 1：新增测试文件头部和新模块导入契约测试**

创建 `tests/test_news_search_backend_split.py`：

```python
from __future__ import annotations

from unittest.mock import MagicMock


def test_search_backend_module_exposes_split_entrypoints():
    from creatures.nanobot.prompts.skills.news_search import search_backend

    assert callable(search_backend.search)
    assert callable(search_backend.extract_web_content)
    assert callable(search_backend._fetch_multi_rss)
    assert callable(search_backend._fetch_juya_rss)
```

- [ ] **步骤 2：新增旧 `tool.WebTools.search` monkeypatch 兼容测试**

在同一文件继续添加：

```python
def test_tool_webtools_search_uses_legacy_patch_points(monkeypatch):
    from creatures.nanobot.prompts.skills.news_search import tool as news_tool

    rss_results = [
        {
            "title": "RSS Fresh",
            "href": "https://rss.example.com/a",
            "body": "rss body",
            "date": "",
            "source_weight": 3,
            "search_strategy": "rss:test",
        }
    ]
    web_results = [
        {
            "title": "Web Fresh",
            "href": "https://web.example.com/b",
            "body": "web body",
        }
    ]

    monkeypatch.setattr(news_tool, "NEWS_SEARCH_DDG_ENABLED", True)
    monkeypatch.setattr(news_tool, "_fetch_multi_rss", lambda query=None, max_results=3: rss_results)
    monkeypatch.setattr(news_tool, "_fetch_juya_rss", lambda max_results=3, target_date=None: [])

    class FakeDDGS:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def text(self, *args, **kwargs):
            return web_results

        def news(self, *args, **kwargs):
            return []

    monkeypatch.setattr(news_tool, "DDGS", FakeDDGS)

    results = news_tool.WebTools.search("AI 最新资讯", max_results=3, deep=False)

    assert any(item["href"] == "https://rss.example.com/a" for item in results)
    assert any(item["href"] == "https://web.example.com/b" for item in results)
    assert news_tool.WebTools.last_error == ""
```

- [ ] **步骤 3：新增旧 `_urlopen` monkeypatch 兼容测试**

在同一文件继续添加：

```python
def test_tool_fetch_juya_rss_uses_legacy_urlopen_patch(monkeypatch):
    from creatures.nanobot.prompts.skills.news_search import tool as news_tool

    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss><channel><item>
  <title>AI Daily 2026-05-01</title>
  <link>https://example.com/issue-1</link>
  <description>fresh issue</description>
  <pubDate>Fri, 01 May 2026 00:00:00 GMT</pubDate>
</item></channel></rss>"""

    mock_resp = MagicMock()
    mock_resp.read.return_value = xml
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False
    calls = []

    def fake_urlopen(url, timeout=10):
        calls.append((url, timeout))
        return mock_resp

    monkeypatch.setattr(news_tool, "_urlopen", fake_urlopen)

    results = news_tool._fetch_juya_rss(max_results=3, target_date="2026-05-01")

    assert calls == [(news_tool.JUYA_RSS_URL, 6)]
    assert len(results) == 1
    assert results[0]["date"].startswith("2026-05-01T")
```

- [ ] **步骤 4：新增旧 trafilatura monkeypatch 兼容测试**

在同一文件继续添加：

```python
def test_tool_extract_web_content_uses_legacy_trafilatura_patch(monkeypatch):
    from creatures.nanobot.prompts.skills.news_search import tool as news_tool

    monkeypatch.setattr(news_tool.trafilatura, "fetch_url", lambda url, timeout=5: "<html>ok</html>")
    monkeypatch.setattr(
        news_tool.trafilatura,
        "extract",
        lambda downloaded, **kwargs: f"extracted:{downloaded}",
    )

    assert news_tool.WebTools.extract_web_content("https://example.com/a") == "extracted:<html>ok</html>"
```

- [ ] **步骤 5：运行红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/test_news_search_backend_split.py -v
```

预期：FAIL，失败原因是 `ImportError: cannot import name 'search_backend'` 或
`ModuleNotFoundError`，证明测试先于新模块实现。

## 任务 2：创建 `search_backend.py`

**文件：**
- 创建：`creatures/nanobot/prompts/skills/news_search/search_backend.py`

- [ ] **步骤 1：新增模块导入、常量和代理 helper**

创建 `search_backend.py`，先写入头部、常量和代理函数：

```python
"""新闻搜索后端实现。"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.request import Request, ProxyHandler, build_opener, urlopen
import xml.etree.ElementTree as ET

from duckduckgo_search import DDGS
import trafilatura

from . import runtime_cache
from .legacy_report import _combined_score, _domain


logger = logging.getLogger("nanobot.ai_daily")

_proxy_url = os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY") or ""
_proxy_opener = (
    build_opener(ProxyHandler({"http": _proxy_url, "https": _proxy_url}))
    if _proxy_url
    else build_opener()
)


def _urlopen(url: str, timeout: int = 10):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return _proxy_opener.open(req, timeout=timeout) if _proxy_url else urlopen(url, timeout=timeout)


def _ddgs_kwargs() -> dict[str, str]:
    return {"proxy": _proxy_url} if _proxy_url else {}


NEWS_SEARCH_DDG_ENABLED = os.environ.get("NEWS_SEARCH_DDG_ENABLED", "0") == "1"
JUYA_RSS_URL = "https://imjuya.github.io/juya-ai-daily/rss.xml"
RSS_KEYWORDS = {
    "juya",
    "ai daily",
    "morning briefing",
    "日报",
    "早报",
    "每日",
    "快讯",
    "newsletter",
    "简报",
    "digest",
}
DAILY_DIGEST_KEYWORDS = runtime_cache.DAILY_DIGEST_KEYWORDS
RSS_SOURCES = [
    {
        "name": "juya_ai_daily",
        "url": "https://imjuya.github.io/juya-ai-daily/rss.xml",
        "weight": 3,
    },
    {
        "name": "reddit_localllama",
        "url": "https://www.reddit.com/r/LocalLLaMA/.rss",
        "weight": 1,
    },
]
```

- [ ] **步骤 2：迁移 query 判定和日期 helper**

继续写入 query 判定、日期解析和 stale 过滤 helper。代码从 `tool.py` 同名函数迁移，
其中 `_extract_date()` 和 `_is_daily_digest_query()` 调用 `runtime_cache`：

```python
def _extract_date(query: str) -> str | None:
    return runtime_cache._extract_date(query, now=datetime.now())


def _is_daily_digest_query(query: str) -> bool:
    return runtime_cache._is_daily_digest_query(query)


def _is_rss_first_query(query: str) -> bool:
    q = (query or "").lower()
    return any(k in q for k in RSS_KEYWORDS)
```

同一步迁移以下函数，保持函数体与旧实现一致：

- `_should_use_juya_direct()`
- `_is_news_query()`
- `_infer_timelimit()`
- `_is_urgent_news_query()`
- `_tokenize_query()`
- `_extract_item_date()`
- `_is_recent_enough()`
- `_normalize_search_result()`
- `_filter_stale_news_results()`
- `_match_query()`
- `_build_query_variants()`

- [ ] **步骤 3：迁移 RSS/Juya 抓取 helper**

继续写入 `_fetch_rss_source()`、`_fetch_multi_rss()` 和 `_fetch_juya_rss()`。三者保持旧逻辑，
但增加 `urlopen_fn` 注入点：

```python
def _fetch_rss_source(
    source: dict[str, Any],
    max_results: int,
    *,
    urlopen_fn: Callable[..., Any] = _urlopen,
) -> list[dict[str, Any]]:
    ...


def _fetch_multi_rss(
    query: str,
    max_results: int,
    *,
    urlopen_fn: Callable[..., Any] = _urlopen,
) -> list[dict[str, Any]]:
    ...


def _fetch_juya_rss(
    max_results: int,
    target_date: str | None = None,
    *,
    urlopen_fn: Callable[..., Any] = _urlopen,
) -> list[dict[str, Any]]:
    ...
```

实现要求：

- `urlopen_fn(source["url"], timeout=6)` 替代旧 `_urlopen(...)`。
- `_fetch_multi_rss()` 调用 `_fetch_rss_source(..., urlopen_fn=urlopen_fn)`。
- 异常日志文本保持旧语义。

- [ ] **步骤 4：迁移排序、搜索和正文提取入口**

继续写入 `_rerank_with_domain_diversity()`、`search()` 和 `extract_web_content()`：

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


def extract_web_content(url: str, *, trafilatura_module: Any = trafilatura) -> str:
    ...
```

实现要求：

- `search()` 中的业务逻辑从旧 `WebTools.search()` 迁移。
- 不直接写 `WebTools.last_error`，改为返回 `last_error` 字符串。
- `ddg_enabled is None` 时读取 `NEWS_SEARCH_DDG_ENABLED`。
- `multi_rss_fetcher(query=query, max_results=rss_limit)` 保留旧参数名。
- `juya_fetcher(max_results=max_results, target_date=target_date)` 保留旧参数名。
- `ddgs_factory(**ddgs_kwargs_fn())` 替代旧 `DDGS(**_ddgs_kwargs())`。
- `extract_web_content()` 使用 `trafilatura_module.fetch_url()` 和
  `trafilatura_module.extract()`。

- [ ] **步骤 5：运行新模块语法检查**

运行：

```bash
python -m compileall creatures/nanobot/prompts/skills/news_search/search_backend.py -q
```

预期：无输出，退出码为 0。

## 任务 3：收敛 `tool.py` 为搜索 facade

**文件：**
- 修改：`creatures/nanobot/prompts/skills/news_search/tool.py`

- [ ] **步骤 1：替换搜索后端导入**

在 `tool.py` 顶部删除只由搜索后端使用的直接 import：

```python
import os as _os
from urllib.request import urlopen, build_opener, ProxyHandler, Request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from duckduckgo_search import DDGS
import trafilatura
```

新增：

```python
from . import search_backend as _search_backend
```

保留 `datetime`、`timedelta`、`timezone` 和 `re`，因为 `tool.py` 的日期 wrapper、
LLM layout 和旧入口仍会使用。

- [ ] **步骤 2：替换代理、DDG、RSS 常量和 helper facade**

删除 `tool.py` 中 `_proxy_url`、`_proxy_opener` 的真实实现，替换为：

```python
DDGS = _search_backend.DDGS
trafilatura = _search_backend.trafilatura


def _urlopen(url, timeout=10):
    return _search_backend._urlopen(url, timeout=timeout)


def _ddgs_kwargs():
    return _search_backend._ddgs_kwargs()
```

将搜索常量替换为：

```python
NEWS_SEARCH_DDG_ENABLED = _search_backend.NEWS_SEARCH_DDG_ENABLED
JUYA_RSS_URL = _search_backend.JUYA_RSS_URL
RSS_KEYWORDS = _search_backend.RSS_KEYWORDS
DAILY_DIGEST_KEYWORDS = _runtime_cache.DAILY_DIGEST_KEYWORDS
RSS_SOURCES = _search_backend.RSS_SOURCES
```

- [ ] **步骤 3：替换搜索 helper wrapper**

删除 `tool.py` 中搜索后端 helper 的真实函数体，保留同名 wrapper：

```python
def _is_rss_first_query(query: str) -> bool:
    return _search_backend._is_rss_first_query(query)


def _should_use_juya_direct(query: str) -> bool:
    return _search_backend._should_use_juya_direct(query)


def _is_news_query(query: str) -> bool:
    return _search_backend._is_news_query(query)


def _infer_timelimit(query: str) -> str | None:
    return _search_backend._infer_timelimit(query)


def _is_urgent_news_query(query: str) -> bool:
    return _search_backend._is_urgent_news_query(query)


def _tokenize_query(query: str) -> list[str]:
    return _search_backend._tokenize_query(query)


def _extract_item_date(item) -> str:
    return _search_backend._extract_item_date(item)


def _is_recent_enough(raw_date: str, hours: int = 72) -> bool:
    return _search_backend._is_recent_enough(raw_date, hours=hours)


def _normalize_search_result(item: dict[str, Any], *, strategy: str) -> dict[str, Any]:
    return _search_backend._normalize_search_result(item, strategy=strategy)


def _filter_stale_news_results(results: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    return _search_backend._filter_stale_news_results(results, query)


def _match_query(item: dict[str, Any], query: str) -> bool:
    return _search_backend._match_query(item, query)
```

继续保留 `_fetch_rss_source()`、`_fetch_multi_rss()`、`_fetch_juya_rss()`、
`_build_query_variants()` 和 `_rerank_with_domain_diversity()` wrapper：

```python
def _fetch_rss_source(source: dict[str, Any], max_results: int) -> list[dict[str, Any]]:
    return _search_backend._fetch_rss_source(source, max_results=max_results, urlopen_fn=_urlopen)


def _fetch_multi_rss(query: str, max_results: int) -> list[dict[str, Any]]:
    return _search_backend._fetch_multi_rss(query=query, max_results=max_results, urlopen_fn=_urlopen)


def _fetch_juya_rss(max_results: int, target_date: str | None = None) -> list[dict[str, Any]]:
    return _search_backend._fetch_juya_rss(
        max_results=max_results,
        target_date=target_date,
        urlopen_fn=_urlopen,
    )
```

- [ ] **步骤 4：替换 `WebTools` 真实实现**

将 `WebTools.search()` 和 `WebTools.extract_web_content()` 改为：

```python
class WebTools:
    last_error: str = ""

    @staticmethod
    def search(query: str, max_results: int = 5, deep: bool = False) -> list[dict]:
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

    @staticmethod
    def extract_web_content(url: str) -> str:
        return _search_backend.extract_web_content(url, trafilatura_module=trafilatura)
```

保持 `search_and_extract_news()` 继续调用 `WebTools.search()` 和
`WebTools.extract_web_content()`。

## 任务 4：验证红绿和相邻回归

**文件：**
- 不修改文件

- [ ] **步骤 1：运行红灯测试的绿灯结果**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/test_news_search_backend_split.py -v
```

预期：PASS，所有新增搜索后端拆分测试通过。

- [ ] **步骤 2：运行搜索后端定向回归**

运行：

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

预期：PASS，旧 DDG/RSS/Juya/trafilatura patch 语义不变。

- [ ] **步骤 3：运行相邻兼容回归**

运行：

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

预期：PASS，legacy report/cache/AI 日报工具和 `asyncio.run` 约束不回归。

- [ ] **步骤 4：运行语法和格式检查**

运行：

```bash
python -m compileall creatures/nanobot/prompts/skills/news_search -q
git diff --check
```

预期：两个命令均无输出，退出码均为 0。

- [ ] **步骤 5：运行全量回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

预期：0 failures。

## 任务 5：提交实现阶段

**文件：**
- 创建：`creatures/nanobot/prompts/skills/news_search/search_backend.py`
- 创建：`tests/test_news_search_backend_split.py`
- 修改：`creatures/nanobot/prompts/skills/news_search/tool.py`
- 修改：`.Codex/plans/news-search-backend-split.md`

- [ ] **步骤 1：记录执行结果**

在本计划顶部追加 `执行结果摘要`，记录：

- 红灯命令和失败原因。
- 绿灯命令和通过数量。
- 搜索后端定向回归结果。
- 相邻兼容回归结果。
- `compileall` 和 `git diff --check` 结果。
- 全量回归结果。
- `tool.py`、`search_backend.py` 和新增测试文件行数。

- [ ] **步骤 2：按文件显式暂存**

运行：

```bash
git add \
  creatures/nanobot/prompts/skills/news_search/search_backend.py \
  creatures/nanobot/prompts/skills/news_search/tool.py \
  tests/test_news_search_backend_split.py \
  .Codex/plans/news-search-backend-split.md
```

- [ ] **步骤 3：检查暂存区**

运行：

```bash
git diff --cached --name-status
git diff --cached --check
```

预期：暂存区只包含本任务 4 个文件；`--check` 无输出。

- [ ] **步骤 4：提交实现**

运行：

```bash
git commit -m "refactor(新闻搜索): 拆分搜索后端"
```

## 任务 6：同步进度文档

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [ ] **步骤 1：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」下补充第三刀进展：

```markdown
  - 进展：`creatures/nanobot/prompts/skills/news_search/tool.py` 第三刀已拆出
    搜索后端到 `news_search/search_backend.py`；`tool.py` 行数从 1110 行降至实际
    `wc -l` 输出值，旧 `WebTools`、RSS/Juya/DDG/trafilatura monkeypatch 入口保留为
    `tool.py` facade。写入文档时必须填入具体数字。
```

- [ ] **步骤 2：更新 `docs/plan_walkthrough.md`**

追加本阶段完成记录，包含：

- 设计提交 `4ba9d95 docs(新闻搜索): 设计搜索后端拆分`。
- 计划提交号。
- 实现提交号。
- 红灯、绿灯、相邻回归、全量回归结果。
- 行数变化。
- 下一步建议：继续评估 V2 evidence bridge 或 AI 日报适配层拆分。

- [ ] **步骤 3：验证文档**

运行：

```bash
git diff --check -- docs/todo.md docs/plan_walkthrough.md
python - <<'PY'
from pathlib import Path
markers = [
    chr(60) + '实际行数',
    chr(60) + '失败数量',
    chr(60) + '通过数量',
    '\u5f85\u5b9a',
    'TO' + 'DO',
]
paths = [Path('docs/todo.md'), Path('docs/plan_walkthrough.md')]
hits = []
for path in paths:
    text = path.read_text(encoding='utf-8')
    hits.extend(f'{path}: {marker}' for marker in markers if marker in text)
if hits:
    raise SystemExit('\n'.join(hits))
PY
```

预期：两个命令均无输出，退出码均为 0。

- [ ] **步骤 4：运行全量回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

预期：0 failures。

- [ ] **步骤 5：提交文档收口**

运行：

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/news-search-backend-split.md
git diff --cached --name-status
git diff --cached --check
git commit -m "docs(计划): 收口搜索后端拆分状态"
```
