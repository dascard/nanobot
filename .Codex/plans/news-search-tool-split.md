# news_search/tool.py 拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [x]`）语法来跟踪进度。

**目标：** 将 `creatures/nanobot/prompts/skills/news_search/tool.py` 中旧版新闻报告、评分和 layout helper 拆到 `legacy_report.py`，保持搜索、AI 日报工具、旧导入路径和 HTML 输出契约不变。

**架构：** `tool.py` 继续作为 legacy 搜索和 KT 工具 facade，持有 `WebTools`、`search_and_extract_news()`、`search_and_extract_news_v2()`、`AiDailyTool`、缓存和网络搜索后端。新增 `legacy_report.py` 只承接纯报告 helper；`tool.py` 显式 import re-export 这些 helper，以兼容旧路径导入。迁移后的报告内部 helper 若需要 monkeypatch，应使用 `legacy_report` 路径；`tool.py` 仅继续承诺顶层搜索流程和运行时依赖入口的 patch 兼容。

**技术栈：** Python 3.12、pytest、KohakuTerrarium BaseTool、项目既有 news_search 工具模块。

---

## 文件职责

- 创建：`creatures/nanobot/prompts/skills/news_search/legacy_report.py`
  - 持有旧版新闻报告相关常量、评分 helper、价值信号 helper、layout fallback / merge / parse helper 和 `_format_news_html_report()`。
  - 只依赖 Python 标准库和轻量 `core.json_utils`，不导入 `DDGS`、`trafilatura`、`BaseTool`、`NewAPIClient` 或 `run_awaitable_sync`。
- 创建：`core/json_utils.py`
  - 承接 `EvolutionUtils.json_repair()` 原有容错 JSON 解析逻辑，避免报告模块反向导入 `core.legacy_adapter`。
- 修改：`core/legacy_adapter.py`
  - `EvolutionUtils.json_repair()` 代理到 `core.json_utils.json_repair()`，保持旧 API 行为兼容。
- 修改：`creatures/nanobot/prompts/skills/news_search/tool.py`
  - 删除迁移到 `legacy_report.py` 的真实实现。
  - 从 `legacy_report.py` 显式导入同名对象，保持旧路径 re-export。
  - 保留 `_summarize_news_layout()`、`WebTools`、`search_and_extract_news()`、`search_and_extract_news_v2()`、`AiDailyTool` 和缓存逻辑。
- 创建：`tests/test_news_search_legacy_report.py`
  - 锁定新模块轻量导入契约。
  - 锁定 `tool.py` re-export 兼容。
- 修改：`.Codex/plans/news-search-tool-split.md`
  - 执行时勾选本计划步骤，并记录实际验证结果。
- 修改：`docs/todo.md`
  - 代码阶段完成后补充 `news_search/tool.py` 第一刀进展，不勾选整个「超大文件」项。
- 修改：`docs/plan_walkthrough.md`
  - 追加设计、红灯、绿灯、定向回归、全量回归、提交号和后续任务。

## 执行结果摘要

- 红灯：新增两个 `legacy_report` 测试在生产迁移前运行，结果为
  `2 failed, 1 warning in 5.63s`，失败原因是 `legacy_report` 模块不存在。
- 绿灯：`tests/test_news_search_legacy_report.py -v` 结果为
  `2 passed, 1 warning in 0.78s`。
- legacy HTML / layout 定向回归结果为 `4 passed, 1 warning in 0.57s`。
- 搜索与 AI 日报相邻回归结果为 `9 passed, 1 warning in 0.73s`。
- `asyncio.run` 策略测试结果为 `1 passed, 1 warning in 1.74s`。
- `git diff --check` 无输出，退出码为 0。
- 全量测试结果为 `1484 passed, 6 skipped, 139 warnings in 107.20s`。
- 行数变化：`tool.py` 从 1835 行降至 1149 行；`legacy_report.py` 当前 723 行。
- 审查修复：`_parse_news_layout_payload()` 已改为直接使用 `core.json_utils`；
  `legacy_report.py` 导入和 parser 调用均不再加载 `news_search.tool`、`duckduckgo_search`、
  `trafilatura` 或 `BaseTool`。
- 审查修复验证：新模块测试 `3 passed, 1 warning in 0.82s`；`EvolutionUtils`
  兼容测试 `5 passed, 1 warning in 0.46s`；news / AI Daily 相邻回归
  `13 passed, 1 warning in 1.15s`；`asyncio.run` 策略测试
  `1 passed, 1 warning in 2.02s`；`git diff --check` 无输出；全量测试
  `1485 passed, 6 skipped, 139 warnings in 116.15s`。

## 任务 1：补 `legacy_report` 模块红灯测试

**文件：**
- 创建：`tests/test_news_search_legacy_report.py`

