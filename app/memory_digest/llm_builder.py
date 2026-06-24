"""MemoryDigest LLM 主生成器。"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from collections.abc import Iterable

from core.database import ChatLog
from core.prompt_v2.section_renderer import sha256_text
from core.prompt_v2.template_loader import load_template
from core.prompt_v2.template_registry import runtime_template_dir
from core.prompt_v2.variables import render_scoped_template

from .builder import MemoryDigestBuildResult, MemoryDigestBuilder
from .quality import build_quality
from .renderer import render_digest_levels

logger = logging.getLogger("nanobot.memory_digest.llm")

_URL_RE = re.compile(r"https?://[^\s，。！？；、)）\]>]+|www\.[^\s，。！？；、)）\]>]+", re.IGNORECASE)
_MAX_SOURCE_LINES = 80
_MIN_LLM_QUALITY = 0.75
_CARD_MAX_CHARS = 120


@dataclass(frozen=True)
class _LlmDigestContext:
    fallback: MemoryDigestBuildResult
    source_rows: list[dict[str, Any]]
    messages: list[dict[str, str]]
    prompt_meta: dict[str, Any]

_GENERIC_CARD_PATTERNS: list[re.Pattern] = [
    re.compile(r"^今天.*讨论"),
    re.compile(r"^用户希望.*更好"),
    re.compile(r"^需要优化"),
    re.compile(r"^本次(对话|讨论|会话).*围绕"),
    re.compile(r"^需要进一步.*(优化|改进|完善)"),
    re.compile(r"^讨论了.*相关"),
    re.compile(r"^用户.*(提出|询问|想知道)"),
    re.compile(r"^系统.*(应该|需要|可以)"),
]


def _is_generic_card_text(text: str) -> bool:
    """用正则检测泛化/无信息量的 card 文本。"""
    t = text.strip()
    if not t:
        return True
    for pat in _GENERIC_CARD_PATTERNS:
        if pat.search(t):
            return True
    return False


def _has_keyword_overlap(text: str, source_text: str, *, min_overlap: int = 2) -> bool:
    """检查 card 文本与 source 是否有基本词面重合。

    中文用双字词组，英文用 3+ 字母单词。
    """
    cn_chars = re.findall(r"[一-鿿]{2,}", text)
    if cn_chars:
        src_chars = set(re.findall(r"[一-鿿]{2,}", source_text))
        overlap = sum(1 for c in cn_chars if c in src_chars)
        if overlap >= min_overlap:
            return True

    en_words = set(re.findall(r"[a-zA-Z_]{3,}", text.lower()))
    if en_words:
        src_words = set(re.findall(r"[a-zA-Z_]{3,}", source_text.lower()))
        if len(en_words & src_words) >= min_overlap:
            return True

    return False
_SYSTEM_TEMPLATE_KEY = "tasks/memory_digest_system"
_USER_TEMPLATE_KEY = "tasks/memory_digest_user"

FALLBACK_MEMORY_DIGEST_SYSTEM_PROMPT = "生成长期记忆摘要，只输出严格 JSON。"
FALLBACK_MEMORY_DIGEST_USER_PROMPT = (
    "根据 digest_source 输出 preview、long_summary、recall_cards、quality。"
)


def _safe_json_loads(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("json_parse_failed:empty_response")
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        match = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)```", text)
        if not match:
            raise ValueError("json_parse_failed")
        value = json.loads(match.group(1).strip())
    if not isinstance(value, dict):
        raise ValueError("json_schema_invalid:root_not_object")
    return value


def _close_awaitable(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        close()


def _call_summarizer(summarizer: Callable[[list[dict[str, str]]], Any], messages: list[dict[str, str]]) -> Any:
    result = summarizer(messages)
    if inspect.isawaitable(result):
        _close_awaitable(result)
        raise TypeError("sync_summarizer_returned_awaitable: use build_memory_digest_with_llm_async")
    return result


async def _call_summarizer_async(
    summarizer: Callable[[list[dict[str, str]]], Any],
    messages: list[dict[str, str]],
) -> Any:
    result = summarizer(messages)
    if inspect.isawaitable(result):
        return await result
    return result


def _as_str(value: Any, *, max_chars: int = 500) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def _as_str_list(value: Any, *, limit: int, item_chars: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _as_str(item, max_chars=item_chars)
        if text:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _as_int_list(value: Any, *, limit: int = 12) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
        if len(result) >= limit:
            break
    return result


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _source_id(*, session_id: str, digest_date: str, source_rows: list[dict[str, Any]]) -> str:
    ids = [int(row["log_id"]) for row in source_rows if row.get("log_id")]
    start = min(ids) if ids else 0
    end = max(ids) if ids else 0
    raw = f"{digest_date}|{session_id}|{start}|{end}|memory_digest_v2"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _source_range(source_rows: list[dict[str, Any]]) -> str:
    ids = [int(row["log_id"]) for row in source_rows if row.get("log_id")]
    if not ids:
        return "log_id 0-0"
    return f"log_id {min(ids)}-{max(ids)}"


def _collect_source_rows(logs: list[ChatLog]) -> list[dict[str, Any]]:
    builder = MemoryDigestBuilder()
    seen_short: set[str] = set()
    rows: list[dict[str, Any]] = []
    for log in logs:
        if builder._skip_reason(log, seen_short):
            continue
        rows.append(builder._format_valid_log(log))
        if len(rows) >= _MAX_SOURCE_LINES:
            break
    return rows


def _template_source(path: Any) -> str:
    try:
        return "runtime" if str(path).startswith(str(runtime_template_dir())) else "default"
    except Exception:
        return "default"


def _render_digest_template(template_key: str, values: dict[str, Any], fallback: str) -> tuple[str, dict[str, Any]]:
    try:
        template = load_template(template_key)
        frontmatter = template.frontmatter or {}
        if str(frontmatter.get("tool_name") or "") != "memory_digest":
            raise ValueError("template_tool_name_mismatch")
        if str(frontmatter.get("kind") or "") not in {"task", "tool"}:
            raise ValueError("template_kind_invalid")
        rendered = render_scoped_template(template.prompt_key, template.body, values).strip()
        if not rendered:
            raise ValueError("template_rendered_empty")
        return rendered, {
            "key": template.prompt_key,
            "path": str(template.path),
            "source": _template_source(template.path),
            "sha256": sha256_text(rendered),
            "version": frontmatter.get("version", ""),
        }
    except Exception as exc:
        logger.warning("memory digest prompt template fallback: key=%s error=%s", template_key, exc)
        text = fallback.strip()
        return text, {
            "key": template_key,
            "path": "",
            "source": "fallback",
            "sha256": sha256_text(text),
            "version": "",
        }


def build_llm_digest_messages(
    *,
    session_id: str,
    digest_date: str,
    fallback: MemoryDigestBuildResult,
    source_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    source_text = "\n".join(str(row.get("line") or "").strip() for row in source_rows if row.get("line"))
    fallback_hint = {
        "preview": fallback.meta.get("preview") or {},
        "long_summary": fallback.meta.get("long_summary") or {},
        "recall_cards": fallback.meta.get("recall_cards") or [],
    }
    sid = _source_id(session_id=session_id, digest_date=digest_date, source_rows=source_rows)
    values = {
        "date": digest_date,
        "session_id": session_id,
        "source_id": sid,
        "source_type": "date_session",
        "source_range": _source_range(source_rows),
        "message_count": str(len(source_rows)),
        "digest_source": source_text,
        "existing_digest_hint": json.dumps(fallback_hint, ensure_ascii=False),
    }
    system_prompt, system_meta = _render_digest_template(
        _SYSTEM_TEMPLATE_KEY,
        values,
        FALLBACK_MEMORY_DIGEST_SYSTEM_PROMPT,
    )
    user_prompt, user_meta = _render_digest_template(
        _USER_TEMPLATE_KEY,
        values,
        (
            FALLBACK_MEMORY_DIGEST_USER_PROMPT
            + "\n\n<digest_source>\n"
            + source_text
            + "\n</digest_source>"
        ),
    )
    prompt_meta = {
        "template": f"{_SYSTEM_TEMPLATE_KEY} + {_USER_TEMPLATE_KEY}",
        "system_key": system_meta["key"],
        "system_path": system_meta["path"],
        "system_source": system_meta["source"],
        "system_sha256": system_meta["sha256"],
        "system_version": system_meta["version"],
        "user_key": user_meta["key"],
        "user_path": user_meta["path"],
        "user_source": user_meta["source"],
        "user_sha256": user_meta["sha256"],
        "user_version": user_meta["version"],
        "source_id": sid,
        "source_type": "date_session",
        "source_range": values["source_range"],
        "message_count": len(source_rows),
    }
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ], prompt_meta


async def default_llm_memory_digest_summarizer_async(messages: list[dict[str, str]]) -> str:
    from config import NEW_API_KEY
    from clients.new_api_client import NewAPIClient
    from clients.classifier_client import resolve_model_route

    route = resolve_model_route("memory_digest")
    client = NewAPIClient(
        api_key=route.get("api_key") or NEW_API_KEY,
        base_url=route.get("base_url") or "",
    )
    response = await client.chat_completion(
        messages=messages,
        temperature=float(route.get("temperature", 0.1)),
        manual_model=route.get("model", ""),
        max_tokens=int(route.get("max_tokens", 1800)),
        llm_source="memory_digest",
        enable_thinking=route.get("enable_thinking", "false"),
    )
    if isinstance(response, dict) and response.get("error"):
        raise RuntimeError(str(response.get("detail") or response.get("error")))
    try:
        return str(response["choices"][0]["message"].get("content") or "")
    except Exception as exc:
        raise RuntimeError("llm_response_missing_content") from exc


def default_llm_memory_digest_summarizer(messages: list[dict[str, str]]) -> str:
    raise RuntimeError(
        "sync_summarizer_required: use default_llm_memory_digest_summarizer_async "
        "with build_memory_digest_with_llm_async"
    )


def parse_llm_digest_response(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    return _safe_json_loads(str(raw or ""))


def _normalize_llm_meta(
    payload: dict[str, Any],
    *,
    fallback: MemoryDigestBuildResult,
    session_id: str,
    digest_date: str,
    user_id: str,
    prompt_meta: dict[str, Any],
) -> dict[str, Any]:
    raw_preview = payload.get("preview")
    raw_long_summary = payload.get("long_summary")
    preview = raw_preview if isinstance(raw_preview, dict) else {"brief": raw_preview}
    long_summary = raw_long_summary if isinstance(raw_long_summary, dict) else {"topic_flow": raw_long_summary}
    quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}

    cards: list[dict[str, Any]] = []
    raw_cards = payload.get("recall_cards") if isinstance(payload.get("recall_cards"), list) else []
    for index, item in enumerate(raw_cards[:12], start=1):
        raw_card = item if isinstance(item, dict) else {"text": item}
        text = _as_str(raw_card.get("text"), max_chars=180)
        if not text:
            continue
        cards.append({
            "card_id": _as_str(raw_card.get("card_id"), max_chars=40) or f"card_{index}",
            "type": _as_str(raw_card.get("type"), max_chars=40) or "recall_card",
            "text": text,
            "keywords": _as_str_list(raw_card.get("keywords"), limit=8, item_chars=40),
            "importance": max(0.0, min(1.0, _as_float(raw_card.get("importance"), default=0.7))),
            "evidence_log_ids": _as_int_list(raw_card.get("evidence_log_ids"), limit=12),
        })

    score = _as_float(quality.get("score"), default=0.0)
    issues = _as_str_list(quality.get("issues"), limit=10, item_chars=80)
    reason = _as_str(quality.get("reason"), max_chars=220)
    meta = {
        **fallback.meta,
        "schema_version": 2,
        "status": "active",
        "digest_date": digest_date,
        "session_id": session_id,
        "user_id": user_id,
        "source_id": prompt_meta.get("source_id", ""),
        "source_type": prompt_meta.get("source_type", "date_session"),
        "source_range": fallback.meta.get("source_range") or prompt_meta.get("source_range", ""),
        "cleaned_source_range": prompt_meta.get("source_range", ""),
        "message_count": int(prompt_meta.get("message_count") or 0),
        "generator": "llm",
        "llm_status": "success",
        "llm_model": "async_llm",
        "prompt_template": prompt_meta.get("template", ""),
        "prompt_version": {
            "system_key": prompt_meta.get("system_key", ""),
            "system_source": prompt_meta.get("system_source", ""),
            "system_sha256": prompt_meta.get("system_sha256", ""),
            "system_version": prompt_meta.get("system_version", ""),
            "user_key": prompt_meta.get("user_key", ""),
            "user_source": prompt_meta.get("user_source", ""),
            "user_sha256": prompt_meta.get("user_sha256", ""),
            "user_version": prompt_meta.get("user_version", ""),
        },
        "fallback_reason": None,
        "preview": {
            "brief": _as_str(preview.get("brief"), max_chars=220),
            "keywords": _as_str_list(preview.get("keywords"), limit=12, item_chars=40),
            "participants": _as_str_list(preview.get("participants"), limit=12, item_chars=40),
        },
        "long_summary": {
            "topic_flow": _as_str(long_summary.get("topic_flow"), max_chars=900),
            "important_details": _as_str_list(long_summary.get("important_details"), limit=20, item_chars=220),
            "conclusions": _as_str_list(long_summary.get("conclusions"), limit=12, item_chars=180),
            "open_loops": _as_str_list(long_summary.get("open_loops"), limit=12, item_chars=180),
        },
        "recall_cards": cards,
        "quality": {
            **build_quality(score=score, issues=issues, should_inject_preview=True),
            "reason": reason,
        },
    }
    return meta


def audit_llm_digest_meta(
    meta: dict[str, Any],
    *,
    source_rows: list[dict[str, Any]] | None = None,
) -> tuple[bool, list[str]]:
    """审计 LLM 生成的 digest meta，验证 card 是否 grounded 于 source。

    source_rows 为可选参数——传入时启用 evidence / keyword overlap 检查。
    """
    issues: list[str] = []
    if str(meta.get("status") or "") != "active":
        issues.append("status_not_active")

    preview = meta.get("preview") if isinstance(meta.get("preview"), dict) else {}
    long_summary = meta.get("long_summary") if isinstance(meta.get("long_summary"), dict) else {}
    cards = meta.get("recall_cards") if isinstance(meta.get("recall_cards"), list) else []

    if not str(preview.get("brief") or "").strip():
        issues.append("preview_brief_empty")
    if not str(long_summary.get("topic_flow") or "").strip():
        issues.append("topic_flow_empty")
    if not cards:
        issues.append("recall_cards_empty")

    quality = meta.get("quality") if isinstance(meta.get("quality"), dict) else {}
    try:
        score = float(quality.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    if score < _MIN_LLM_QUALITY:
        issues.append("quality_score_below_threshold")
    quality_issues = quality.get("issues") if isinstance(quality.get("issues"), list) else []
    if quality_issues:
        issues.append("quality_issues_present")

    if _URL_RE.search(json.dumps({
        "preview": preview,
        "long_summary": long_summary,
        "recall_cards": cards,
    }, ensure_ascii=False)):
        issues.append("contains_url")

    # 构建 source 上下文（用于 grounded 检查）
    source_log_ids: set[int] = set()
    source_text: str = ""
    if source_rows:
        for row in source_rows:
            lid = row.get("log_id")
            if lid is not None:
                source_log_ids.add(int(lid))
        source_text = "\n".join(
            str(row.get("line") or "").strip() for row in source_rows
        )

    for card in cards:
        text = str(card.get("text") if isinstance(card, dict) else card or "").strip()
        if len(text) > _CARD_MAX_CHARS:
            issues.append("recall_card_too_long")

        # evidence_log_ids 必须存在于 source
        evidence_ids = card.get("evidence_log_ids") if isinstance(card, dict) else []
        if isinstance(evidence_ids, list) and evidence_ids and source_log_ids:
            for eid in evidence_ids:
                try:
                    if int(eid) not in source_log_ids:
                        issues.append("recall_card_evidence_not_in_source")
                        break
                except (TypeError, ValueError):
                    issues.append("recall_card_evidence_not_in_source")
                    break

        # 无 evidence → 检查词面重合
        if (not isinstance(evidence_ids, list) or not evidence_ids) and source_text:
            if not _has_keyword_overlap(text, source_text):
                issues.append("recall_card_not_grounded")

        # 泛化句检测（正则）
        if _is_generic_card_text(text):
            issues.append("recall_card_too_generic")

        # 路径/栈帧
        if re.search(r"(/[\w.-]+){2,}|[A-Za-z]:\\|[\w.-]+\.py:\d+", text):
            issues.append("recall_card_contains_log_path")

    return not issues, issues


def _fallback_result(
    fallback: MemoryDigestBuildResult,
    *,
    status: str,
    error: str = "",
    prompt_meta: dict[str, Any] | None = None,
) -> MemoryDigestBuildResult:
    prompt_meta = prompt_meta or {}
    meta = {
        **fallback.meta,
        "source_id": prompt_meta.get("source_id") or fallback.meta.get("source_id") or "",
        "source_type": prompt_meta.get("source_type") or fallback.meta.get("source_type") or "date_session",
        "source_range": fallback.meta.get("source_range") or prompt_meta.get("source_range", ""),
        "cleaned_source_range": prompt_meta.get("source_range", ""),
        "message_count": int(prompt_meta.get("message_count") or fallback.meta.get("source_stats", {}).get("valid_log_count") or 0),
        "generator": "deterministic_fallback",
        "llm_status": status,
        "llm_model": "",
        "prompt_template": prompt_meta.get("template", ""),
        "prompt_version": {
            "system_key": prompt_meta.get("system_key", ""),
            "system_source": prompt_meta.get("system_source", ""),
            "system_sha256": prompt_meta.get("system_sha256", ""),
            "system_version": prompt_meta.get("system_version", ""),
            "user_key": prompt_meta.get("user_key", ""),
            "user_source": prompt_meta.get("user_source", ""),
            "user_sha256": prompt_meta.get("user_sha256", ""),
            "user_version": prompt_meta.get("user_version", ""),
        },
        "fallback_reason": error[:500] if error else None,
    }
    if error:
        meta["llm_error"] = error[:500]
    return MemoryDigestBuildResult(
        status=fallback.status,
        meta=meta,
        level_contents=render_digest_levels(meta),
    )


def _prepare_llm_digest_context(
    *,
    user_id: str,
    session_id: str,
    digest_date: str,
    logs: Iterable[ChatLog],
    llm_enabled: bool,
) -> tuple[_LlmDigestContext | None, MemoryDigestBuildResult | None]:
    log_rows = list(logs)
    fallback = MemoryDigestBuilder().build(
        user_id=user_id,
        session_id=session_id,
        digest_date=digest_date,
        logs=log_rows,
    )
    if not llm_enabled:
        return None, _fallback_result(fallback, status="disabled")
    if fallback.status != "active":
        return None, _fallback_result(fallback, status="skipped")

    source_rows = _collect_source_rows(log_rows)
    if not source_rows:
        return None, _fallback_result(fallback, status="skipped", error="source_rows_empty")

    messages, prompt_meta = build_llm_digest_messages(
        session_id=session_id,
        digest_date=digest_date,
        fallback=fallback,
        source_rows=source_rows,
    )
    return _LlmDigestContext(
        fallback=fallback,
        source_rows=source_rows,
        messages=messages,
        prompt_meta=prompt_meta,
    ), None


def _build_memory_digest_result_from_raw(
    raw: Any,
    *,
    context: _LlmDigestContext,
    session_id: str,
    digest_date: str,
    user_id: str,
) -> MemoryDigestBuildResult:
    payload = parse_llm_digest_response(raw)
    meta = _normalize_llm_meta(
        payload,
        fallback=context.fallback,
        session_id=session_id,
        digest_date=digest_date,
        user_id=user_id,
        prompt_meta=context.prompt_meta,
    )
    audit_ok, issues = audit_llm_digest_meta(meta, source_rows=context.source_rows)
    if not audit_ok:
        return _fallback_result(
            context.fallback,
            status="fallback",
            error=",".join(issues),
            prompt_meta=context.prompt_meta,
        )
    return MemoryDigestBuildResult(
        status="active",
        meta=meta,
        level_contents=render_digest_levels(meta),
    )


def build_memory_digest_with_llm(
    *,
    user_id: str,
    session_id: str,
    digest_date: str,
    logs: Iterable[ChatLog],
    summarizer: Callable[[list[dict[str, str]]], Any] | None = None,
    llm_enabled: bool = True,
) -> MemoryDigestBuildResult:
    context, early_result = _prepare_llm_digest_context(
        user_id=user_id,
        session_id=session_id,
        digest_date=digest_date,
        logs=logs,
        llm_enabled=llm_enabled,
    )
    if early_result is not None:
        return early_result
    assert context is not None
    if summarizer is None:
        return _fallback_result(
            context.fallback,
            status="fallback",
            error="sync_summarizer_required: use build_memory_digest_with_llm_async",
            prompt_meta=context.prompt_meta,
        )
    try:
        raw = _call_summarizer(summarizer, context.messages)
        return _build_memory_digest_result_from_raw(
            raw,
            context=context,
            session_id=session_id,
            digest_date=digest_date,
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning("memory digest llm fallback: session_id=%s date=%s error=%s", session_id, digest_date, exc)
        return _fallback_result(context.fallback, status="fallback", error=str(exc), prompt_meta=context.prompt_meta)


async def build_memory_digest_with_llm_async(
    *,
    user_id: str,
    session_id: str,
    digest_date: str,
    logs: Iterable[ChatLog],
    summarizer: Callable[[list[dict[str, str]]], Any] | None = None,
    llm_enabled: bool = True,
) -> MemoryDigestBuildResult:
    context, early_result = _prepare_llm_digest_context(
        user_id=user_id,
        session_id=session_id,
        digest_date=digest_date,
        logs=logs,
        llm_enabled=llm_enabled,
    )
    if early_result is not None:
        return early_result
    assert context is not None
    summarizer = summarizer or default_llm_memory_digest_summarizer_async
    try:
        raw = await _call_summarizer_async(summarizer, context.messages)
        return _build_memory_digest_result_from_raw(
            raw,
            context=context,
            session_id=session_id,
            digest_date=digest_date,
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning("memory digest llm fallback: session_id=%s date=%s error=%s", session_id, digest_date, exc)
        return _fallback_result(context.fallback, status="fallback", error=str(exc), prompt_meta=context.prompt_meta)
