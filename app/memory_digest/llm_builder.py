"""MemoryDigest LLM 主生成器。"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

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
_CARD_MAX_CHARS = 120
_RECALL_CARD_LIMIT = 8
_CARD_KEYWORD_LIMIT = 6
_CARD_KEYWORD_MAX_CHARS = 32
_PREVIEW_BRIEF_MAX_CHARS = 200
_PREVIEW_LIST_LIMIT = 8
_PREVIEW_ITEM_MAX_CHARS = 32
_TOPIC_FLOW_MAX_CHARS = 600
_IMPORTANT_DETAIL_LIMIT = 8
_IMPORTANT_DETAIL_MAX_CHARS = 140
_CONCLUSION_LIMIT = 6
_CONCLUSION_MAX_CHARS = 120
_OPEN_LOOP_LIMIT = 6
_OPEN_LOOP_MAX_CHARS = 120
_QUALITY_REASON_MAX_CHARS = 180
_MEMORY_DIGEST_MAX_ESTIMATED_OUTPUT_TOKENS = 7200
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

    def __init__(
        self,
        message: str,
        *,
        requested_models: Iterable[str] = (),
        request_log_ids: Iterable[int] = (),
    ) -> None:
        super().__init__(message)
        self.requested_models = tuple(
            str(model or "").strip()
            for model in requested_models
            if str(model or "").strip()
        )
        self.request_log_ids = tuple(
            int(log_id)
            for log_id in request_log_ids
            if isinstance(log_id, int)
            and not isinstance(log_id, bool)
            and log_id > 0
        )


class MemoryDigestOutputError(ValueError):
    """记忆摘要模型输出不满足 JSON 根合同。"""


class MemoryDigestCapacityError(MemoryDigestOutputError):
    """模型因输出容量截断响应；相同合同下原样重试没有意义。"""

    def __init__(
        self,
        message: str = "memory_digest_output_capacity_exceeded",
        *,
        requested_models: Iterable[str] = (),
        request_log_ids: Iterable[int] = (),
    ) -> None:
        super().__init__(message)
        self.requested_models = tuple(
            str(model or "").strip()
            for model in requested_models
            if str(model or "").strip()
        )
        self.request_log_ids = tuple(
            int(log_id)
            for log_id in request_log_ids
            if isinstance(log_id, int)
            and not isinstance(log_id, bool)
            and log_id > 0
        )


@dataclass(frozen=True)
class MemoryDigestLlmOutput:
    content: str
    model: str
    requested_model: str = ""
    request_log_id: int | None = None
    actual_model_observed: bool = True


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


async def _call_heartbeat_async(
    heartbeat: Callable[[], Any] | None,
) -> None:
    if heartbeat is None:
        return
    result = heartbeat()
    if inspect.isawaitable(result):
        await result


async def _call_checkpoint_async(
    checkpoint_callback: Callable[[dict[str, Any]], Any] | None,
    checkpoint: dict[str, Any],
) -> None:
    if checkpoint_callback is None:
        return
    result = checkpoint_callback(checkpoint)
    if inspect.isawaitable(result):
        await result


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


def _as_int_list(value: Any, *, limit: int = 8) -> list[int]:
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
    manifest = [
        {
            "log_id": int(row.get("log_id") or 0),
            "time": str(row.get("time") or ""),
            "role": str(row.get("role") or ""),
            "is_bot": bool(row.get("is_bot")),
            "sender": str(row.get("sender") or ""),
            "content": str(row.get("content") or row.get("line") or ""),
        }
        for row in source_rows
    ]
    raw = json.dumps(
        {
            "schema": "memory_digest_source_v3",
            "digest_date": digest_date,
            "session_id": session_id,
            "rows": manifest,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
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
        max_tokens=int(route.get("max_tokens", 8192)),
        llm_source="memory_digest",
        enable_thinking=route.get("enable_thinking", "false"),
    )
    if isinstance(response, dict) and response.get("error"):
        raw_requested_models = response.get("_nanobot_requested_models")
        if not isinstance(raw_requested_models, (list, tuple)):
            raw_requested_models = [
                response.get("_nanobot_requested_model")
                or response.get("_nanobot_model_id")
                or route.get("model")
                or ""
            ]
        raw_request_log_ids = response.get("_nanobot_request_log_ids")
        if not isinstance(raw_request_log_ids, (list, tuple)):
            raw_request_log_ids = [response.get("_nanobot_request_log_id")]
        raise MemoryDigestModelError(
            str(response.get("detail") or response.get("error")),
            requested_models=raw_requested_models,
            request_log_ids=raw_request_log_ids,
        )
    try:
        raw_log_id = response.get("_nanobot_request_log_id")
        request_log_id = (
            int(raw_log_id)
            if isinstance(raw_log_id, int)
            and not isinstance(raw_log_id, bool)
            and raw_log_id > 0
            else None
        )
        observed_model = str(response.get("model") or "").strip()
        requested_model = str(
            response.get("_nanobot_requested_model")
            or response.get("_nanobot_model_id")
            or route.get("model")
            or "unknown"
        ).strip() or "unknown"
        finish_reason = str(
            response["choices"][0].get("finish_reason") or ""
        ).strip().lower()
        if finish_reason in {"length", "max_tokens"}:
            raise MemoryDigestCapacityError(
                requested_models=[requested_model],
                request_log_ids=[request_log_id] if request_log_id else [],
            )
        return MemoryDigestLlmOutput(
            content=str(response["choices"][0]["message"].get("content") or ""),
            model=observed_model or "unknown",
            requested_model=requested_model,
            request_log_id=request_log_id,
            actual_model_observed=bool(observed_model),
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


def _validate_llm_digest_output_budget(payload: dict[str, Any]) -> None:
    """按紧凑 JSON 审计长期摘要整体容量，避免接受失控的大数组。"""

    from app.session_memory.windowing import estimate_tokens

    try:
        compact = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise MemoryDigestOutputError(
            "json_schema_invalid:non_serializable"
        ) from exc
    if estimate_tokens(compact) > _MEMORY_DIGEST_MAX_ESTIMATED_OUTPUT_TOKENS:
        raise MemoryDigestCapacityError(
            "memory_digest_output_contract_exceeded"
        )


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
    for index, item in enumerate(raw_cards[:_RECALL_CARD_LIMIT], start=1):
        raw_card = item if isinstance(item, dict) else {"text": item}
        text = _as_str(raw_card.get("text"), max_chars=_CARD_MAX_CHARS)
        if not text:
            continue
        card_type = _as_str(raw_card.get("type"), max_chars=40)
        if card_type not in _CANONICAL_CARD_TYPES:
            card_type = "fact"
        cards.append({
            "card_id": _as_str(raw_card.get("card_id"), max_chars=40) or f"card_{index}",
            "type": card_type,
            "text": text,
            "keywords": _as_str_list(
                raw_card.get("keywords"),
                limit=_CARD_KEYWORD_LIMIT,
                item_chars=_CARD_KEYWORD_MAX_CHARS,
            ),
            "importance": max(0.0, min(1.0, _as_float(raw_card.get("importance"), default=0.7))),
            "evidence_log_ids": _as_int_list(raw_card.get("evidence_log_ids"), limit=8),
        })

    score = _as_float(quality.get("score"), default=0.0)
    issues = _as_str_list(quality.get("issues"), limit=10, item_chars=80)
    reason = _as_str(
        quality.get("reason"),
        max_chars=_QUALITY_REASON_MAX_CHARS,
    )
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
            "brief": _as_str(
                preview.get("brief"),
                max_chars=_PREVIEW_BRIEF_MAX_CHARS,
            ),
            "keywords": _as_str_list(
                preview.get("keywords"),
                limit=_PREVIEW_LIST_LIMIT,
                item_chars=_PREVIEW_ITEM_MAX_CHARS,
            ),
            "participants": _as_str_list(
                preview.get("participants"),
                limit=_PREVIEW_LIST_LIMIT,
                item_chars=_PREVIEW_ITEM_MAX_CHARS,
            ),
        },
        "long_summary": {
            "topic_flow": _as_str(
                long_summary.get("topic_flow"),
                max_chars=_TOPIC_FLOW_MAX_CHARS,
            ),
            "important_details": _as_str_list(
                long_summary.get("important_details"),
                limit=_IMPORTANT_DETAIL_LIMIT,
                item_chars=_IMPORTANT_DETAIL_MAX_CHARS,
            ),
            "conclusions": _as_str_list(
                long_summary.get("conclusions"),
                limit=_CONCLUSION_LIMIT,
                item_chars=_CONCLUSION_MAX_CHARS,
            ),
            "open_loops": _as_str_list(
                long_summary.get("open_loops"),
                limit=_OPEN_LOOP_LIMIT,
                item_chars=_OPEN_LOOP_MAX_CHARS,
            ),
        },
        "recall_cards": cards,
        "quality": {
            **build_quality(score=score, issues=issues, should_inject_preview=True),
            "reason": reason,
        },
    }
    return meta


def _recall_card_audit_issues(
    card: Any,
    *,
    source_rows: list[dict[str, Any]] | None = None,
) -> list[str]:
    """按单张召回卡执行证据与内容门禁。"""

    issues: list[str] = []
    normalized_card = card if isinstance(card, dict) else {}
    text = str(normalized_card.get("text") or card or "").strip()
    audited_card_text = json.dumps(
        normalized_card if normalized_card else card,
        ensure_ascii=False,
    )
    if len(text) > _CARD_MAX_CHARS:
        issues.append("recall_card_too_long")
    if _URL_RE.search(audited_card_text):
        issues.append("contains_url")
    if _CREDENTIAL_RE.search(audited_card_text):
        issues.append("contains_credential_material")
    if _PROMPT_INJECTION_RE.search(audited_card_text):
        issues.append("contains_prompt_injection_text")

    source_log_ids = {
        int(row.get("log_id"))
        for row in (source_rows or [])
        if str(row.get("log_id") or "").isdigit()
    }
    source_by_id = {
        int(row.get("log_id")): row
        for row in (source_rows or [])
        if str(row.get("log_id") or "").isdigit()
    }
    evidence_ids = normalized_card.get("evidence_log_ids") or []
    if not isinstance(evidence_ids, list) or not evidence_ids:
        issues.append("recall_card_evidence_missing")
    elif len(evidence_ids) > 8:
        issues.append("recall_card_evidence_too_many")

    if isinstance(evidence_ids, list) and evidence_ids and source_rows is not None:
        evidence_lines: list[str] = []
        evidence_valid = True
        for evidence_id in evidence_ids:
            try:
                normalized_id = int(evidence_id)
            except (TypeError, ValueError):
                evidence_valid = False
                break
            if normalized_id not in source_log_ids:
                evidence_valid = False
                break
            evidence_lines.append(
                str(source_by_id[normalized_id].get("line") or "")
            )
        if not evidence_valid:
            issues.append("recall_card_evidence_not_in_source")
        else:
            evidence_text = "\n".join(evidence_lines)
            keywords = normalized_card.get("keywords") or []
            keyword_values = [
                str(item).strip()
                for item in keywords
                if str(item).strip()
            ]
            keyword_supported = (
                not keyword_values
                or any(
                    keyword.lower() in evidence_text.lower()
                    for keyword in keyword_values
                )
            )
            if evidence_text and (
                not keyword_supported
                or not _has_keyword_overlap(text, evidence_text, min_overlap=1)
            ):
                issues.append("recall_card_evidence_not_grounded")

            if str(normalized_card.get("type") or "") == "preference" and any(
                str(source_by_id[int(evidence_id)].get("role") or "")
                not in {"user", "ambient"}
                or bool(source_by_id[int(evidence_id)].get("is_bot"))
                for evidence_id in evidence_ids
            ):
                issues.append("preference_evidence_invalid_role")

    if _is_generic_card_text(text):
        issues.append("recall_card_too_generic")
    if re.search(r"(/[\w.-]+){2,}|[A-Za-z]:\\|[\w.-]+\.py:\d+", text):
        issues.append("recall_card_contains_log_path")
    return list(dict.fromkeys(issues))


def _apply_recall_card_governance(
    meta: dict[str, Any],
    *,
    source_rows: list[dict[str, Any]] | None = None,
) -> list[str]:
    """仅保留通过候选级证据门禁的召回卡，并记录非正文审计统计。"""

    cards = meta.get("recall_cards") if isinstance(meta.get("recall_cards"), list) else []
    accepted: list[dict[str, Any]] = []
    rejection_reason_counts: dict[str, int] = {}
    for card in cards:
        card_issues = _recall_card_audit_issues(card, source_rows=source_rows)
        if not card_issues and isinstance(card, dict):
            accepted.append(card)
            continue
        for issue in card_issues or ["recall_card_schema_invalid"]:
            rejection_reason_counts[issue] = rejection_reason_counts.get(issue, 0) + 1

    meta["recall_cards"] = accepted
    meta["recall_card_governance"] = {
        "generated_count": len(cards),
        "evidence_accepted_count": len(accepted),
        "evidence_rejected_count": len(cards) - len(accepted),
        "retained_count": len(accepted),
        "rejection_reason_counts": dict(sorted(rejection_reason_counts.items())),
    }
    if cards and not accepted:
        return list(rejection_reason_counts)
    return []


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

    # 模型自评分仅作为预览注入和检索权重信号，不能单独否定确定性审计结果。
    quality = meta.get("quality") if isinstance(meta.get("quality"), dict) else {}
    quality_issues = quality.get("issues") if isinstance(quality.get("issues"), list) else []
    if quality_issues:
        issues.append("quality_issues_present")

    audited_text = json.dumps({
        "preview": preview,
        "long_summary": long_summary,
    }, ensure_ascii=False)
    if _URL_RE.search(audited_text):
        issues.append("contains_url")
    if _CREDENTIAL_RE.search(audited_text):
        issues.append("contains_credential_material")
    if _PROMPT_INJECTION_RE.search(audited_text):
        issues.append("contains_prompt_injection_text")

    for card in cards:
        issues.extend(_recall_card_audit_issues(card, source_rows=source_rows))

    issues = list(dict.fromkeys(issues))
    return not issues, issues


def _fallback_result(
    fallback: MemoryDigestBuildResult,
    *,
    status: str,
    error: str = "",
    prompt_meta: dict[str, Any] | None = None,
    result_status: str | None = None,
    failure_type: str = "",
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
    if failure_type:
        meta["failure_type"] = str(failure_type)[:64]
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


def _with_quality_audit_trace(
    result: MemoryDigestBuildResult,
    *,
    audited_meta: dict[str, Any],
    issues: Iterable[str],
) -> MemoryDigestBuildResult:
    """保留被拒绝的模型自评分和确定性审计项。"""

    quality = (
        audited_meta.get("quality")
        if isinstance(audited_meta.get("quality"), dict)
        else {}
    )
    meta = {
        **result.meta,
        "model_quality_score": quality.get("score"),
        "audit_issues": list(dict.fromkeys(str(issue) for issue in issues)),
    }
    return MemoryDigestBuildResult(
        status=result.status,
        meta=meta,
        level_contents=dict(result.level_contents),
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
            failure_type="input_invalid",
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
            failure_type="input_invalid",
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
            failure_type="template_invalid",
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


def _restore_batch_checkpoint(
    context: _LlmDigestContext,
    checkpoint: dict[str, Any] | None,
) -> list[MemoryDigestBuildResult]:
    """只恢复与当前完整来源和批次清单严格一致的连续检查点。"""

    raw = checkpoint if isinstance(checkpoint, dict) else {}
    if (
        int(raw.get("schema_version") or 0) != 1
        or str(raw.get("digest_source_id") or "")
        != str(context.prompt_meta.get("source_id") or "")
    ):
        return []
    entries = raw.get("completed_batches")
    if not isinstance(entries, list) or len(entries) > len(context.batches):
        return []

    restored: list[MemoryDigestBuildResult] = []
    for expected_index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            return []
        batch = context.batches[expected_index]
        meta = entry.get("meta")
        if (
            int(entry.get("batch_index", -1)) != expected_index
            or str(entry.get("batch_source_id") or "")
            != str(batch.prompt_meta.get("source_id") or "")
            or not isinstance(meta, dict)
            or str(meta.get("status") or "") != "active"
            or str(meta.get("generator") or "") != "llm"
            or str(meta.get("source_id") or "")
            != str(batch.prompt_meta.get("source_id") or "")
        ):
            return []
        audit_ok, _issues = audit_llm_digest_meta(
            meta,
            source_rows=batch.source_rows,
        )
        if not audit_ok:
            return []
        restored.append(MemoryDigestBuildResult(
            status="active",
            meta=dict(meta),
            level_contents=render_digest_levels(meta),
        ))
    return restored


def _build_batch_checkpoint(
    context: _LlmDigestContext,
    results: list[MemoryDigestBuildResult],
) -> dict[str, Any]:
    completed = []
    for batch_index, result in enumerate(results):
        if batch_index >= len(context.batches):
            break
        completed.append({
            "batch_index": batch_index,
            "batch_source_id": str(
                context.batches[batch_index].prompt_meta.get("source_id") or ""
            ),
            "meta": dict(result.meta),
        })
    return {
        "schema_version": 1,
        "digest_source_id": str(context.prompt_meta.get("source_id") or ""),
        "batch_count": len(context.batches),
        "completed_batches": completed,
    }


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
                if len(merged_cards) >= _RECALL_CARD_LIMIT:
                    break
            if len(merged_cards) >= _RECALL_CARD_LIMIT:
                break

        scores = [
            _as_float((item.meta.get("quality") or {}).get("score"), default=0.0)
            for item in results
        ]
        governance_rows = [
            item.meta.get("recall_card_governance")
            if isinstance(item.meta.get("recall_card_governance"), dict)
            else {}
            for item in results
        ]
        rejection_reason_counts: dict[str, int] = {}
        for governance in governance_rows:
            raw_counts = governance.get("rejection_reason_counts")
            if not isinstance(raw_counts, dict):
                continue
            for reason, count in raw_counts.items():
                rejection_reason_counts[str(reason)] = (
                    rejection_reason_counts.get(str(reason), 0)
                    + max(0, int(count or 0))
                )
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
                    max_chars=_PREVIEW_BRIEF_MAX_CHARS,
                ),
                "keywords": _unique_strings(
                    (value for item in previews for value in (item.get("keywords") or [])),
                    limit=_PREVIEW_LIST_LIMIT,
                    item_chars=_PREVIEW_ITEM_MAX_CHARS,
                ),
                "participants": _unique_strings(
                    (value for item in previews for value in (item.get("participants") or [])),
                    limit=_PREVIEW_LIST_LIMIT,
                    item_chars=_PREVIEW_ITEM_MAX_CHARS,
                ),
            },
            "long_summary": {
                "topic_flow": _as_str(
                    "\n".join(str(item.get("topic_flow") or "").strip() for item in long_summaries),
                    max_chars=_TOPIC_FLOW_MAX_CHARS,
                ),
                "important_details": _unique_strings(
                    (value for item in long_summaries for value in (item.get("important_details") or [])),
                    limit=_IMPORTANT_DETAIL_LIMIT,
                    item_chars=_IMPORTANT_DETAIL_MAX_CHARS,
                ),
                "conclusions": _unique_strings(
                    (value for item in long_summaries for value in (item.get("conclusions") or [])),
                    limit=_CONCLUSION_LIMIT,
                    item_chars=_CONCLUSION_MAX_CHARS,
                ),
                "open_loops": _unique_strings(
                    (value for item in long_summaries for value in (item.get("open_loops") or [])),
                    limit=_OPEN_LOOP_LIMIT,
                    item_chars=_OPEN_LOOP_MAX_CHARS,
                ),
            },
            "recall_cards": merged_cards,
            "recall_card_governance": {
                "generated_count": sum(
                    max(0, int(item.get("generated_count") or 0))
                    for item in governance_rows
                ),
                "evidence_accepted_count": sum(
                    max(0, int(item.get("evidence_accepted_count") or 0))
                    for item in governance_rows
                ),
                "evidence_rejected_count": sum(
                    max(0, int(item.get("evidence_rejected_count") or 0))
                    for item in governance_rows
                ),
                "retained_count": len(merged_cards),
                "rejection_reason_counts": dict(sorted(rejection_reason_counts.items())),
            },
            "quality": build_quality(
                score=min(scores) if scores else 0.0,
                issues=[],
                should_inject_preview=True,
            ),
            "llm_models": _unique_strings(
                (
                    model
                    for item in results
                    for model in (
                        item.meta.get("llm_models")
                        if isinstance(item.meta.get("llm_models"), list)
                        else [item.meta.get("llm_model")]
                    )
                ),
                limit=32,
                item_chars=128,
            ),
            "llm_requested_models": _unique_strings(
                (
                    model
                    for item in results
                    for model in (
                        item.meta.get("llm_requested_models")
                        if isinstance(item.meta.get("llm_requested_models"), list)
                        else []
                    )
                ),
                limit=32,
                item_chars=128,
            ),
            "llm_request_log_ids": list(dict.fromkeys(
                int(log_id)
                for item in results
                for log_id in (
                    item.meta.get("llm_request_log_ids")
                    if isinstance(item.meta.get("llm_request_log_ids"), list)
                    else []
                )
                if isinstance(log_id, int)
                and not isinstance(log_id, bool)
                and log_id > 0
            )),
            "llm_actual_model_observed": all(
                bool(item.meta.get("llm_actual_model_observed", False))
                for item in results
            ),
        }

    audit_ok, issues = audit_llm_digest_meta(meta, source_rows=context.source_rows)
    if not audit_ok:
        return _with_quality_audit_trace(
            _fallback_result(
                context.fallback,
                status="fallback",
                error=",".join(issues),
                prompt_meta=context.prompt_meta,
                result_status="failed",
                failure_type="quality_rejected",
            ),
            audited_meta=meta,
            issues=issues,
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
    _validate_llm_digest_output_budget(payload)
    meta = _normalize_llm_meta(
        payload,
        fallback=context.fallback,
        session_id=session_id,
        digest_date=digest_date,
        user_id=user_id,
        prompt_meta=context.prompt_meta,
        llm_model=llm_model,
    )
    all_rejected_issues = _apply_recall_card_governance(
        meta,
        source_rows=context.source_rows,
    )
    audit_ok, issues = audit_llm_digest_meta(meta, source_rows=context.source_rows)
    if not audit_ok:
        issues = list(dict.fromkeys([*all_rejected_issues, *issues]))
        result = _with_quality_audit_trace(
            _fallback_result(
                context.fallback,
                status="fallback",
                error=",".join(issues),
                prompt_meta=context.prompt_meta,
                result_status="failed",
                failure_type="quality_rejected",
            ),
            audited_meta=meta,
            issues=issues,
        )
    else:
        result = MemoryDigestBuildResult(
            status="active",
            meta=meta,
            level_contents=render_digest_levels(meta),
        )
    if not isinstance(raw, MemoryDigestLlmOutput):
        return result
    return _with_llm_output_trace(result, [raw])


def _with_llm_output_trace(
    result: MemoryDigestBuildResult,
    outputs: Iterable[MemoryDigestLlmOutput],
) -> MemoryDigestBuildResult:
    rows = list(outputs)
    if not rows:
        return result
    models = _unique_strings(
        [
            *(result.meta.get("llm_models") or []),
            *(row.model for row in rows),
        ],
        limit=32,
        item_chars=128,
    )
    requested_models = _unique_strings(
        [
            *(result.meta.get("llm_requested_models") or []),
            *(row.requested_model for row in rows),
        ],
        limit=32,
        item_chars=128,
    )
    request_log_ids = list(dict.fromkeys([
        *(
            int(log_id)
            for log_id in (result.meta.get("llm_request_log_ids") or [])
            if isinstance(log_id, int)
            and not isinstance(log_id, bool)
            and log_id > 0
        ),
        *(
            int(row.request_log_id)
            for row in rows
            if isinstance(row.request_log_id, int)
            and not isinstance(row.request_log_id, bool)
            and row.request_log_id > 0
        ),
    ]))
    traced_meta = {
        **result.meta,
        "llm_model": models[-1] if models else "unknown",
        "llm_models": models,
        "llm_requested_models": requested_models,
        "llm_request_log_ids": request_log_ids,
        "llm_actual_model_observed": bool(
            result.meta.get("llm_actual_model_observed", True)
        ) and all(bool(row.actual_model_observed) for row in rows),
    }
    return MemoryDigestBuildResult(
        status=result.status,
        meta=traced_meta,
        level_contents=result.level_contents,
    )


def _with_llm_failure_trace(
    result: MemoryDigestBuildResult,
    outputs: Iterable[MemoryDigestLlmOutput],
    error: BaseException,
) -> MemoryDigestBuildResult:
    traced = _with_llm_output_trace(result, outputs)
    meta = dict(traced.meta)
    requested_models = _unique_strings(
        [
            *(meta.get("llm_requested_models") or []),
            *list(getattr(error, "requested_models", ()) or ()),
        ],
        limit=64,
        item_chars=128,
    )
    request_log_ids = list(dict.fromkeys(
        int(log_id)
        for log_id in [
            *(meta.get("llm_request_log_ids") or []),
            *list(getattr(error, "request_log_ids", ()) or ()),
        ]
        if isinstance(log_id, int)
        and not isinstance(log_id, bool)
        and log_id > 0
    ))
    meta.update({
        "llm_models": list(meta.get("llm_models") or []),
        "llm_requested_models": requested_models,
        "llm_request_log_ids": request_log_ids,
        "llm_actual_model_observed": False,
    })
    return MemoryDigestBuildResult(
        status=traced.status,
        meta=meta,
        level_contents=traced.level_contents,
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
            failure_type="generator_not_llm",
        )
    results: list[MemoryDigestBuildResult] = []
    for batch in context.batches:
        try:
            raw = _call_summarizer(summarizer, batch.messages)
        except MemoryDigestCapacityError as exc:
            logger.warning(
                "memory digest output capacity exceeded: session_id=%s date=%s",
                session_id,
                digest_date,
            )
            return _with_llm_failure_trace(_fallback_result(
                context.fallback,
                status="fallback",
                error=str(exc),
                prompt_meta=context.prompt_meta,
                result_status="failed",
                failure_type="output_capacity_exceeded",
            ), (), exc)
        except (
            ConnectionError,
            TimeoutError,
            MemoryDigestModelError,
            SyncSummarizerContractError,
        ) as exc:
            logger.warning(
                "memory digest llm fallback: session_id=%s date=%s error_type=%s",
                session_id,
                digest_date,
                type(exc).__name__,
            )
            return _fallback_result(
                context.fallback,
                status="fallback",
                error=str(exc),
                prompt_meta=context.prompt_meta,
                result_status="failed",
                failure_type="model_error",
            )
        try:
            result = _build_memory_digest_result_from_raw(
                raw,
                context=_batch_context(context, batch),
                session_id=session_id,
                digest_date=digest_date,
                user_id=user_id,
            )
        except MemoryDigestCapacityError as exc:
            return _with_llm_failure_trace(_fallback_result(
                context.fallback,
                status="fallback",
                error=str(exc),
                prompt_meta=context.prompt_meta,
                result_status="failed",
                failure_type="output_capacity_exceeded",
            ), (), exc)
        except MemoryDigestOutputError as exc:
            logger.warning(
                "memory digest llm fallback: session_id=%s date=%s error_type=%s",
                session_id,
                digest_date,
                type(exc).__name__,
            )
            return _fallback_result(
                context.fallback,
                status="fallback",
                error=str(exc),
                prompt_meta=context.prompt_meta,
                result_status="failed",
                failure_type="output_invalid",
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
    heartbeat: Callable[[], Any] | None = None,
    resume_checkpoint: dict[str, Any] | None = None,
    checkpoint_callback: Callable[[dict[str, Any]], Any] | None = None,
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
    results = _restore_batch_checkpoint(context, resume_checkpoint)
    traced_outputs: list[MemoryDigestLlmOutput] = []
    for batch_index, batch in enumerate(
        context.batches[len(results):],
        start=len(results),
    ):
        try:
            await _call_heartbeat_async(heartbeat)
            try:
                raw = await _call_summarizer_async(summarizer, batch.messages)
            finally:
                await _call_heartbeat_async(heartbeat)
        except MemoryDigestCapacityError as exc:
            logger.warning(
                "memory digest output capacity exceeded: session_id=%s date=%s",
                session_id,
                digest_date,
            )
            return _with_llm_failure_trace(_fallback_result(
                context.fallback,
                status="fallback",
                error=str(exc),
                prompt_meta=context.prompt_meta,
                result_status="failed",
                failure_type="output_capacity_exceeded",
            ), traced_outputs, exc)
        except (ConnectionError, TimeoutError, MemoryDigestModelError) as exc:
            logger.warning(
                "memory digest llm fallback: session_id=%s date=%s error_type=%s",
                session_id,
                digest_date,
                type(exc).__name__,
            )
            return _with_llm_failure_trace(_fallback_result(
                context.fallback,
                status="fallback",
                error=str(exc),
                prompt_meta=context.prompt_meta,
                result_status="failed",
                failure_type="model_error",
            ), traced_outputs, exc)
        if isinstance(raw, MemoryDigestLlmOutput):
            traced_outputs.append(raw)
        try:
            result = _build_memory_digest_result_from_raw(
                raw,
                context=_batch_context(context, batch),
                session_id=session_id,
                digest_date=digest_date,
                user_id=user_id,
            )
        except MemoryDigestCapacityError as exc:
            return _with_llm_failure_trace(_fallback_result(
                context.fallback,
                status="fallback",
                error=str(exc),
                prompt_meta=context.prompt_meta,
                result_status="failed",
                failure_type="output_capacity_exceeded",
            ), traced_outputs, exc)
        except MemoryDigestOutputError as exc:
            logger.warning(
                "memory digest llm fallback: session_id=%s date=%s error_type=%s",
                session_id,
                digest_date,
                type(exc).__name__,
            )
            return _with_llm_output_trace(_fallback_result(
                context.fallback,
                status="fallback",
                error=str(exc),
                prompt_meta=context.prompt_meta,
                result_status="failed",
                failure_type="output_invalid",
            ), traced_outputs)
        if result.status != "active":
            return _with_llm_output_trace(result, traced_outputs)
        results.append(result)
        checkpoint = _build_batch_checkpoint(context, results)
        await _call_checkpoint_async(checkpoint_callback, checkpoint)
    merged = _merge_batch_results(context, results)
    return _with_llm_output_trace(merged, traced_outputs)
