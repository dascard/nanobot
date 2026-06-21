# news_search 运行时缓存拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `creatures/nanobot/prompts/skills/news_search/tool.py` 中的运行时缓存状态、缓存 key 计算和日期解析 helper 拆到 `runtime_cache.py`，保持旧 `tool.py` 符号、AI 日报缓存行为和测试 monkeypatch 入口不变。

**架构：** `runtime_cache.py` 持有缓存 dict、锁、TTL 默认值、日期解析、日报查询识别、缓存 key 计算和缓存读写纯逻辑，只依赖标准库。`tool.py` 继续作为旧搜索 / AI 日报工具 facade，保留同名旧符号；缓存 dict / lock 指向新模块同一对象，函数通过薄 wrapper 调用新模块并保留旧 TTL monkeypatch 语义。

**技术栈：** Python 3.12、pytest、KohakuTerrarium BaseTool、项目既有 `news_search` 工具模块。

---

## 文件职责

- 创建：`creatures/nanobot/prompts/skills/news_search/runtime_cache.py`
  - 持有 `NEWS_SEARCH_CACHE_TTL_SECONDS`、`NEWS_SEARCH_CACHE_MAX_ENTRIES`、
    `_NEWS_SEARCH_CACHE` 和 `_NEWS_SEARCH_CACHE_LOCK`。
  - 实现 `_coerce_date()`、`_extract_date()`、`_is_daily_digest_query()`、
    `_news_search_cache_key()`、`_get_cached_news_result()` 和
    `_store_cached_news_result()`。
  - 只依赖标准库，不导入 `tool.py`、`DDGS`、`trafilatura`、`BaseTool`、
    `NewAPIClient` 或 `run_awaitable_sync`。
- 修改：`creatures/nanobot/prompts/skills/news_search/tool.py`
  - 删除本地缓存状态、日期解析和缓存 helper 的真实实现。
  - 从 `runtime_cache.py` 绑定同一个缓存 dict / lock。
  - 保留 `_news_search_cache_key()`、`_get_cached_news_result()` 和
    `_store_cached_news_result()` wrapper；`AiDailyTool._execute()` 继续调用旧函数名。
- 创建：`tests/test_news_search_runtime_cache.py`
  - 锁定新模块轻量导入、缓存 key 形态、旧 facade 共享缓存状态和 TTL monkeypatch 行为。
- 修改：`.Codex/plans/news-search-cache-split.md`
  - 执行时勾选步骤并记录实际红灯、绿灯、相邻回归、全量回归和提交号。
- 修改：`docs/todo.md`
  - 代码阶段完成后补充第二刀实现状态，不勾选整个「超大文件 >800 行拆分」项。
- 修改：`docs/plan_walkthrough.md`
  - 追加第二刀的执行记录、验证证据、行数变化和提交号。

## 任务 1：补运行时缓存红灯测试

**文件：**
- 创建：`tests/test_news_search_runtime_cache.py`

- [ ] **步骤 1：新增测试文件头部和轻量导入测试**

创建 `tests/test_news_search_runtime_cache.py`：

```python
from __future__ import annotations

import subprocess
import sys


def test_runtime_cache_imports_without_runtime_tool_dependencies():
    code = """
import sys
from creatures.nanobot.prompts.skills.news_search import runtime_cache
blocked = [
    "creatures.nanobot.prompts.skills.news_search.tool",
    "duckduckgo_search",
    "trafilatura",
    "kohakuterrarium.modules.tool.base",
]
loaded = [name for name in blocked if name in sys.modules]
assert not loaded, loaded
assert runtime_cache._NEWS_SEARCH_CACHE == {}
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
```

- [ ] **步骤 2：新增缓存 key 契约测试**

在同一文件继续添加：

```python
def test_runtime_cache_key_preserves_daily_and_query_contract():
    from creatures.nanobot.prompts.skills.news_search import runtime_cache

    daily_key = runtime_cache._news_search_cache_key(
        "2026年5月1日 AI 日报",
        8,
        mode="quality",
        user_id="user-a",
        session_id="session-a",
    )
    query_key = runtime_cache._news_search_cache_key(
        "  GPT-5   NEWS  ",
        3,
        mode="fast",
        user_id="user-a",
        session_id="session-a",
    )

    assert daily_key == ("v2_20260503", "daily_ai", "2026-05-01", 8, "quality")
    assert query_key == ("v2_20260503", "query", "gpt-5 news", 3, "fast")
```

- [ ] **步骤 3：新增 `tool.py` facade 兼容测试**

在同一文件继续添加：