- [x] **步骤 1：新增测试文件头部**

创建 `tests/test_news_search_legacy_report.py`：

```python
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
```

- [x] **步骤 2：新增轻量导入红灯测试**

继续写入：

```python
def test_legacy_report_imports_without_runtime_tool_dependencies():
    repo_root = Path(__file__).resolve().parents[1]
    code = r"""
import importlib
import json
import sys

module = importlib.import_module(
    "creatures.nanobot.prompts.skills.news_search.legacy_report"
)
runtime_modules = [
    "duckduckgo_search",
    "trafilatura",
    "kohakuterrarium.modules.tool.base",
]
payload = {
    "has_report": hasattr(module, "_format_news_html_report"),
    "loaded": {name: name in sys.modules for name in runtime_modules},
}
print(json.dumps(payload, sort_keys=True))
if not payload["has_report"] or any(payload["loaded"].values()):
    raise SystemExit(1)
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["has_report"] is True
    assert payload["loaded"] == {
        "duckduckgo_search": False,
        "trafilatura": False,
        "kohakuterrarium.modules.tool.base": False,
    }
```

实现前预期失败：`ModuleNotFoundError: No module named 'creatures.nanobot.prompts.skills.news_search.legacy_report'`。

- [x] **步骤 3：新增旧路径 re-export 红灯测试**

继续写入：

```python
def test_tool_reexports_legacy_report_helpers():
    from creatures.nanobot.prompts.skills.news_search import legacy_report
    from creatures.nanobot.prompts.skills.news_search import tool

    names = [
        "TRUSTED_NEWS_DOMAINS",
        "VALUE_ALERT_KEYWORDS",
        "MODEL_NAME_HINTS",
        "_domain",
        "_source_score",
        "_freshness_score",
        "_combined_score",
        "_value_signal_score",
        "_extract_model_hints",
        "_build_value_alert",
        "_truncate_text",
        "_normalize_summary_text",
        "_escape_md_table_cell",
        "_escape_html",
        "_build_news_conclusion",
        "_build_news_brief_items",
        "_format_news_unavailable_report",
        "_coerce_layout_text",
        "_coerce_layout_list",
        "_parse_news_layout_payload",
        "_specificity_score",
        "_merge_specific_items",
        "_merge_layout_with_fallback",
        "_build_news_layout_fallback",
        "_format_news_html_report",
    ]

    for name in names:
        assert getattr(tool, name) is getattr(legacy_report, name)
```

实现前预期失败：`legacy_report` 模块不存在。

- [x] **步骤 4：运行红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest \
  tests/test_news_search_legacy_report.py::test_legacy_report_imports_without_runtime_tool_dependencies \
  tests/test_news_search_legacy_report.py::test_tool_reexports_legacy_report_helpers \
  -v
```

预期：

```text
FAILED tests/test_news_search_legacy_report.py::test_legacy_report_imports_without_runtime_tool_dependencies
FAILED tests/test_news_search_legacy_report.py::test_tool_reexports_legacy_report_helpers
```

如果失败原因不是模块不存在或 re-export 不存在，先修正测试，不进入生产迁移。

结果：按上述命令运行后得到 `2 failed, 1 warning in 5.63s`，失败原因为
`legacy_report` 模块不存在，符合红灯预期。

## 任务 2：创建 `legacy_report.py` 并迁移纯报告 helper

**文件：**
- 创建：`creatures/nanobot/prompts/skills/news_search/legacy_report.py`
- 修改：`creatures/nanobot/prompts/skills/news_search/tool.py`

- [x] **步骤 1：创建新模块头部**

新建 `creatures/nanobot/prompts/skills/news_search/legacy_report.py`：

```python
"""旧版新闻搜索报告渲染 helper。"""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from core.json_utils import json_repair
```

不要导入 `asyncio`、`DDGS`、`trafilatura`、`BaseTool`、`NewAPIClient`、`run_awaitable_sync` 或项目运行时工具模块；`core.json_utils` 是允许的轻量依赖。

- [x] **步骤 2：迁移报告常量**

从 `tool.py` 移出并原样放入新模块：

```python
TRUSTED_NEWS_DOMAINS = {
    "openai.com", "anthropic.com", "googleblog.com", "deepmind.google", "microsoft.com",
    "aws.amazon.com", "ai.meta.com", "huggingface.co", "arxiv.org", "nature.com",
    "techcrunch.com", "theverge.com", "venturebeat.com", "wired.com", "reuters.com",
    "bloomberg.com", "ft.com", "wsj.com", "bbc.com",
}

