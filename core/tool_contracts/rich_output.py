"""富 HTML 工具终结结果的框架无关 wire contract。"""

from __future__ import annotations

import json


RICH_OUTPUT_MARKER = "NANOBOT_RICH_OUTPUT"
RICH_REPORT_TOOLS = {
    "ai_daily": "ai_daily",
    "group_analysis": "group_analysis",
}
RICH_REPORT_HTML_MARKERS = {
    "ai_daily": "news-brief",
    "group_analysis": "group-analysis-report",
}


def build_rich_output(html: str, *, report_kind: str) -> str:
    kind = str(report_kind or "").strip()
    if kind not in RICH_REPORT_TOOLS:
        raise ValueError(f"Unsupported rich report kind: {kind}")
    content = str(html or "").strip()
    if not content:
        raise ValueError("Rich terminal HTML must not be empty")
    return json.dumps(
        {
            RICH_OUTPUT_MARKER: {
                "version": 1,
                "report_kind": kind,
                "content_type": "text/html",
                "html": content,
            }
        },
        ensure_ascii=False,
    )


__all__ = [
    "RICH_OUTPUT_MARKER",
    "RICH_REPORT_HTML_MARKERS",
    "RICH_REPORT_TOOLS",
    "build_rich_output",
]