```python
def test_tool_cache_facade_shares_runtime_state_and_honors_legacy_ttl(monkeypatch):
    from creatures.nanobot.prompts.skills.news_search import runtime_cache
    from creatures.nanobot.prompts.skills.news_search import tool as news_tool

    key = runtime_cache._news_search_cache_key("AI news", 3)
    news_tool._NEWS_SEARCH_CACHE.clear()

    assert news_tool._NEWS_SEARCH_CACHE is runtime_cache._NEWS_SEARCH_CACHE
    assert news_tool._NEWS_SEARCH_CACHE_LOCK is runtime_cache._NEWS_SEARCH_CACHE_LOCK

    runtime_cache._store_cached_news_result(key, "<article>cached</article>")
    assert news_tool._get_cached_news_result(key) == "<article>cached</article>"

    monkeypatch.setattr(news_tool, "NEWS_SEARCH_CACHE_TTL_SECONDS", -1)
    assert news_tool._get_cached_news_result(key) is None
```

- [ ] **步骤 4：运行红灯测试**

运行：

```bash
python -m pytest tests/test_news_search_runtime_cache.py -v
```

预期：FAIL，至少包含 `ImportError` 或 `ModuleNotFoundError`，原因是
`creatures.nanobot.prompts.skills.news_search.runtime_cache` 尚不存在。

- [ ] **步骤 5：提交测试红灯阶段**

测试红灯阶段不提交。该阶段只记录失败输出，生产实现通过后与实现一起提交。

## 任务 2：实现 `runtime_cache.py` 并收敛 `tool.py` facade

**文件：**
- 创建：`creatures/nanobot/prompts/skills/news_search/runtime_cache.py`
- 修改：`creatures/nanobot/prompts/skills/news_search/tool.py`

- [ ] **步骤 1：创建 `runtime_cache.py`**

新增 `creatures/nanobot/prompts/skills/news_search/runtime_cache.py`：

```python
"""运行时新闻搜索缓存 helper。"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any


NEWS_SEARCH_CACHE_TTL_SECONDS = int(os.environ.get("NEWS_SEARCH_CACHE_TTL_SECONDS", "300"))
NEWS_SEARCH_CACHE_MAX_ENTRIES = 64
_NEWS_SEARCH_CACHE: dict[tuple[Any, ...], tuple[float, str]] = {}
_NEWS_SEARCH_CACHE_LOCK = threading.Lock()


DAILY_DIGEST_KEYWORDS = {
    "日报",
    "早报",
    "每日",
    "今日ai",
    "今天ai",
    "ai daily",
    "morning briefing",
    "简报",
    "digest",
}


def _coerce_date(year: int | str, month: int | str, day: int | str) -> str | None:
    try:
        return datetime(int(year), int(month), int(day)).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _extract_date(query: str, *, now: datetime | None = None) -> str | None:
    text = query or ""
    current = now or datetime.now()

    match = re.search(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", text)
    if match:
        return _coerce_date(match.group(1), match.group(2), match.group(3))

    match = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", text)
    if match:
        return _coerce_date(match.group(1), match.group(2), match.group(3))

    match = re.search(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", text)
    if match:
        return _coerce_date(current.year, match.group(1), match.group(2))

    if re.search(r"\b(today)\b|今天|今日", text, flags=re.IGNORECASE):
        return current.strftime("%Y-%m-%d")

    return None


def _is_daily_digest_query(query: str) -> bool:
    q = (query or "").lower()
    return any(keyword in q for keyword in DAILY_DIGEST_KEYWORDS)


def _news_search_cache_key(
    query: str,
    max_results: int,
    mode: str = "fast",
    user_id: str = "",
    session_id: str = "",
    *,
    now: datetime | None = None,
    date_extractor: Callable[[str], str | None] | None = None,
    daily_digest_detector: Callable[[str], bool] | None = None,
) -> tuple[Any, ...]:
    del user_id, session_id
    current = now or datetime.now()
    q = re.sub(r"\s+", " ", (query or "").lower()).strip()
    extract = date_extractor or _extract_date
    is_daily_digest = daily_digest_detector or _is_daily_digest_query
    target_date = extract(query)
    today = current.strftime("%Y-%m-%d")
    version = "v2_20260503"
    if is_daily_digest(q):
        return (version, "daily_ai", target_date or today, int(max_results), mode)
    return (version, "query", q, int(max_results), mode)


def _get_cached_news_result(
    key: tuple[Any, ...],
    *,
    ttl_seconds: int | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> str | None:
    ttl = NEWS_SEARCH_CACHE_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    now = monotonic()
    with _NEWS_SEARCH_CACHE_LOCK:
        cached = _NEWS_SEARCH_CACHE.get(key)
        if not cached:
            return None
        created_at, output = cached
        if now - created_at > ttl:
            _NEWS_SEARCH_CACHE.pop(key, None)
            return None
        return output


def _store_cached_news_result(
    key: tuple[Any, ...],
    output: str,
    *,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    with _NEWS_SEARCH_CACHE_LOCK:
        if len(_NEWS_SEARCH_CACHE) > NEWS_SEARCH_CACHE_MAX_ENTRIES:
            oldest_key = min(_NEWS_SEARCH_CACHE, key=lambda k: _NEWS_SEARCH_CACHE[k][0])
            _NEWS_SEARCH_CACHE.pop(oldest_key, None)
        _NEWS_SEARCH_CACHE[key] = (monotonic(), output)
```