VALUE_ALERT_KEYWORDS = {
    "free", "free tier", "open source", "open-source", "cheap", "low cost", "cost-effective",
    "trial", "credit", "coupon", "discount", "benchmark", "price", "pricing", "token", "api",
    "白嫖", "免费", "开源", "低价", "便宜", "优惠", "试用", "赠金", "充值", "调用价格", "token价格",
}

MODEL_NAME_HINTS = {
    "qwen", "deepseek", "kimi", "gpt", "claude", "gemini", "llama", "mistral", "hunyuan", "glm",
    "通义", "豆包", "混元", "智谱", "阶跃", "minimax",
}
```

- [x] **步骤 3：迁移评分和价值信号 helper**

从 `tool.py` 移出并原样放入新模块：

- `_domain()`
- `_source_score()`
- `_freshness_score()`
- `_combined_score()`
- `_value_signal_score()`
- `_extract_model_hints()`
- `_build_value_alert()`

迁移后把类型标注统一为内置泛型，例如 `dict[str, Any]`、`list[str]`。不要改变函数体逻辑。

- [x] **步骤 4：迁移文本和 HTML helper**

从 `tool.py` 移出并原样放入新模块：

- `_truncate_text()`
- `_normalize_summary_text()`
- `_escape_md_table_cell()`
- `_escape_html()`
- `_build_news_conclusion()`
- `_build_news_brief_items()`
- `_format_news_unavailable_report()`

迁移时保持 HTML/CSS 字符串完全一致，不重排文案，不调整颜色或 class 名。

- [x] **步骤 5：迁移 layout 解析和 fallback helper**

从 `tool.py` 移出并原样放入新模块：

- `_coerce_layout_text()`
- `_coerce_layout_list()`
- `_parse_news_layout_payload()`
- `_specificity_score()`
- `_merge_specific_items()`
- `_merge_layout_with_fallback()`
- `_build_news_layout_fallback()`

不要迁移 `_summarize_news_layout()`；它继续留在 `tool.py`，作为旧 monkeypatch 目标和 LLM 调用边界。

- [x] **步骤 6：迁移最终 HTML 渲染 helper**

从 `tool.py` 移出并原样放入新模块：

- `_format_news_html_report()`

确保新模块已从 `datetime` 导入 `datetime`，因为该函数会渲染当前时间。

- [x] **步骤 7：在 `tool.py` re-export 迁移对象**

在 `tool.py` 的导入区加入：

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

之后删除 `tool.py` 中被迁移对象的真实定义。不要删除 `_summarize_news_layout()`、`_heuristic_should_deepen()` 或 `_model_should_deepen()`。

- [x] **步骤 8：清理 `tool.py` 未用 import**

迁移后检查 `tool.py` 的 import：

- 如果 `html` 只被迁移 helper 使用，从 `tool.py` 删除。
- 如果 `urlparse` 只被 `_domain()` 使用，从 `tool.py` 删除。
- 保留 `json`，因为 `_extract_json_object()` 等 evidence bridge 仍使用。
- 保留 `re`，因为 RSS、query routing 和 JSON 抽取仍使用。
- 保留 `datetime`、`timedelta`、`timezone`，因为搜索后端日期处理仍使用。

用 `python -m compileall creatures/nanobot/prompts/skills/news_search -q` 检查语法。

结果：已创建 `legacy_report.py` 并保留 `tool.py` facade；`compileall` 无输出，退出码为 0。

## 任务 3：验证红绿灯和相邻行为

**文件：**
- 修改：`.Codex/plans/news-search-tool-split.md`

- [x] **步骤 1：运行新模块绿灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/test_news_search_legacy_report.py -v
```

预期：

```text
2 passed
```

结果：`2 passed, 1 warning in 0.78s`。

- [x] **步骤 2：运行 legacy HTML 和 layout 定向回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest \
  tests/test_tools_package.py::test_parse_news_layout_payload_accepts_small_json_schema \
  tests/test_tools_package.py::test_merge_layout_with_fallback_backfills_specific_models_and_more_items \
  tests/test_tools_package.py::test_combined_news_tool_renders_fixed_html_template_from_structured_layout \
  tests/test_tools_package.py::test_combined_news_tool_returns_unavailable_html_when_search_backends_fail \
  -v
```

预期：4 个用例全部通过。

结果：`4 passed, 1 warning in 0.57s`。

- [x] **步骤 3：运行搜索和 AI 日报相邻回归**

运行：

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

预期：所有列出的测试通过。

结果：`9 passed, 1 warning in 0.73s`。

- [x] **步骤 4：运行 asyncio 策略测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/test_asyncio_run_policy.py::test_asyncio_run_only_appears_under_main_guard -v
```

预期：

```text
1 passed
```

结果：`1 passed, 1 warning in 1.74s`。

- [x] **步骤 5：运行全量测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

预期：0 failures。

结果：`1484 passed, 6 skipped, 139 warnings in 107.20s`。

