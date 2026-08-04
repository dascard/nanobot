"""图片摘要工具的框架无关输入与输出编排。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.error
from collections.abc import Callable
from typing import Any

from core.tool_contracts.result import ToolServiceResult


logger = logging.getLogger("nanobot.app.tool.image_summary")
SummaryCallable = Callable[[list[str], str], str]


def normalize_image_files(files: Any) -> list[str]:
    if not isinstance(files, list):
        return []
    normalized: list[str] = []
    for file in files:
        if not isinstance(file, str):
            continue
        item = file.strip()
        if item and item not in normalized:
            normalized.append(item)
    return normalized


def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(
            r"^```(?:json)?\s*",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return cleaned.strip()


def parse_image_summary_payload(text: str) -> dict[str, Any]:
    cleaned = _strip_code_fences(text)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        parsed = json.loads(cleaned[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("模型未返回可解析的 JSON")


def extract_completion_content(body: dict[str, Any]) -> str:
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content")
    finish_reason = choice.get("finish_reason") or choice.get(
        "native_finish_reason"
    )
    reasoning = message.get("reasoning")

    if isinstance(content, str) and content.strip():
        return content

    reasoning_preview = str(reasoning or "")[:300]
    if finish_reason == "length":
        raise ValueError(
            "truncated_empty_content: "
            f"finish_reason={finish_reason}, "
            f"has_reasoning={bool(reasoning)}, "
            f"reasoning_preview={reasoning_preview}"
        )
    if reasoning:
        raise ValueError(
            "reasoning_only_empty_content: "
            f"finish_reason={finish_reason}, "
            "has_reasoning=true, "
            f"reasoning_preview={reasoning_preview}"
        )
    raise ValueError(
        "empty_message_content: "
        f"finish_reason={finish_reason}, has_reasoning=false"
    )


def normalize_image_summary_payload(
    payload: dict[str, Any],
    *,
    image_count: int,
) -> dict[str, Any]:
    normalized = dict(payload)
    normalized.setdefault("overall_summary", "")
    normalized["image_count"] = int(
        normalized.get("image_count") or image_count
    )
    for key in ("per_image", "keywords", "risk_flags"):
        if not isinstance(normalized.get(key), list):
            normalized[key] = []
    if not isinstance(normalized.get("confidence"), str):
        normalized["confidence"] = "medium"
    normalized.setdefault("confidence", "medium")
    return normalized


async def execute_image_summary(
    args: dict[str, Any],
    *,
    summarize: SummaryCallable,
) -> ToolServiceResult:
    files = normalize_image_files(args.get("files"))
    focus = str(args.get("focus", "")).strip()
    logger.warning(
        "[image_summary] input files=%r focus=%s",
        files,
        focus,
    )
    if not files:
        return ToolServiceResult(error="Missing 'files' argument")

    try:
        raw_content = await asyncio.to_thread(
            summarize,
            files,
            focus,
        )
        parsed = normalize_image_summary_payload(
            parse_image_summary_payload(raw_content),
            image_count=len(files),
        )
        return ToolServiceResult(
            output=json.dumps(parsed, ensure_ascii=False),
            exit_code=0,
        )
    except urllib.error.URLError as exc:
        logger.error(
            "[image_summary] Qwen request failed: %s",
            exc,
            exc_info=True,
        )
        return ToolServiceResult(
            error=f"Image summary failed: {exc}"
        )
    except Exception as exc:
        logger.error(
            "[image_summary] Failed: %s",
            exc,
            exc_info=True,
        )
        return ToolServiceResult(
            error=f"Image summary failed: {exc}"
        )


__all__ = [
    "SummaryCallable",
    "execute_image_summary",
    "extract_completion_content",
    "normalize_image_files",
    "normalize_image_summary_payload",
    "parse_image_summary_payload",
]
