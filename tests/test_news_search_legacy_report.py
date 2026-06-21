from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


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
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(repo_root)
        if not existing_pythonpath
        else f"{repo_root}{os.pathsep}{existing_pythonpath}"
    )
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


def test_parse_layout_payload_does_not_import_runtime_tool_dependencies():
    repo_root = Path(__file__).resolve().parents[1]
    code = r"""
import importlib
import json
import sys

module = importlib.import_module(
    "creatures.nanobot.prompts.skills.news_search.legacy_report"
)
parsed = module._parse_news_layout_payload(
    '{"title":"AI 今日速报","summary":"OpenAI 发布新模型","highlights":["GPT-5 API 降价"]}'
)
runtime_modules = [
    "creatures.nanobot.prompts.skills.news_search.tool",
    "duckduckgo_search",
    "trafilatura",
    "kohakuterrarium.modules.tool.base",
]
payload = {
    "title": parsed.get("title"),
    "summary": parsed.get("summary"),
    "loaded": {name: name in sys.modules for name in runtime_modules},
}
print(json.dumps(payload, sort_keys=True, ensure_ascii=False))
if payload["title"] != "AI 今日速报" or any(payload["loaded"].values()):
    raise SystemExit(1)
"""
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(repo_root)
        if not existing_pythonpath
        else f"{repo_root}{os.pathsep}{existing_pythonpath}"
    )
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
    assert payload["title"] == "AI 今日速报"
    assert payload["summary"] == "OpenAI 发布新模型"
    assert payload["loaded"] == {
        "creatures.nanobot.prompts.skills.news_search.tool": False,
        "duckduckgo_search": False,
        "trafilatura": False,
        "kohakuterrarium.modules.tool.base": False,
    }
