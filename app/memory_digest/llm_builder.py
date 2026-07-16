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
from core.prompt_v2.task_contracts import TaskCallValueError
from core.prompt_v2.task_templates import TaskInvocationError, render_task_pair

from .builder import MemoryDigestBuildResult, MemoryDigestBuilder
from .quality import build_quality
from .renderer import render_digest_levels

logger = logging.getLogger("nanobot.memory_digest.llm")

_URL_RE = re.compile(r"https?://[^\s，。！？；、)）\]>]+|www\.[^\s，。！？；、)）\]>]+", re.IGNORECASE)
_CREDENTIAL_RE = re.compile(
    r"\b(?:api[_-]?key|token|password|passwd|secret|authorization)\b\s*[:=]\s*[\"']?[A-Za-z0-9_.-]{6,}",
    re.IGNORECASE,
)
_PROMPT_INJECTION_RE = re.compile(
    r"(?:忽略|无视).{0,12}(?:系统|之前|上述).{0,12}(?:指令|提示)|"
    r"ignore.{0,12}(?:previous|system).{0,12}instructions?",
    re.IGNORECASE,
)
_MAX_SOURCE_LINES = 80
_MIN_LLM_QUALITY = 0.75
_CARD_MAX_CHARS = 120
_CANONICAL_CARD_TYPES = {"decision", "fact", "todo", "preference", "module", "design_rule"}


@dataclass(frozen=True)
class _LlmDigestBatch:
    source_rows: list[dict[str, Any]]
    messages: list[dict[str, str]]
    prompt_meta: dict[str, Any]


@dataclass(frozen=True)
class _LlmDigestContext:
    fallback: MemoryDigestBuildResult
    source_rows: list[dict[str, Any]]
    messages: list[dict[str, str]]
    prompt_meta: dict[str, Any]
    batches: tuple[_LlmDigestBatch, ...] = ()

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



class SyncSummarizerContractError(TypeError):
    """同步入口收到了 awaitable summarizer。"""


class MemoryDigestModelError(RuntimeError):
    """记忆摘要模型调用或响应合同失败。"""


class MemoryDigestOutputError(ValueError):
    """记忆摘要模型输出不满足 JSON 根合同。"""


@dataclass(frozen=True)
class MemoryDigestLlmOutput:
    content: str
    model: str