## 任务 4：同步文档并提交阶段

**文件：**
- 修改：`.Codex/plans/news-search-tool-split.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [x] **步骤 1：更新计划复选框**

把本计划中已经执行的步骤改为 `[x]`，并在每个验证步骤下补充实际输出摘要，例如：

```markdown
结果：`2 passed, 1 warning in 0.42s`。
```

- [x] **步骤 2：更新 `docs/todo.md`**

在「超大文件 >800 行拆分」条目下追加一条进展：

```markdown
  - 进展：`creatures/nanobot/prompts/skills/news_search/tool.py` 第一刀已拆出
    旧版新闻报告 helper 到 `news_search/legacy_report.py`；搜索后端、AI 日报工具、
    缓存和 `_summarize_news_layout()` 仍留在旧文件，后续继续拆分。
```

不要勾选整个「超大文件 >800 行拆分」条目。

- [x] **步骤 3：更新 `docs/plan_walkthrough.md`**

在 `docs/plan_walkthrough.md` 末尾的
`2026-06-21 news_search/tool.py 第一刀拆分` 小节中补充：

- 红灯输出。
- 绿灯输出。
- 定向回归输出。
- `asyncio.run` 策略测试输出。
- 全量测试输出。
- 行数变化。
- 提交号。

- [x] **步骤 4：检查格式和 diff**

运行：

```bash
git diff --check
```

预期没有输出，退出码为 0。

运行：

```bash
git status --short
```

只暂存本阶段相关文件，不暂存 pycache、`nanobot.db`、`docs/goal.md` 或其他既有脏项。

- [x] **步骤 5：显式暂存并提交**

运行：

```bash
git add \
  creatures/nanobot/prompts/skills/news_search/legacy_report.py \
  creatures/nanobot/prompts/skills/news_search/tool.py \
  tests/test_news_search_legacy_report.py \
  .Codex/plans/news-search-tool-split.md \
  docs/todo.md \
  docs/plan_walkthrough.md
git commit -m "refactor(新闻搜索): 拆分旧版报告渲染"
```

不要使用 `git add .` 或 `git add -A`。

## 任务 5：审查反馈修复

**文件：**
- 创建：`core/json_utils.py`
- 修改：`core/legacy_adapter.py`
- 修改：`creatures/nanobot/prompts/skills/news_search/legacy_report.py`
- 修改：`tests/test_news_search_legacy_report.py`
- 修改：`docs/superpowers/specs/2026-06-21-news-search-tool-split-design.md`
- 修改：`.Codex/plans/news-search-tool-split.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [x] **步骤 1：补 parser 轻量依赖红灯测试**

新增 `test_parse_layout_payload_does_not_import_runtime_tool_dependencies`，在全新 Python
进程中导入 `legacy_report` 并调用 `_parse_news_layout_payload()`，断言不会加载
`news_search.tool`、`duckduckgo_search`、`trafilatura` 或 `BaseTool`。

结果：实现修复前该测试失败，stdout 显示四个 runtime 模块均被加载。

- [x] **步骤 2：抽出轻量 JSON helper**

新增 `core/json_utils.py`，迁移 `EvolutionUtils.json_repair()` 原有容错 JSON 解析逻辑；
`EvolutionUtils.json_repair()` 保持旧方法签名并代理到轻量 helper。

- [x] **步骤 3：改造 `legacy_report.py` parser 依赖**

`_parse_news_layout_payload()` 直接调用 `core.json_utils.json_repair()`，不再从
`core.legacy_adapter` 导入 `EvolutionUtils`。同时清理迁移后未使用的 `json` 导入和
旧式 `Dict/List` 标注。

- [x] **步骤 4：收窄兼容说明**

设计文档、实现计划和 walkthrough 明确：旧 `tool.py` 路径保证导入兼容和顶层运行入口
patch 兼容；迁移后的报告内部 helper 如需替换内部依赖，应 patch `legacy_report` 路径。

- [x] **步骤 5：验证审查修复**

验证结果：

- `tests/test_news_search_legacy_report.py -v`：`3 passed, 1 warning in 0.82s`。
- `tests/test_audit_fixes.py::TestEvolutionUtils -v`：`5 passed, 1 warning in 0.46s`。
- news / AI Daily 相邻回归：`13 passed, 1 warning in 1.15s`。
- `tests/test_asyncio_run_policy.py::test_asyncio_run_only_appears_under_main_guard -v`：
  `1 passed, 1 warning in 2.02s`。
- `git diff --check`：无输出，退出码为 0。
- `python -m pytest tests/ -v`：`1485 passed, 6 skipped, 139 warnings in 116.15s`。

提交信息：`fix(新闻搜索): 隔离报告解析轻量依赖`。