- [ ] **步骤 2：替换 `tool.py` 顶部缓存状态**

在 `tool.py` 导入区加入：

```python
from . import runtime_cache as _runtime_cache
```

将原本的缓存状态：

```python
NEWS_SEARCH_CACHE_TTL_SECONDS = int(os.environ.get("NEWS_SEARCH_CACHE_TTL_SECONDS", "300"))
NEWS_SEARCH_DDG_ENABLED = os.environ.get("NEWS_SEARCH_DDG_ENABLED", "0") == "1"
_NEWS_SEARCH_CACHE: dict[tuple[Any, ...], tuple[float, str]] = {}
_NEWS_SEARCH_CACHE_LOCK = threading.Lock()
```

替换为：

```python
NEWS_SEARCH_CACHE_TTL_SECONDS = _runtime_cache.NEWS_SEARCH_CACHE_TTL_SECONDS
NEWS_SEARCH_DDG_ENABLED = os.environ.get("NEWS_SEARCH_DDG_ENABLED", "0") == "1"
_NEWS_SEARCH_CACHE = _runtime_cache._NEWS_SEARCH_CACHE
_NEWS_SEARCH_CACHE_LOCK = _runtime_cache._NEWS_SEARCH_CACHE_LOCK
```

- [ ] **步骤 3：替换 `tool.py` 日期解析 helper**

删除 `tool.py` 本地 `_coerce_date()` 和 `_extract_date()` 真实实现，改为：

```python
_coerce_date = _runtime_cache._coerce_date
_extract_date = _runtime_cache._extract_date
```

保留其他搜索日期调用点不变。

- [ ] **步骤 4：替换 `tool.py` 日报识别和缓存 helper**

删除 `tool.py` 本地 `_is_daily_digest_query()`、`_news_search_cache_key()`、
`_get_cached_news_result()` 和 `_store_cached_news_result()` 的真实实现，改为：

```python
_is_daily_digest_query = _runtime_cache._is_daily_digest_query


def _news_search_cache_key(
    query: str,
    max_results: int,
    mode: str = "fast",
    user_id: str = "",
    session_id: str = "",
) -> tuple[Any, ...]:
    return _runtime_cache._news_search_cache_key(
        query,
        max_results,
        mode=mode,
        user_id=user_id,
        session_id=session_id,
        date_extractor=_extract_date,
        daily_digest_detector=_is_daily_digest_query,
    )


def _get_cached_news_result(key: tuple[Any, ...]) -> str | None:
    return _runtime_cache._get_cached_news_result(
        key,
        ttl_seconds=NEWS_SEARCH_CACHE_TTL_SECONDS,
    )


def _store_cached_news_result(key: tuple[Any, ...], output: str) -> None:
    _runtime_cache._store_cached_news_result(key, output)
```

- [ ] **步骤 5：清理 `tool.py` 未用 import**

运行：

```bash
python -m compileall creatures/nanobot/prompts/skills/news_search -q
```

预期：退出码 0，无语法错误。若 `threading` 或 `time` 只被缓存实现使用，从 `tool.py`
删除对应 import；若文件其他位置仍使用则保留。

- [ ] **步骤 6：运行运行时缓存绿灯测试**

运行：

```bash
python -m pytest tests/test_news_search_runtime_cache.py -v
```

预期：PASS，3 个测试全部通过。

- [ ] **步骤 7：运行 AI 日报缓存相邻回归**

运行：

```bash
python -m pytest tests/test_tools_package.py::test_ai_daily_tool_reuses_equivalent_daily_query_cache -v
python -m pytest tests/test_ai_daily_ingest.py -v
python -m pytest tests/test_ai_daily_tool_and_sources.py -v
python -m pytest tests/test_news_search_legacy_report.py -v
```

预期：全部 PASS。重点确认同一日报请求第二次仍复用缓存，ingest warning 不影响工具成功。

- [ ] **步骤 8：运行 `asyncio.run` 约束测试**

运行：

```bash
python -m pytest tests/test_asyncio_run_policy.py::test_asyncio_run_only_appears_under_main_guard -v
```