def _safe_json_loads(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        raise MemoryDigestOutputError("json_parse_failed:empty_response")
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        match = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)```", text)
        if not match:
            raise MemoryDigestOutputError("json_parse_failed") from exc
        try:
            value = json.loads(match.group(1).strip())
        except (TypeError, json.JSONDecodeError) as fenced_exc:
            raise MemoryDigestOutputError("json_parse_failed") from fenced_exc
    if not isinstance(value, dict):
        raise MemoryDigestOutputError("json_schema_invalid:root_not_object")
    return value


def _close_awaitable(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        close()


def _call_summarizer(summarizer: Callable[[list[dict[str, str]]], Any], messages: list[dict[str, str]]) -> Any:
    result = summarizer(messages)
    if inspect.isawaitable(result):
        _close_awaitable(result)
        raise SyncSummarizerContractError(
            "sync_summarizer_returned_awaitable: use build_memory_digest_with_llm_async"
        )
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
    return rows


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
    rendered = render_task_pair("memory_digest", values)
    system_meta = rendered.system
    user_meta = rendered.user
    prompt_meta = {
        "template": f"{_SYSTEM_TEMPLATE_KEY} + {_USER_TEMPLATE_KEY}",
        "system_key": system_meta.task_key,
        "system_path": system_meta.path,
        "system_source": system_meta.source,
        "system_sha256": sha256_text(system_meta.content),
        "system_version": system_meta.version,
        "user_key": user_meta.task_key,
        "user_path": user_meta.path,
        "user_source": user_meta.source,
        "user_sha256": sha256_text(user_meta.content),
        "user_version": user_meta.version,
        "source_id": sid,
        "source_type": "date_session",
        "source_range": values["source_range"],
        "message_count": len(source_rows),
    }
    return rendered.messages, prompt_meta


async def default_llm_memory_digest_summarizer_async(
    messages: list[dict[str, str]],
) -> MemoryDigestLlmOutput:
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
        raise MemoryDigestModelError(
            str(response.get("detail") or response.get("error"))
        )
    try:
        return MemoryDigestLlmOutput(
            content=str(response["choices"][0]["message"].get("content") or ""),
            model=str(route.get("model") or "unknown"),
        )
    except (AttributeError, IndexError, KeyError, TypeError) as exc:
        raise MemoryDigestModelError("llm_response_missing_content") from exc


def default_llm_memory_digest_summarizer(messages: list[dict[str, str]]) -> str:
    raise RuntimeError(
        "sync_summarizer_required: use default_llm_memory_digest_summarizer_async "
        "with build_memory_digest_with_llm_async"
    )


def parse_llm_digest_response(raw: Any) -> dict[str, Any]:
    if isinstance(raw, MemoryDigestLlmOutput):
        raw = raw.content
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
    llm_model: str,
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
        card_type = _as_str(raw_card.get("type"), max_chars=40)
        if card_type not in _CANONICAL_CARD_TYPES:
            card_type = "fact"
        cards.append({
            "card_id": _as_str(raw_card.get("card_id"), max_chars=40) or f"card_{index}",
            "type": card_type,
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
        "llm_model": llm_model or "unknown",
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

    audited_text = json.dumps({
        "preview": preview,
        "long_summary": long_summary,
        "recall_cards": cards,
    }, ensure_ascii=False)
    if _URL_RE.search(audited_text):
        issues.append("contains_url")
    if _CREDENTIAL_RE.search(audited_text):
        issues.append("contains_credential_material")
    if _PROMPT_INJECTION_RE.search(audited_text):
        issues.append("contains_prompt_injection_text")

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
    source_by_id = {
        int(row.get("log_id")): row
        for row in (source_rows or [])
        if str(row.get("log_id") or "").isdigit()
    }

    for card in cards:
        text = str(card.get("text") if isinstance(card, dict) else card or "").strip()
        if len(text) > _CARD_MAX_CHARS:
            issues.append("recall_card_too_long")

        # evidence_log_ids 必须存在于 source
        evidence_ids = card.get("evidence_log_ids") if isinstance(card, dict) else []
        if isinstance(evidence_ids, list) and evidence_ids and source_log_ids:
            evidence_lines: list[str] = []
            for eid in evidence_ids:
                try:
                    if int(eid) not in source_log_ids:
                        issues.append("recall_card_evidence_not_in_source")
                        break
                    evidence_lines.extend(
                        str(row.get("line") or "")
                        for row in (source_rows or [])
                        if int(row.get("log_id") or 0) == int(eid)
                    )
                except (TypeError, ValueError):
                    issues.append("recall_card_evidence_not_in_source")
                    break
            evidence_text = "\n".join(evidence_lines)
            keywords = card.get("keywords") if isinstance(card, dict) else []
            keyword_values = [str(item).strip() for item in keywords or [] if str(item).strip()]
            keyword_supported = (
                not keyword_values
                or any(keyword.lower() in evidence_text.lower() for keyword in keyword_values)
            )
            if evidence_text and (
                not keyword_supported
                or not _has_keyword_overlap(text, evidence_text, min_overlap=1)
            ):
                issues.append("recall_card_evidence_not_grounded")
            if str(card.get("type") or "") == "preference":
                evidence_rows = [
                    source_by_id.get(int(eid))
                    for eid in evidence_ids
                    if str(eid).isdigit()
                ]
                if any(
                    row is None
                    or str(row.get("role") or "") not in {"user", "ambient"}
                    or bool(row.get("is_bot"))
                    for row in evidence_rows
                ):
                    issues.append("preference_evidence_invalid_role")

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
    result_status: str | None = None,
) -> MemoryDigestBuildResult:
    prompt_meta = prompt_meta or {}
    meta = {
        **fallback.meta,
        "status": result_status or fallback.meta.get("status") or fallback.status,
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
    level_contents = (
        dict(fallback.level_contents)
        if result_status == "failed"
        else render_digest_levels(meta)
    )
    return MemoryDigestBuildResult(
        status=result_status or fallback.status,
        meta=meta,
        level_contents=level_contents,
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
        return None, _fallback_result(
            fallback,
            status="input_invalid",
            error="source_rows_empty",
            result_status="failed",
        )

    try:
        batches: list[_LlmDigestBatch] = []
        for start in range(0, len(source_rows), _MAX_SOURCE_LINES):
            batch_rows = source_rows[start:start + _MAX_SOURCE_LINES]
            messages, batch_prompt_meta = build_llm_digest_messages(
                session_id=session_id,
                digest_date=digest_date,
                fallback=fallback,
                source_rows=batch_rows,
            )
            batches.append(_LlmDigestBatch(
                source_rows=batch_rows,
                messages=messages,
                prompt_meta=batch_prompt_meta,
            ))
    except TaskCallValueError as exc:
        logger.warning(
            "memory digest task input rejected: session_id=%s date=%s error_type=%s",
            session_id,
            digest_date,
            type(exc).__name__,
        )
        return None, _fallback_result(
            fallback,
            status="input_invalid",
            error=type(exc).__name__,
            result_status="failed",
        )
    except TaskInvocationError as exc:
        logger.warning(
            "memory digest task template rejected: session_id=%s date=%s error_type=%s",
            session_id,
            digest_date,
            type(exc).__name__,
        )
        return None, _fallback_result(
            fallback,
            status="template_invalid",
            error=type(exc).__name__,
            result_status="failed",
        )
    first_batch = batches[0]
    prompt_meta = {
        **first_batch.prompt_meta,
        "source_id": _source_id(
            session_id=session_id,
            digest_date=digest_date,
            source_rows=source_rows,
        ),
        "source_range": _source_range(source_rows),
        "message_count": len(source_rows),
        "batch_count": len(batches),
    }
    return _LlmDigestContext(
        fallback=fallback,
        source_rows=source_rows,
        messages=first_batch.messages,
        prompt_meta=prompt_meta,
        batches=tuple(batches),
    ), None


def _batch_context(
    context: _LlmDigestContext,
    batch: _LlmDigestBatch,
) -> _LlmDigestContext:
    return _LlmDigestContext(
        fallback=context.fallback,
        source_rows=batch.source_rows,
        messages=batch.messages,
        prompt_meta=batch.prompt_meta,
        batches=(batch,),
    )


def _unique_strings(values: Iterable[Any], *, limit: int, item_chars: int) -> list[str]:
    result: list[str] = []
    for value in values:
        text = _as_str(value, max_chars=item_chars)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _merge_batch_results(
    context: _LlmDigestContext,
    results: list[MemoryDigestBuildResult],
) -> MemoryDigestBuildResult:
    if len(results) == 1:
        meta = {
            **results[0].meta,
            "source_id": context.prompt_meta["source_id"],
            "source_range": context.prompt_meta["source_range"],
            "cleaned_source_range": context.prompt_meta["source_range"],
            "message_count": context.prompt_meta["message_count"],
            "batch_count": 1,
        }
    else:
        previews = [item.meta.get("preview", {}) for item in results]
        long_summaries = [item.meta.get("long_summary", {}) for item in results]
        card_groups = [
            list(item.meta.get("recall_cards") or [])
            for item in results
        ]
        merged_cards: list[dict[str, Any]] = []
        seen_card_text: set[str] = set()
        max_group_size = max((len(group) for group in card_groups), default=0)
        for offset in range(max_group_size):
            for group in card_groups:
                if offset >= len(group):
                    continue
                card = group[offset]
                if not isinstance(card, dict):
                    continue
                text_value = str(card.get("text") or "").strip()
                if not text_value or text_value in seen_card_text:
                    continue
                seen_card_text.add(text_value)
                merged_cards.append({**card, "card_id": f"card_{len(merged_cards) + 1}"})
                if len(merged_cards) >= 12:
                    break
            if len(merged_cards) >= 12:
                break

        scores = [
            _as_float((item.meta.get("quality") or {}).get("score"), default=0.0)
            for item in results
        ]
        base = dict(results[0].meta)
        meta = {
            **base,
            "source_id": context.prompt_meta["source_id"],
            "source_range": context.prompt_meta["source_range"],
            "cleaned_source_range": context.prompt_meta["source_range"],
            "message_count": context.prompt_meta["message_count"],
            "batch_count": len(results),
            "preview": {
                "brief": _as_str(
                    "；".join(str(item.get("brief") or "").strip() for item in previews),
                    max_chars=220,
                ),
                "keywords": _unique_strings(
                    (value for item in previews for value in (item.get("keywords") or [])),
                    limit=12,
                    item_chars=40,
                ),
                "participants": _unique_strings(
                    (value for item in previews for value in (item.get("participants") or [])),
                    limit=12,
                    item_chars=40,
                ),
            },
            "long_summary": {
                "topic_flow": _as_str(
                    "\n".join(str(item.get("topic_flow") or "").strip() for item in long_summaries),
                    max_chars=900,
                ),
                "important_details": _unique_strings(
                    (value for item in long_summaries for value in (item.get("important_details") or [])),
                    limit=20,
                    item_chars=220,
                ),
                "conclusions": _unique_strings(
                    (value for item in long_summaries for value in (item.get("conclusions") or [])),
                    limit=12,
                    item_chars=180,
                ),
                "open_loops": _unique_strings(
                    (value for item in long_summaries for value in (item.get("open_loops") or [])),
                    limit=12,
                    item_chars=180,
                ),
            },
            "recall_cards": merged_cards,
            "quality": build_quality(
                score=min(scores) if scores else 0.0,
                issues=[],
                should_inject_preview=True,
            ),
        }

    audit_ok, issues = audit_llm_digest_meta(meta, source_rows=context.source_rows)
    if not audit_ok:
        return _fallback_result(
            context.fallback,
            status="fallback",
            error=",".join(issues),
            prompt_meta=context.prompt_meta,
            result_status="failed",
        )
    return MemoryDigestBuildResult(
        status="active",
        meta=meta,
        level_contents=render_digest_levels(meta),
    )


def _build_memory_digest_result_from_raw(
    raw: Any,
    *,
    context: _LlmDigestContext,
    session_id: str,
    digest_date: str,
    user_id: str,
) -> MemoryDigestBuildResult:
    llm_model = raw.model if isinstance(raw, MemoryDigestLlmOutput) else "custom_summarizer"
    payload = parse_llm_digest_response(raw)
    meta = _normalize_llm_meta(
        payload,
        fallback=context.fallback,
        session_id=session_id,
        digest_date=digest_date,
        user_id=user_id,
        prompt_meta=context.prompt_meta,
        llm_model=llm_model,
    )
    audit_ok, issues = audit_llm_digest_meta(meta, source_rows=context.source_rows)
    if not audit_ok:
        return _fallback_result(
            context.fallback,
            status="fallback",
            error=",".join(issues),
            prompt_meta=context.prompt_meta,
            result_status="failed",
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
            result_status="failed",
        )
    results: list[MemoryDigestBuildResult] = []
    for batch in context.batches:
        try:
            raw = _call_summarizer(summarizer, batch.messages)
        except (
            ConnectionError,
            TimeoutError,
            MemoryDigestModelError,
            SyncSummarizerContractError,
        ) as exc:
            logger.warning("memory digest llm fallback: session_id=%s date=%s error=%s", session_id, digest_date, exc)
            return _fallback_result(
                context.fallback,
                status="fallback",
                error=str(exc),
                prompt_meta=context.prompt_meta,
                result_status="failed",
            )
        try:
            result = _build_memory_digest_result_from_raw(
                raw,
                context=_batch_context(context, batch),
                session_id=session_id,
                digest_date=digest_date,
                user_id=user_id,
            )
        except MemoryDigestOutputError as exc:
            logger.warning("memory digest llm fallback: session_id=%s date=%s error=%s", session_id, digest_date, exc)
            return _fallback_result(
                context.fallback,
                status="fallback",
                error=str(exc),
                prompt_meta=context.prompt_meta,
                result_status="failed",
            )
        if result.status != "active":
            return result
        results.append(result)
    return _merge_batch_results(context, results)


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
    results: list[MemoryDigestBuildResult] = []
    for batch in context.batches:
        try:
            raw = await _call_summarizer_async(summarizer, batch.messages)
        except (ConnectionError, TimeoutError, MemoryDigestModelError) as exc:
            logger.warning("memory digest llm fallback: session_id=%s date=%s error=%s", session_id, digest_date, exc)
            return _fallback_result(
                context.fallback,
                status="fallback",
                error=str(exc),
                prompt_meta=context.prompt_meta,
                result_status="failed",
            )
        try:
            result = _build_memory_digest_result_from_raw(
                raw,
                context=_batch_context(context, batch),
                session_id=session_id,
                digest_date=digest_date,
                user_id=user_id,
            )
        except MemoryDigestOutputError as exc:
            logger.warning("memory digest llm fallback: session_id=%s date=%s error=%s", session_id, digest_date, exc)
            return _fallback_result(
                context.fallback,
                status="fallback",
                error=str(exc),
                prompt_meta=context.prompt_meta,
                result_status="failed",
            )
        if result.status != "active":
            return result
        results.append(result)
    return _merge_batch_results(context, results)