预期：PASS，证明本阶段没有新增非 main guard 下的 `asyncio.run()`。

- [ ] **步骤 9：提交缓存拆分实现阶段**

运行：

```bash
git diff --check
python -m pytest tests/ -v
git add creatures/nanobot/prompts/skills/news_search/runtime_cache.py \
  creatures/nanobot/prompts/skills/news_search/tool.py \
  tests/test_news_search_runtime_cache.py
git commit -m "refactor(新闻搜索): 拆分运行时缓存"
```

预期：`git diff --check` 无输出，全量测试 0 failures，commit 成功。

## 任务 3：同步计划、TODO 和 walkthrough

**文件：**
- 修改：`.Codex/plans/news-search-cache-split.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [ ] **步骤 1：更新本计划执行结果**

在本计划顶部或对应任务步骤中写入实际结果：

```markdown
## 执行结果摘要

- 红灯：记录 `python -m pytest tests/test_news_search_runtime_cache.py -v` 的失败数量和失败原因。
- 绿灯：记录 `python -m pytest tests/test_news_search_runtime_cache.py -v` 的通过数量。
- 相邻回归：`tests/test_tools_package.py::test_ai_daily_tool_reuses_equivalent_daily_query_cache`、
  `tests/test_ai_daily_ingest.py`、`tests/test_ai_daily_tool_and_sources.py`、
  `tests/test_news_search_legacy_report.py`，记录通过数量。
- `asyncio.run` 约束：`tests/test_asyncio_run_policy.py::test_asyncio_run_only_appears_under_main_guard`
  记录通过数量。
- 全量回归：`python -m pytest tests/ -v`，记录通过数量。
- 提交：`refactor(新闻搜索): 拆分运行时缓存`。
```

实际填写时使用命令输出中的具体数字和失败原因，不写概括性成功判断。

- [ ] **步骤 2：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」条目下追加：

```markdown
  - 进展：`creatures/nanobot/prompts/skills/news_search/tool.py` 第二刀已拆出
    运行时缓存到 `news_search/runtime_cache.py`；旧 `tool.py` 缓存符号保留为同一
    dict / lock 和薄 wrapper，AI 日报缓存命中行为不变。
```

- [ ] **步骤 3：更新 `docs/plan_walkthrough.md`**

追加 `2026-06-21 news_search/tool.py 运行时缓存拆分` 小节，记录：

```markdown
- [x] 阶段 0：设计文档和实现计划。
- [x] 阶段 1：运行时缓存红灯测试。
- [x] 阶段 2：新增 `runtime_cache.py` 并收敛 `tool.py` facade。
- [x] 阶段 3：运行相邻回归、`asyncio.run` 策略测试和全量回归。
- [x] 阶段 4：同步 TODO、walkthrough 和计划状态。
```

同时记录行数变化、验证结果和提交号。

- [ ] **步骤 4：验证文档阶段**

运行：

```bash
git diff --check
python - <<'PY'
from pathlib import Path

markers = [chr(60) + "失败数量", chr(60) + "通过数量", "\u5f85\u5b9a"]
paths = [
    Path(".Codex/plans/news-search-cache-split.md"),
    Path("docs/todo.md"),
    Path("docs/plan_walkthrough.md"),
]
hits = []
for path in paths:
    text = path.read_text(encoding="utf-8")
    hits.extend(f"{path}: {marker}" for marker in markers if marker in text)
if hits:
    raise SystemExit("\n".join(hits))
PY
python -m pytest tests/ -v
```

预期：`git diff --check` 无输出；红旗扫描无占位符命中；全量测试 0 failures。

- [ ] **步骤 5：提交文档收口阶段**

运行：

```bash
git add .Codex/plans/news-search-cache-split.md docs/todo.md docs/plan_walkthrough.md
git commit -m "docs(计划): 收口新闻搜索缓存拆分状态"
```

预期：commit 成功。

## 阶段性验证清单

- [ ] 新增测试先红后绿，红灯原因来自 `runtime_cache.py` 缺失或旧 facade 契约未满足。
- [ ] `runtime_cache.py` 轻量导入测试证明没有加载运行时工具依赖。
- [ ] `news_tool._NEWS_SEARCH_CACHE.clear()` 仍清理实际运行缓存。
- [ ] `news_tool.NEWS_SEARCH_CACHE_TTL_SECONDS` monkeypatch 仍影响旧 `_get_cached_news_result()`。
- [ ] AI 日报缓存复用测试仍只调用一次 `_run_news_daily_pipeline()`。
- [ ] `tests/test_asyncio_run_policy.py::test_asyncio_run_only_appears_under_main_guard` 通过。
- [ ] `python -m pytest tests/ -v` 0 failures 后再 commit。
