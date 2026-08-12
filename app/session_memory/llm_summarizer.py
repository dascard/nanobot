"""异步 LLM rolling session summary 生成与审计。"""

from __future__ import annotations

import hashlib
import html
import inspect
import json
import logging
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session

from app.session_memory import config
from app.session_memory.llm_contract import (
    InheritanceAudit,
    SummaryObligation,
    SummaryBatchTrace,
    SessionSummaryLLMResult,
    SummaryRequestBatch,
    TurnCoverageManifest,
    TurnFragment,
    build_coverage_manifest,
    build_previous_summary_obligations,
    build_summary_obligations,
    build_summary_request_batches,
    canonical_previous_state,
    canonical_summary_state,
    fragment_summary_turn,
    normalize_inheritance_metadata,
    request_char_count,
    strip_summary_inheritance,
    validate_inheritance,
)
from app.session_memory.jobs import (
    SessionSummaryJobLease,
    SessionSummaryJobLeaseLost,
    acquire_summary_finalize_permit,
    assert_summary_job_lease,
    claim_summary_job,
    fetch_pending_summary_jobs,
    mark_summary_job_done,
    mark_summary_job_failed,
    mark_summary_job_obsolete,
    renew_summary_job_lease,
    session_summary_job_lease,
)
from app.session_memory.rolling_summary import (
    SUMMARY_SOURCE_CONVERSATION_TURN,
    archive_active_summaries_for_session,
    audit_rolling_summary,
    normalize_summary_source_type,
    summary_covered_from,
    summary_source_ids_json,
)
from app.session_memory.summarizer import render_summary_text
from app.session_memory.windowing import (
    estimate_tokens,
    is_context_eligible_chat_log,
    is_context_eligible_turn,
    safe_meta,
)
from core.db.models.chat import ChatLog, ConversationTurn
from core.db.models.session_memory import RollingSessionSummary, SessionSummaryJob
from core.time_utils import db_now_naive

logger = logging.getLogger("nanobot.session_summary.llm")

LEGACY_SYNC_WORKER_HELPERS = True

# Prompt 目标为 7 项；正常结果允许最多 8 项作为一次局部修复的触发条件。
SESSION_SUMMARY_MAX_STATE_OBLIGATIONS = 8
SESSION_SUMMARY_REPAIR_TARGET_OBLIGATIONS = 7
SESSION_SUMMARY_MAX_REPAIR_ATTEMPTS = 1
SESSION_SUMMARY_MAX_SUMMARY_CHARS = 400
# Prompt 目标仍为 60 字；硬门禁保留 4 字格式容差，避免轻微越界导致整单重试。
SESSION_SUMMARY_MAX_OBLIGATION_CHARS = 64
SESSION_SUMMARY_MAX_ESTIMATED_OUTPUT_TOKENS = 3000

_SESSION_SUMMARY_LIST_FIELDS = (
    "open_threads",
    "decisions",
    "important_user_requests",
    "resolved_items",
    "artifacts",
    "participants",
    "keywords",
)
_SESSION_SUMMARY_ROOT_FIELDS = frozenset({
    "summary",
    *_SESSION_SUMMARY_LIST_FIELDS,
    "quality",
    "inheritance",
})
_SESSION_SUMMARY_QUALITY_FIELDS = frozenset({"score", "issues"})
_SESSION_SUMMARY_INHERITANCE_FIELDS = frozenset({
    "source_id",
    "disposition",
    "target_field",
    "target_index",
})

SESSION_SUMMARY_FRAGMENT_MAX_CHARS = 1000


def _render_session_summary_prompt(template_key: str) -> str:
    """从唯一 Task Contract 入口读取 Session Summary 静态指令。"""

    from core.prompt_v2.task_templates import (
        TaskTemplateRenderError,
        render_task_prompt,
    )

    rendered = render_task_prompt(template_key, {})
    if not rendered:
        raise TaskTemplateRenderError(f"task {template_key} rendered empty")
    return rendered


class NonRetryableSessionSummaryError(ValueError):
    """输入合同确定性失败，禁止 worker 自动重复调用。"""


_SAFE_SESSION_SUMMARY_ERROR_CODES = frozenset({
    "contains_prompt_control_tag",
    "contains_user_input_tag",
    "json_parse_failed",
    "json_schema_invalid",
    "llm_response_missing_content",
    "possible_tool_contract_leak",
    "quality_issues_present",
    "quality_score_below_threshold",
    "source_turn_ids_mismatch",
    "source_turn_not_eligible",
    "source_turns_empty",
    "summary_contains_current_user_input",
    "summary_empty",
    "summary_fragment_count_invalid",
    "summary_fragment_index_invalid",
    "summary_fragment_manifest_invalid",
    "summary_fragment_size_invalid",
    "summary_inheritance_invalid",
    "summary_input_manifest_mismatch",
    "summary_job_failed",
    "summary_job_lease_lost",
    "summary_mentions_recent_raw_turn",
    "summary_obligation_invalid",
    "summary_prepare_invalid",
    "summary_previous_obligation_budget_exceeded",
    "summary_request_budget_exceeded",
    "summary_request_message_invalid",
    "summary_state_budget_exceeded",
    "summary_state_obligation_budget_exceeded",
    "summary_state_repair_failed",
    "summary_state_repair_invalid",
    "summary_state_output_budget_exceeded",
    "summary_state_output_token_budget_exceeded",
    "summary_too_long",
    "summary_turn_hash_invalid",
    "summary_turn_id_duplicate",
    "summary_turn_id_invalid",
    "sync_summarizer_returned_awaitable",
})

_SAFE_TASK_RUNTIME_FAILURE_CODES = frozenset({
    "authorization_failed",
    "business_validation_failed",
    "cancelled",
    "conflict",
    "contract_version_mismatch",
    "empty_output",
    "execution_timeout",
    "field_out_of_range",
    "invalid_invocation",
    "invalid_json",
    "output_limit_exceeded",
    "permanent_failure",
    "provider_error",
    "provider_unavailable",
    "quota_exceeded",
    "rate_limited",
    "route_unavailable",
    "schema_invalid",
    "template_unavailable",
    "transient_transport",
})


def _safe_session_summary_error(exc: BaseException) -> str:
    """将任意异常收敛为不含请求正文、地址或凭证的稳定错误码。"""

    raw = str(exc or "")
    task_runtime_match = re.fullmatch(
        r"task_runtime_failed:([a-z][a-z0-9_]{2,63})",
        raw.strip(),
    )
    if (
        task_runtime_match is not None
        and task_runtime_match.group(1) in _SAFE_TASK_RUNTIME_FAILURE_CODES
    ):
        return f"task_runtime_failed:{task_runtime_match.group(1)}"
    safe_codes: list[str] = []
    for part in raw.split(","):
        code = part.strip().split(":", 1)[0]
        if (
            re.fullmatch(r"[a-z][a-z0-9_]{2,127}", code)
            and code in _SAFE_SESSION_SUMMARY_ERROR_CODES
            and code not in safe_codes
        ):
            safe_codes.append(code)
    if safe_codes:
        return ",".join(safe_codes)
    error_type = type(exc).__name__
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", error_type):
        error_type = "Exception"
    return f"session_summary_processing_failed:{error_type}"


@dataclass(frozen=True)
class SessionSummaryTurnSnapshot:
    """跨事务传递给摘要器的最小 turn 快照。"""

    id: int
    created_at: datetime | None
    role: str
    content: str
    meta_json: str


SessionSummaryTurn = ConversationTurn | ChatLog | SessionSummaryTurnSnapshot


@dataclass(frozen=True)
class PreparedSessionSummaryJob:
    job_id: int
    lease: SessionSummaryJobLease
    source_turns: tuple[SessionSummaryTurnSnapshot, ...]
    fragments: tuple[TurnFragment, ...]
    manifest: TurnCoverageManifest
    batch_contracts: tuple[SummaryRequestBatch, ...]
    previous_state: dict[str, Any]
    previous_obligations: tuple[SummaryObligation, ...]
    previous_legacy_summary: bool = False
    previous_quality: dict[str, Any] = field(
        default_factory=lambda: {"score": 0.0, "issues": []}
    )
    previous_quality_present: bool = False
    source_type: str = "conversation_turn"
    max_fragment_chars: int = SESSION_SUMMARY_FRAGMENT_MAX_CHARS
    batch_traces: list[SummaryBatchTrace] = field(default_factory=list, compare=False)

    @property
    def messages(self) -> list[dict[str, str]]:
        """兼容旧诊断调用，返回首批消息副本。"""

        if not self.batch_contracts:
            return []
        return [dict(message) for message in self.batch_contracts[0].messages]


@dataclass(frozen=True)
class _AcceptedSummaryPayload:
    """一次模型输出通过来源、预算和 inheritance 审计后的内存态。"""

    business_payload: dict[str, Any]
    state: dict[str, Any]
    obligations: tuple[SummaryObligation, ...]
    inheritance: tuple[dict[str, Any], ...]
    inheritance_audit: InheritanceAudit
    llm_result: SessionSummaryLLMResult
    quality_immutable: bool = True


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid_json_constant:{value}")


def _safe_json_loads(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("json_parse_failed: empty_response")
    try:
        value = json.loads(text, parse_constant=_reject_json_constant)
    except (TypeError, ValueError, json.JSONDecodeError):
        match = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)```", text)
        if not match:
            raise ValueError("json_parse_failed")
        try:
            value = json.loads(
                match.group(1).strip(),
                parse_constant=_reject_json_constant,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("json_parse_failed") from exc
    if not isinstance(value, dict):
        raise ValueError("json_schema_invalid: root_not_object")
    return value


def parse_llm_summary_response(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        payload = dict(raw)
    else:
        payload = _safe_json_loads(str(raw or ""))
    if set(payload) - _SESSION_SUMMARY_ROOT_FIELDS:
        raise ValueError("json_schema_invalid: unexpected_root_fields")
    summary = payload.setdefault("summary", "")
    if type(summary) is not str:
        raise ValueError("json_schema_invalid: summary_not_string")
    for field_name in _SESSION_SUMMARY_LIST_FIELDS:
        items = payload.setdefault(field_name, [])
        if type(items) is not list or any(type(item) is not str for item in items):
            raise ValueError(f"json_schema_invalid: {field_name}_not_string_list")

    quality = payload.get("quality")
    if quality is None:
        quality = {}
    if type(quality) is not dict:
        raise ValueError("json_schema_invalid: quality_not_object")
    if set(quality) - _SESSION_SUMMARY_QUALITY_FIELDS:
        raise ValueError("json_schema_invalid: unexpected_quality_fields")
    score = quality.get("score", 0.0)
    if type(score) not in (int, float):
        raise ValueError("json_schema_invalid: quality_score_invalid")
    try:
        numeric_score = float(score)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("json_schema_invalid: quality_score_invalid") from exc
    if not math.isfinite(numeric_score) or not 0.0 <= numeric_score <= 1.0:
        raise ValueError("json_schema_invalid: quality_score_invalid")
    quality_issues = quality.get("issues", [])
    if (
        type(quality_issues) is not list
        or any(type(item) is not str for item in quality_issues)
    ):
        raise ValueError("json_schema_invalid: quality_issues_not_string_list")
    payload["quality"] = {
        "score": numeric_score,
        "issues": quality_issues,
    }

    inheritance = payload.setdefault("inheritance", [])
    if type(inheritance) is not list or any(
        type(item) is not dict
        or set(item) != _SESSION_SUMMARY_INHERITANCE_FIELDS
        for item in inheritance
    ):
        raise ValueError("json_schema_invalid: inheritance_invalid")
    return payload


def _json_list(value: str | None) -> list[int]:
    try:
        raw = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    result: list[int] = []
    for item in raw:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _load_source_turns(
    db: Session,
    job: SessionSummaryJob,
) -> list[ConversationTurn | ChatLog]:
    from app.session_memory.rolling_summary import normalize_summary_source_type

    source_type = normalize_summary_source_type(
        getattr(job, "source_type", "conversation_turn")
    )
    ids = _json_list(
        job.source_ids_json
        if source_type == "chat_log"
        else job.source_turn_ids_json
    )
    if not ids:
        return []
    source_model = ChatLog if source_type == "chat_log" else ConversationTurn
    rows = (
        db.query(source_model)
        .filter(source_model.id.in_(ids))
        .all()
    )
    by_id = {int(row.id): row for row in rows}
    return [by_id[source_id] for source_id in ids if source_id in by_id]


def _snapshot_turn(
    turn: ConversationTurn | ChatLog,
    *,
    source_type: str = "conversation_turn",
) -> SessionSummaryTurnSnapshot:
    role = str(turn.role or "")
    content = str(turn.content or "")
    if source_type == "chat_log":
        from core.context_builder import format_group_canonical_message

        sender = str(getattr(turn, "sender_name", "") or "").strip()
        if not sender:
            sender = "nanobot" if role == "assistant" else "未知用户"
        meta = safe_meta(getattr(turn, "meta_json", "{}"))
        content = format_group_canonical_message(
            sender_name=sender,
            content=content,
            timestamp=turn.created_at,
            message_id=str(getattr(turn, "message_id", "") or ""),
            directed=meta.get("directed"),
            mentions=meta.get("mentions"),
            reply_to=meta.get("reply_to"),
        )
        if role in {"ambient", "user"}:
            role = "user"
    return SessionSummaryTurnSnapshot(
        id=int(turn.id),
        created_at=turn.created_at,
        role=role,
        content=content,
        meta_json=str(turn.meta_json or "{}"),
    )


def _fragment_source_turns(
    source_turns: list[SessionSummaryTurn] | tuple[SessionSummaryTurnSnapshot, ...],
    *,
    max_fragment_chars: int = SESSION_SUMMARY_FRAGMENT_MAX_CHARS,
) -> tuple[TurnFragment, ...]:
    return tuple(
        fragment
        for turn in source_turns
        for fragment in fragment_summary_turn(
            turn,
            max_fragment_chars=max_fragment_chars,
        )
    )


def build_llm_summary_messages(
    *,
    previous_summary: RollingSessionSummary | None,
    source_turns: list[SessionSummaryTurn],
) -> list[dict[str, str]]:
    fragments = _fragment_source_turns(source_turns)
    if not fragments:
        raise ValueError("source_turns_empty")
    build_coverage_manifest(fragments)
    previous_state = canonical_previous_state(
        previous_summary,
        max_state_chars=config.SESSION_SUMMARY_LLM_MAX_STATE_CHARS,
    )
    obligations = build_previous_summary_obligations(
        previous_summary,
        max_state_chars=config.SESSION_SUMMARY_LLM_MAX_STATE_CHARS,
    )
    batches = build_summary_request_batches(
        system_prompt=_render_session_summary_prompt("tasks/session_summary_system"),
        previous_state=previous_state,
        available_obligations=obligations,
        fragments=fragments,
        output_instruction=_render_session_summary_prompt("tasks/session_summary_output"),
        max_request_chars=config.SESSION_SUMMARY_LLM_MAX_REQUEST_CHARS,
        safety_chars=config.SESSION_SUMMARY_LLM_REQUEST_SAFETY_CHARS,
    )
    if not batches:
        raise ValueError("source_turns_empty")
    return [dict(message) for message in batches[0].messages]


async def default_llm_summary_summarizer_async(
    messages: list[dict[str, str]],
) -> SessionSummaryLLMResult:
    import asyncio

    from core.task_runtime import (
        TaskInvocation,
        execute_task,
        thaw_task_value,
    )

    messages_sha256 = hashlib.sha256(
        json.dumps(
            messages,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    result = await asyncio.to_thread(
        execute_task,
        TaskInvocation(
            invocation_id="session_summary",
            route_key="session_summary",
            input_values={},
            rendered_messages=tuple(messages),
            idempotency_key=f"session_summary:{messages_sha256}",
            timeout_budget_seconds=120.0,
        ),
    )
    if not result.ok:
        failure_code = (
            result.failure.code.value
            if result.failure is not None
            else "provider_error"
        )
        raise RuntimeError(
            f"task_runtime_failed:{failure_code}"
        )
    payload = thaw_task_value(result.parsed_value)
    actual_model = str(result.model or "unknown").strip() or "unknown"
    requested_model = str(
        result.execution_metadata.get("requested_model")
        or actual_model
    ).strip() or "unknown"
    raw_log_id = result.execution_metadata.get("request_log_id")
    request_log_id = (
        int(raw_log_id)
        if type(raw_log_id) is int and raw_log_id > 0
        else None
    )
    return SessionSummaryLLMResult(
        content=json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        model=actual_model,
        requested_model=requested_model,
        request_log_id=request_log_id,
    )


def default_llm_summary_summarizer(messages: list[dict[str, str]]) -> str:
    raise RuntimeError(
        "sync_summarizer_required: use default_llm_summary_summarizer_async "
        "with process_claimed_session_summary_job_short_transactions_async"
    )


def _close_awaitable(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        close()


def _call_summarizer(
    summarizer: Callable[[list[dict[str, str]]], Any],
    messages: list[dict[str, str]],
) -> Any:
    result = summarizer(messages)
    if inspect.isawaitable(result):
        _close_awaitable(result)
        raise TypeError(
            "sync_summarizer_returned_awaitable: "
            "use process_claimed_session_summary_job_short_transactions_async"
        )
    return result


def _normalize_llm_result(raw: Any) -> SessionSummaryLLMResult:
    if isinstance(raw, SessionSummaryLLMResult):
        model = str(raw.model or "unknown").strip() or "unknown"
        requested_model = (
            str(raw.requested_model or model).strip() or model
        )
        request_log_id = (
            int(raw.request_log_id)
            if isinstance(raw.request_log_id, int)
            and not isinstance(raw.request_log_id, bool)
            and raw.request_log_id > 0
            else None
        )
        return SessionSummaryLLMResult(
            content=raw.content,
            model=model,
            requested_model=requested_model,
            request_log_id=request_log_id,
        )
    return SessionSummaryLLMResult(
        content=raw,
        model="custom_summarizer",
        requested_model="custom_summarizer",
        request_log_id=None,
    )


async def _call_summarizer_async(
    summarizer: Callable[[list[dict[str, str]]], Any],
    messages: list[dict[str, str]],
) -> Any:
    result = summarizer(messages)
    if inspect.isawaitable(result):
        return await result
    return result


def audit_llm_session_summary(
    *,
    payload: dict[str, Any],
    source_turns: list[SessionSummaryTurn],
    job: SessionSummaryJob,
) -> tuple[bool, list[str]]:
    issues: list[str] = []
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        issues.append("summary_empty")
    if len(summary) > config.ROLLING_SUMMARY_MAX_CHARS:
        issues.append("summary_too_long")

    for key in [
        "open_threads",
        "decisions",
        "important_user_requests",
        "resolved_items",
        "artifacts",
        "participants",
        "keywords",
    ]:
        if not isinstance(payload.get(key), list):
            issues.append(f"{key}_not_list")

    source_type = normalize_summary_source_type(
        getattr(job, "source_type", SUMMARY_SOURCE_CONVERSATION_TURN)
    )
    expected_ids = _json_list(
        getattr(job, "source_ids_json", "[]")
        if source_type == "chat_log"
        else getattr(job, "source_turn_ids_json", "[]")
    )
    actual_ids = [int(turn.id) for turn in source_turns]
    if expected_ids != actual_ids:
        issues.append("source_turn_ids_mismatch")

    eligibility_check = (
        is_context_eligible_chat_log
        if source_type == "chat_log"
        else is_context_eligible_turn
    )
    for turn in source_turns:
        ok, reason = eligibility_check(turn)
        if not ok:
            issues.append(f"source_turn_not_eligible:{turn.id}:{reason}")

    # 模型自评分保留为观测数据；晋升只由结构、来源和内容审计决定。
    quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
    quality_issues = quality.get("issues")
    quality_issues = quality_issues if isinstance(quality_issues, list) else []
    blocking_quality_issues = [
        str(item) for item in quality_issues
        if not str(item).lower().startswith(("warning", "warn:", "警告"))
    ]
    if blocking_quality_issues:
        issues.append("quality_issues_present")

    try:
        job_meta = json.loads(getattr(job, "meta_json", "{}") or "{}")
        if not isinstance(job_meta, dict):
            job_meta = {}
    except (TypeError, json.JSONDecodeError):
        job_meta = {}
    recent_raw_turn_ids = job_meta.get("recent_raw_turn_ids")
    if not isinstance(recent_raw_turn_ids, list):
        recent_raw_turn_ids = []
    current_user_input = str(job_meta.get("current_user_input") or "")
    audit_ok, audit_issues = audit_rolling_summary(
        summary_json=payload,
        pending_turn_ids=actual_ids,
        recent_raw_turn_ids=[
            int(item) for item in recent_raw_turn_ids if str(item).isdigit()
        ],
        current_user_input=current_user_input,
    )
    if not audit_ok:
        issues.extend(audit_issues)

    text = json.dumps(payload, ensure_ascii=False)
    forbidden_tags = ["<system", "</system>", "<user_input", "</user_input>", "<runtime_context"]
    if any(tag in text for tag in forbidden_tags):
        issues.append("contains_prompt_control_tag")

    return not issues, issues


def _audit_intermediate_summary_payload(
    payload: dict[str, Any],
    source_turns: tuple[SessionSummaryTurnSnapshot, ...],
    *,
    source_type: str,
) -> None:
    turn_ids = [int(turn.id) for turn in source_turns]
    ephemeral_job = SimpleNamespace(
        source_turn_ids_json=json.dumps(turn_ids, ensure_ascii=False),
        source_ids_json=json.dumps(turn_ids, ensure_ascii=False),
        source_type=source_type,
        meta_json="{}",
    )
    ok, issues = audit_llm_session_summary(
        payload=payload,
        source_turns=list(source_turns),
        job=ephemeral_job,
    )
    if not ok:
        raise ValueError(",".join(issues))


def _prepared_fragment_hashes(
    prepared: PreparedSessionSummaryJob,
) -> tuple[str, ...]:
    return tuple(
        fragment_hash
        for batch in prepared.batch_contracts
        for fragment_hash in batch.fragment_hashes
    )


def _validate_prepared_coverage(prepared: PreparedSessionSummaryJob) -> None:
    batch_fragments = tuple(
        fragment
        for batch in prepared.batch_contracts
        for fragment in batch.fragments
    )
    if (
        batch_fragments != prepared.fragments
        or _prepared_fragment_hashes(prepared) != prepared.manifest.fragment_hashes
    ):
        raise ValueError("summary_input_manifest_mismatch")


def _validate_completed_coverage(prepared: PreparedSessionSummaryJob) -> None:
    completed_hashes = tuple(
        fragment_hash
        for trace in prepared.batch_traces
        for fragment_hash in trace.fragment_hashes
    )
    if (
        tuple(trace.batch_index for trace in prepared.batch_traces)
        != tuple(range(len(prepared.batch_traces)))
        or completed_hashes != prepared.manifest.fragment_hashes
    ):
        raise ValueError("summary_input_manifest_mismatch")


def _source_turns_for_batch(
    prepared: PreparedSessionSummaryJob,
    batch: SummaryRequestBatch,
) -> tuple[SessionSummaryTurnSnapshot, ...]:
    turn_ids = {fragment.turn_id for fragment in batch.fragments}
    return tuple(turn for turn in prepared.source_turns if turn.id in turn_ids)


def _build_bounded_summary_obligations(
    state: dict[str, Any],
) -> tuple[SummaryObligation, ...]:
    """限制下一批 inheritance 合同规模，避免 1200 tokens 被审计结构耗尽。"""

    summary = str(state.get("summary") or "")
    obligations = build_summary_obligations(state)
    if len(obligations) > SESSION_SUMMARY_MAX_STATE_OBLIGATIONS:
        raise ValueError("summary_state_obligation_budget_exceeded")
    if (
        len(summary) > SESSION_SUMMARY_MAX_SUMMARY_CHARS
        or any(
            len(obligation.normalized_text) > SESSION_SUMMARY_MAX_OBLIGATION_CHARS
            for obligation in obligations
        )
    ):
        raise NonRetryableSessionSummaryError(
            "summary_state_output_budget_exceeded"
        )
    return obligations


def _compact_raw_json_for_budget(raw: str) -> str:
    """移除 JSON 字符串外的排版空白，同时保留转义与字符串正文。"""

    text = str(raw or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        text = fenced.group(1).strip()

    compacted: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            compacted.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char in " \t\r\n":
            continue
        compacted.append(char)
        if char == '"':
            in_string = True
    return "".join(compacted)


def _validate_summary_response_budget(
    payload: dict[str, Any],
    *,
    raw_content: Any = None,
) -> None:
    """按紧凑 JSON 校验完整模型响应，覆盖 quality 与 inheritance 开销。"""

    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("json_schema_invalid: non_serializable") from exc
    raw_text = raw_content if type(raw_content) is str else serialized
    raw_budget_text = (
        _compact_raw_json_for_budget(raw_text)
        if type(raw_content) is str
        else serialized
    )
    if max(
        len(serialized),
        len(raw_budget_text),
    ) > config.SESSION_SUMMARY_LLM_MAX_OUTPUT_CHARS:
        raise NonRetryableSessionSummaryError(
            "summary_state_output_budget_exceeded"
        )
    if max(
        estimate_tokens(serialized),
        estimate_tokens(raw_budget_text),
    ) > SESSION_SUMMARY_MAX_ESTIMATED_OUTPUT_TOKENS:
        raise NonRetryableSessionSummaryError(
            "summary_state_output_token_budget_exceeded"
        )


def _validate_previous_obligation_budget(
    obligations: tuple[SummaryObligation, ...],
) -> None:
    """保留旧调用方的显式门禁，但不再用于 worker 的首轮准备。"""

    if len(obligations) > SESSION_SUMMARY_MAX_STATE_OBLIGATIONS:
        raise NonRetryableSessionSummaryError(
            "summary_previous_obligation_budget_exceeded"
        )


def _build_next_request_batch(
    *,
    state: dict[str, Any],
    obligations: tuple[SummaryObligation, ...],
    fragments: tuple[TurnFragment, ...],
    batch_index: int,
) -> SummaryRequestBatch:
    batches = build_summary_request_batches(
        system_prompt=_render_session_summary_prompt("tasks/session_summary_system"),
        previous_state=state,
        available_obligations=obligations,
        fragments=fragments,
        output_instruction=_render_session_summary_prompt("tasks/session_summary_output"),
        max_request_chars=config.SESSION_SUMMARY_LLM_MAX_REQUEST_CHARS,
        safety_chars=config.SESSION_SUMMARY_LLM_REQUEST_SAFETY_CHARS,
        start_batch_index=batch_index,
    )
    if not batches:
        raise ValueError("summary_input_manifest_mismatch")
    return batches[0]


def _with_bounded_summary_obligations(
    accepted: _AcceptedSummaryPayload,
    *,
    repair_response: bool = False,
) -> _AcceptedSummaryPayload:
    try:
        obligations = _build_bounded_summary_obligations(accepted.state)
    except ValueError as exc:
        if (
            repair_response
            and str(exc) == "summary_state_obligation_budget_exceeded"
        ):
            raise NonRetryableSessionSummaryError(
                "summary_state_obligation_budget_exceeded"
            ) from exc
        raise
    return _AcceptedSummaryPayload(
        business_payload=accepted.business_payload,
        state=accepted.state,
        obligations=obligations,
        inheritance=accepted.inheritance,
        inheritance_audit=accepted.inheritance_audit,
        llm_result=accepted.llm_result,
        quality_immutable=accepted.quality_immutable,
    )


def _output_budget_repair_overflow(
    accepted: _AcceptedSummaryPayload,
) -> _AcceptedSummaryPayload:
    """为长度修复补上 summary 继承义务，防止压缩时丢失累计事实。"""

    summary_oversized = (
        len(str(accepted.state.get("summary") or ""))
        > SESSION_SUMMARY_MAX_SUMMARY_CHARS
    )
    return _AcceptedSummaryPayload(
        business_payload=accepted.business_payload,
        state=accepted.state,
        obligations=build_summary_obligations(
            accepted.state,
            legacy_summary=summary_oversized,
        ),
        inheritance=accepted.inheritance,
        inheritance_audit=accepted.inheritance_audit,
        llm_result=accepted.llm_result,
        quality_immutable=accepted.quality_immutable,
    )


def _accept_summary_payload(
    *,
    raw: Any,
    obligations: tuple[SummaryObligation, ...],
    source_turns: tuple[SessionSummaryTurnSnapshot, ...] | None = None,
    source_type: str = SUMMARY_SOURCE_CONVERSATION_TURN,
) -> _AcceptedSummaryPayload:
    """解析一次模型输出并验证来源、预算和 inheritance。"""

    llm_result = _normalize_llm_result(raw)
    payload = parse_llm_summary_response(llm_result.content)
    _validate_summary_response_budget(
        payload,
        raw_content=llm_result.content,
    )
    normalization = normalize_inheritance_metadata(
        payload,
        obligations,
        max_state_chars=config.SESSION_SUMMARY_LLM_MAX_STATE_CHARS,
    )
    payload = normalization.payload
    inheritance_audit = validate_inheritance(
        payload,
        obligations,
        max_state_chars=config.SESSION_SUMMARY_LLM_MAX_STATE_CHARS,
        normalized_count=normalization.normalized_count,
    )
    business_payload = strip_summary_inheritance(payload)
    if source_turns is not None:
        _audit_intermediate_summary_payload(
            business_payload,
            source_turns,
            source_type=source_type,
        )
    state = canonical_summary_state(
        business_payload,
        max_state_chars=config.SESSION_SUMMARY_LLM_MAX_STATE_CHARS,
    )
    return _AcceptedSummaryPayload(
        business_payload=business_payload,
        state=state,
        obligations=build_summary_obligations(state),
        inheritance=tuple(
            item for item in payload.get("inheritance", [])
            if isinstance(item, dict)
        ),
        inheritance_audit=inheritance_audit,
        llm_result=llm_result,
        quality_immutable=True,
    )


def _build_summary_repair_field_limits(
    field_counts: Mapping[str, int],
) -> dict[str, int]:
    """为局部修复生成确定性的逐字段目标配额。"""

    counts = {
        field: max(0, int(field_counts.get(field, 0) or 0))
        for field in _SESSION_SUMMARY_REPAIRABLE_FIELDS
    }
    limits = {
        field: 1 if counts[field] else 0
        for field in _SESSION_SUMMARY_REPAIRABLE_FIELDS
    }
    remaining = max(
        0,
        SESSION_SUMMARY_REPAIR_TARGET_OBLIGATIONS - sum(limits.values()),
    )
    field_order = {
        field: index
        for index, field in enumerate(_SESSION_SUMMARY_REPAIRABLE_FIELDS)
    }
    while remaining:
        candidates = [
            field
            for field in _SESSION_SUMMARY_REPAIRABLE_FIELDS
            if limits[field] < counts[field]
        ]
        if not candidates:
            break
        field = max(
            candidates,
            key=lambda item: (
                counts[item] - limits[item],
                counts[item],
                -field_order[item],
            ),
        )
        limits[field] += 1
        remaining -= 1
    return limits


def _state_inheritance_audit(
    state: Mapping[str, Any],
    obligations: tuple[SummaryObligation, ...],
) -> InheritanceAudit:
    canonical = canonical_summary_state(
        state,
        max_state_chars=config.SESSION_SUMMARY_LLM_MAX_STATE_CHARS,
    )
    state_json = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return InheritanceAudit(
        obligation_count=len(obligations),
        carried_count=0,
        updated_count=0,
        resolved_count=0,
        state_sha256=hashlib.sha256(state_json.encode("utf-8")).hexdigest(),
    )


def _previous_repair_quality(previous: Any | None) -> dict[str, Any]:
    """读取旧摘要质量；旧记录缺失时使用数据库中的保守默认值。"""

    quality: Any = None
    raw_summary = getattr(previous, "summary_json", None) if previous is not None else None
    if isinstance(raw_summary, str) and raw_summary.strip():
        try:
            parsed = json.loads(raw_summary)
        except (TypeError, json.JSONDecodeError) as exc:
            raise NonRetryableSessionSummaryError("json_schema_invalid") from exc
        if isinstance(parsed, dict) and "quality" in parsed:
            quality = parsed.get("quality")
    if quality is None:
        raw_issues = getattr(previous, "issues_json", "[]") if previous is not None else "[]"
        try:
            issues = json.loads(raw_issues or "[]")
        except (TypeError, json.JSONDecodeError):
            issues = []
        if not isinstance(issues, list) or any(type(item) is not str for item in issues):
            issues = []
        raw_score = getattr(previous, "quality_score", 0.0) if previous is not None else 0.0
        try:
            score = float(raw_score or 0.0)
        except (TypeError, ValueError, OverflowError):
            score = 0.0
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            score = 0.0
        return {"score": score, "issues": issues}
    if type(quality) is not dict or set(quality) - _SESSION_SUMMARY_QUALITY_FIELDS:
        raise NonRetryableSessionSummaryError("json_schema_invalid")
    score = quality.get("score", 0.0)
    issues = quality.get("issues", [])
    if type(score) not in (int, float) or type(issues) is not list:
        raise NonRetryableSessionSummaryError("json_schema_invalid")
    try:
        score = float(score)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NonRetryableSessionSummaryError("json_schema_invalid") from exc
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise NonRetryableSessionSummaryError("json_schema_invalid")
    if any(type(item) is not str for item in issues):
        raise NonRetryableSessionSummaryError("json_schema_invalid")
    return {"score": score, "issues": list(issues)}


def _build_summary_repair_messages(
    overflow: _AcceptedSummaryPayload,
    *,
    repair_kind: str = "state_obligation_budget",
) -> list[dict[str, str]]:
    """只回传失败状态和脱敏索引账本，不重发 ConversationTurn。"""

    field_indexes: dict[str, int] = {}
    field_counts: dict[str, int] = {}
    ledger: list[dict[str, Any]] = []
    for obligation in overflow.obligations:
        source_index = field_indexes.get(obligation.field, 0)
        field_indexes[obligation.field] = source_index + 1
        field_counts[obligation.field] = field_counts.get(obligation.field, 0) + 1
        ledger.append({
            "source_id": obligation.source_id,
            "field": obligation.field,
            "source_index": source_index,
        })

    normalized_repair_kind = str(
        repair_kind or "state_obligation_budget"
    )
    output_budget_repair = normalized_repair_kind == "state_output_budget"
    if output_budget_repair:
        violation = {
            "code": "summary_state_output_budget_exceeded",
            "kind": normalized_repair_kind,
            "summary_chars": len(str(overflow.state.get("summary") or "")),
            "summary_max_chars": SESSION_SUMMARY_MAX_SUMMARY_CHARS,
            "summary_editable": any(
                obligation.field == "legacy_summary"
                for obligation in overflow.obligations
            ),
            "obligation_max_chars": SESSION_SUMMARY_MAX_OBLIGATION_CHARS,
            "oversized_fields": sorted({
                obligation.field
                for obligation in overflow.obligations
                if obligation.field != "legacy_summary"
                and len(obligation.normalized_text)
                > SESSION_SUMMARY_MAX_OBLIGATION_CHARS
            }),
            "field_counts": field_counts,
            "field_target_limits": field_counts,
        }
    else:
        violation = {
            "code": "summary_state_obligation_budget_exceeded",
            "kind": normalized_repair_kind,
            "actual_count": len(overflow.obligations),
            "target_count": SESSION_SUMMARY_REPAIR_TARGET_OBLIGATIONS,
            "hard_limit": SESSION_SUMMARY_MAX_STATE_OBLIGATIONS,
            "field_counts": field_counts,
            "field_target_limits": _build_summary_repair_field_limits(
                field_counts
            ),
        }
    violation_json = json.dumps(
        violation,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    state_json = json.dumps(
        overflow.business_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    ledger_json = json.dumps(
        ledger,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    user_prompt = (
        "<summary_repair>\n"
        "<contract_violation>\n"
        f"{html.escape(violation_json, quote=False)}\n"
        "</contract_violation>\n\n"
        "<failed_summary_state>\n"
        f"{html.escape(state_json, quote=False)}\n"
        "</failed_summary_state>\n\n"
        "<repair_obligation_ledger>\n"
        f"{html.escape(ledger_json, quote=False)}\n"
        "</repair_obligation_ledger>\n\n"
        "请执行本次摘要合同局部修复。\n"
        "</summary_repair>"
    )
    messages = [
        {
            "role": "system",
            "content": _render_session_summary_prompt(
                "tasks/session_summary_system"
            ),
        },
        {
            "role": "user",
            "content": (
                _render_session_summary_prompt("tasks/session_summary_output")
                + "\n\n"
                + user_prompt
            ),
        },
    ]
    request_limit = (
        int(config.SESSION_SUMMARY_LLM_MAX_REQUEST_CHARS)
        - int(config.SESSION_SUMMARY_LLM_REQUEST_SAFETY_CHARS)
    )
    if request_limit <= 0 or request_char_count(messages) > request_limit:
        raise NonRetryableSessionSummaryError("summary_request_budget_exceeded")
    return messages


_SESSION_SUMMARY_REPAIRABLE_FIELDS = (
    "open_threads",
    "decisions",
    "important_user_requests",
    "artifacts",
)
_SESSION_SUMMARY_REPAIR_IMMUTABLE_FIELDS = (
    "summary",
    "resolved_items",
    "participants",
    "keywords",
    "quality",
)


def _accept_summary_repair_payload(
    *,
    raw: Any,
    overflow: _AcceptedSummaryPayload,
    source_turns: tuple[SessionSummaryTurnSnapshot, ...] = (),
    repair_kind: str = "state_obligation_budget",
) -> _AcceptedSummaryPayload:
    """验证单次局部修复没有改变不可变状态或凭空增加目标项。"""

    try:
        repaired = _accept_summary_payload(
            raw=raw,
            obligations=overflow.obligations,
            source_turns=None,
        )
    except NonRetryableSessionSummaryError:
        raise
    except (TypeError, ValueError) as exc:
        safe_error = _safe_session_summary_error(exc)
        if safe_error in {
            "json_parse_failed",
            "json_schema_invalid",
            "summary_inheritance_invalid",
            "summary_state_budget_exceeded",
            "summary_state_output_budget_exceeded",
            "summary_state_output_token_budget_exceeded",
        }:
            raise NonRetryableSessionSummaryError(safe_error) from exc
        raise NonRetryableSessionSummaryError(
            "summary_state_repair_invalid"
        ) from exc

    output_budget_repair = repair_kind == "state_output_budget"
    summary_editable = output_budget_repair and any(
        obligation.field == "legacy_summary"
        for obligation in overflow.obligations
    )
    immutable_fields = tuple(
        field
        for field in _SESSION_SUMMARY_REPAIR_IMMUTABLE_FIELDS
        if (overflow.quality_immutable or field != "quality")
        and (not summary_editable or field != "summary")
    )
    if any(
        repaired.business_payload.get(field)
        != overflow.business_payload.get(field)
        for field in immutable_fields
    ):
        raise NonRetryableSessionSummaryError("summary_state_repair_invalid")
    if repaired.inheritance_audit.resolved_count:
        raise NonRetryableSessionSummaryError("summary_state_repair_invalid")

    field_counts = {
        field: sum(1 for obligation in overflow.obligations if obligation.field == field)
        for field in _SESSION_SUMMARY_REPAIRABLE_FIELDS
    }
    limits = (
        field_counts
        if output_budget_repair
        else _build_summary_repair_field_limits(field_counts)
    )
    for field_name in _SESSION_SUMMARY_REPAIRABLE_FIELDS:
        values = repaired.business_payload.get(field_name)
        if not isinstance(values, list) or len(values) > limits[field_name]:
            raise NonRetryableSessionSummaryError("summary_state_repair_invalid")

    expected_targets = {
        (field, index)
        for field in _SESSION_SUMMARY_REPAIRABLE_FIELDS
        for index, _item in enumerate(
            repaired.business_payload.get(field, [])
        )
    }
    if summary_editable:
        expected_targets.add(("summary", 0))
    inherited_targets = {
        (str(item.get("target_field")), int(item.get("target_index")))
        for item in repaired.inheritance
        if item.get("disposition") in {"carried", "updated"}
        and isinstance(item.get("target_index"), int)
        and not isinstance(item.get("target_index"), bool)
    }
    if inherited_targets != expected_targets:
        raise NonRetryableSessionSummaryError("summary_state_repair_invalid")
    if (
        not output_budget_repair
        and len(repaired.obligations)
        > SESSION_SUMMARY_REPAIR_TARGET_OBLIGATIONS
    ):
        raise NonRetryableSessionSummaryError(
            "summary_state_obligation_budget_exceeded"
        )

    # repair 没有新的来源证据；如果调用方提供快照，只做一次常规内容门禁。
    if source_turns:
        _audit_intermediate_summary_payload(
            repaired.business_payload,
            source_turns,
            source_type=SUMMARY_SOURCE_CONVERSATION_TURN,
        )
    return _with_bounded_summary_obligations(
        repaired,
        repair_response=True,
    )


def _call_summary_repair_sync(
    *,
    overflow: _AcceptedSummaryPayload,
    summarizer: Callable[[list[dict[str, str]]], Any],
    repair_kind: str,
) -> _AcceptedSummaryPayload:
    messages = _build_summary_repair_messages(
        overflow,
        repair_kind=repair_kind,
    )
    try:
        raw = _call_summarizer(summarizer, messages)
    except Exception as exc:
        raise NonRetryableSessionSummaryError(
            "summary_state_repair_failed"
        ) from exc
    return _accept_summary_repair_payload(
        raw=raw,
        overflow=overflow,
        repair_kind=repair_kind,
    )


async def _call_summary_repair_async(
    *,
    overflow: _AcceptedSummaryPayload,
    summarizer: Callable[[list[dict[str, str]]], Any],
    repair_kind: str,
) -> _AcceptedSummaryPayload:
    messages = _build_summary_repair_messages(
        overflow,
        repair_kind=repair_kind,
    )
    try:
        raw = await _call_summarizer_async(summarizer, messages)
    except Exception as exc:
        raise NonRetryableSessionSummaryError(
            "summary_state_repair_failed"
        ) from exc
    return _accept_summary_repair_payload(
        raw=raw,
        overflow=overflow,
        repair_kind=repair_kind,
    )


def _accept_summary_batch_payload(
    *,
    raw: Any,
    obligations: tuple[SummaryObligation, ...],
    prepared: PreparedSessionSummaryJob,
    batch: SummaryRequestBatch,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    tuple[SummaryObligation, ...],
    InheritanceAudit,
    SessionSummaryLLMResult,
]:
    accepted = _accept_summary_payload(
        raw=raw,
        obligations=obligations,
        source_turns=_source_turns_for_batch(prepared, batch),
        source_type=prepared.source_type,
    )
    accepted = _with_bounded_summary_obligations(accepted)
    return (
        accepted.business_payload,
        accepted.state,
        accepted.obligations,
        accepted.inheritance_audit,
        accepted.llm_result,
    )


def _previous_overflow_payload(
    prepared: PreparedSessionSummaryJob,
) -> _AcceptedSummaryPayload:
    business_payload = {
        **prepared.previous_state,
        "quality": {
            "score": float(prepared.previous_quality.get("score", 0.0) or 0.0),
            "issues": list(prepared.previous_quality.get("issues", [])),
        },
    }
    return _AcceptedSummaryPayload(
        business_payload=business_payload,
        state=prepared.previous_state,
        obligations=prepared.previous_obligations,
        inheritance=(),
        inheritance_audit=_state_inheritance_audit(
            prepared.previous_state,
            prepared.previous_obligations,
        ),
        llm_result=SessionSummaryLLMResult(
            content=business_payload,
            model="previous_summary",
            requested_model="previous_summary",
            request_log_id=None,
        ),
        quality_immutable=prepared.previous_quality_present,
    )


def _summarize_prepared_sync(
    prepared: PreparedSessionSummaryJob,
    summarizer: Callable[[list[dict[str, str]]], Any],
    *,
    renew_lease: Callable[[], bool] | None = None,
) -> Any:
    _validate_prepared_coverage(prepared)
    prepared.batch_traces.clear()
    remaining = prepared.fragments
    state = prepared.previous_state
    obligations = prepared.previous_obligations
    completed_hashes: list[str] = []
    final_payload: dict[str, Any] | None = None
    final_result: SessionSummaryLLMResult | None = None
    batch_index = 0
    pending_repair: tuple[str, _AcceptedSummaryPayload] | None = None
    if len(obligations) > SESSION_SUMMARY_MAX_STATE_OBLIGATIONS:
        if renew_lease is not None and not renew_lease():
            raise ValueError("summary_job_lease_lost")
        repaired_previous = _call_summary_repair_sync(
            overflow=_previous_overflow_payload(prepared),
            summarizer=summarizer,
            repair_kind="previous_state_obligation_budget",
        )
        state = repaired_previous.state
        obligations = repaired_previous.obligations
        pending_repair = (
            "previous_state_obligation_budget",
            repaired_previous,
        )
    while remaining:
        batch = _build_next_request_batch(
            state=state,
            obligations=obligations,
            fragments=remaining,
            batch_index=batch_index,
        )
        raw = _call_summarizer(
            summarizer,
            [dict(message) for message in batch.messages],
        )
        source_turns = _source_turns_for_batch(prepared, batch)
        primary = _accept_summary_payload(
            raw=raw,
            obligations=obligations,
            source_turns=source_turns,
            source_type=prepared.source_type,
        )
        repair_attempts = 0
        batch_repair: tuple[str, _AcceptedSummaryPayload] | None = None
        if len(primary.obligations) > SESSION_SUMMARY_MAX_STATE_OBLIGATIONS:
            if repair_attempts >= SESSION_SUMMARY_MAX_REPAIR_ATTEMPTS:
                raise NonRetryableSessionSummaryError(
                    "summary_state_obligation_budget_exceeded"
                )
            repair_attempts += 1
            if renew_lease is not None and not renew_lease():
                raise ValueError("summary_job_lease_lost")
            repaired = _call_summary_repair_sync(
                overflow=primary,
                summarizer=summarizer,
                repair_kind="state_obligation_budget",
            )
            accepted = repaired
            batch_repair = ("state_obligation_budget", repaired)
        else:
            try:
                accepted = _with_bounded_summary_obligations(primary)
            except NonRetryableSessionSummaryError as exc:
                if (
                    str(exc) != "summary_state_output_budget_exceeded"
                    or repair_attempts
                    >= SESSION_SUMMARY_MAX_REPAIR_ATTEMPTS
                ):
                    raise
                repair_attempts += 1
                if renew_lease is not None and not renew_lease():
                    raise ValueError("summary_job_lease_lost")
                repaired = _call_summary_repair_sync(
                    overflow=_output_budget_repair_overflow(primary),
                    summarizer=summarizer,
                    repair_kind="state_output_budget",
                )
                accepted = repaired
                batch_repair = ("state_output_budget", repaired)

        final_payload = accepted.business_payload
        state = accepted.state
        obligations = accepted.obligations
        final_result = accepted.llm_result
        trace_repair = batch_repair or pending_repair
        prepared.batch_traces.append(SummaryBatchTrace(
            batch_index=batch.batch_index,
            fragment_hashes=batch.fragment_hashes,
            inheritance_audit=primary.inheritance_audit,
            model=primary.llm_result.model,
            requested_model=primary.llm_result.requested_model,
            request_log_id=primary.llm_result.request_log_id,
            repair_kind=(trace_repair[0] if trace_repair is not None else ""),
            repair_inheritance_audit=(
                trace_repair[1].inheritance_audit
                if trace_repair is not None else None
            ),
            repair_model=(
                trace_repair[1].llm_result.model
                if trace_repair is not None else ""
            ),
            repair_requested_model=(
                trace_repair[1].llm_result.requested_model
                if trace_repair is not None else ""
            ),
            repair_request_log_id=(
                trace_repair[1].llm_result.request_log_id
                if trace_repair is not None else None
            ),
        ))
        pending_repair = None
        if renew_lease is not None and not renew_lease():
            raise ValueError("summary_job_lease_lost")
        completed_hashes.extend(batch.fragment_hashes)
        remaining = remaining[len(batch.fragments):]
        batch_index += 1
    if (
        tuple(completed_hashes) != prepared.manifest.fragment_hashes
        or final_payload is None
        or final_result is None
    ):
        raise ValueError("summary_input_manifest_mismatch")
    return SessionSummaryLLMResult(
        content=final_payload,
        model=final_result.model,
        requested_model=final_result.requested_model,
        request_log_id=final_result.request_log_id,
    )


async def _summarize_prepared_async(
    prepared: PreparedSessionSummaryJob,
    summarizer: Callable[[list[dict[str, str]]], Any],
    *,
    renew_lease: Callable[[], Any] | None = None,
) -> Any:
    _validate_prepared_coverage(prepared)
    prepared.batch_traces.clear()
    remaining = prepared.fragments
    state = prepared.previous_state
    obligations = prepared.previous_obligations
    completed_hashes: list[str] = []
    final_payload: dict[str, Any] | None = None
    final_result: SessionSummaryLLMResult | None = None
    batch_index = 0
    pending_repair: tuple[str, _AcceptedSummaryPayload] | None = None
    if len(obligations) > SESSION_SUMMARY_MAX_STATE_OBLIGATIONS:
        if renew_lease is not None:
            renewed = renew_lease()
            if inspect.isawaitable(renewed):
                renewed = await renewed
            if not renewed:
                raise ValueError("summary_job_lease_lost")
        repaired_previous = await _call_summary_repair_async(
            overflow=_previous_overflow_payload(prepared),
            summarizer=summarizer,
            repair_kind="previous_state_obligation_budget",
        )
        state = repaired_previous.state
        obligations = repaired_previous.obligations
        pending_repair = (
            "previous_state_obligation_budget",
            repaired_previous,
        )
    while remaining:
        batch = _build_next_request_batch(
            state=state,
            obligations=obligations,
            fragments=remaining,
            batch_index=batch_index,
        )
        raw = await _call_summarizer_async(
            summarizer,
            [dict(message) for message in batch.messages],
        )
        source_turns = _source_turns_for_batch(prepared, batch)
        primary = _accept_summary_payload(
            raw=raw,
            obligations=obligations,
            source_turns=source_turns,
            source_type=prepared.source_type,
        )
        repair_attempts = 0
        batch_repair: tuple[str, _AcceptedSummaryPayload] | None = None
        if len(primary.obligations) > SESSION_SUMMARY_MAX_STATE_OBLIGATIONS:
            if repair_attempts >= SESSION_SUMMARY_MAX_REPAIR_ATTEMPTS:
                raise NonRetryableSessionSummaryError(
                    "summary_state_obligation_budget_exceeded"
                )
            repair_attempts += 1
            if renew_lease is not None:
                renewed = renew_lease()
                if inspect.isawaitable(renewed):
                    renewed = await renewed
                if not renewed:
                    raise ValueError("summary_job_lease_lost")
            repaired = await _call_summary_repair_async(
                overflow=primary,
                summarizer=summarizer,
                repair_kind="state_obligation_budget",
            )
            accepted = repaired
            batch_repair = ("state_obligation_budget", repaired)
        else:
            try:
                accepted = _with_bounded_summary_obligations(primary)
            except NonRetryableSessionSummaryError as exc:
                if (
                    str(exc) != "summary_state_output_budget_exceeded"
                    or repair_attempts
                    >= SESSION_SUMMARY_MAX_REPAIR_ATTEMPTS
                ):
                    raise
                repair_attempts += 1
                if renew_lease is not None:
                    renewed = renew_lease()
                    if inspect.isawaitable(renewed):
                        renewed = await renewed
                    if not renewed:
                        raise ValueError("summary_job_lease_lost")
                repaired = await _call_summary_repair_async(
                    overflow=_output_budget_repair_overflow(primary),
                    summarizer=summarizer,
                    repair_kind="state_output_budget",
                )
                accepted = repaired
                batch_repair = ("state_output_budget", repaired)

        final_payload = accepted.business_payload
        state = accepted.state
        obligations = accepted.obligations
        final_result = accepted.llm_result
        trace_repair = batch_repair or pending_repair
        prepared.batch_traces.append(SummaryBatchTrace(
            batch_index=batch.batch_index,
            fragment_hashes=batch.fragment_hashes,
            inheritance_audit=primary.inheritance_audit,
            model=primary.llm_result.model,
            requested_model=primary.llm_result.requested_model,
            request_log_id=primary.llm_result.request_log_id,
            repair_kind=(trace_repair[0] if trace_repair is not None else ""),
            repair_inheritance_audit=(
                trace_repair[1].inheritance_audit
                if trace_repair is not None else None
            ),
            repair_model=(
                trace_repair[1].llm_result.model
                if trace_repair is not None else ""
            ),
            repair_requested_model=(
                trace_repair[1].llm_result.requested_model
                if trace_repair is not None else ""
            ),
            repair_request_log_id=(
                trace_repair[1].llm_result.request_log_id
                if trace_repair is not None else None
            ),
        ))
        pending_repair = None
        if renew_lease is not None:
            renewed = renew_lease()
            if inspect.isawaitable(renewed):
                renewed = await renewed
            if not renewed:
                raise ValueError("summary_job_lease_lost")
        completed_hashes.extend(batch.fragment_hashes)
        remaining = remaining[len(batch.fragments):]
        batch_index += 1
    if (
        tuple(completed_hashes) != prepared.manifest.fragment_hashes
        or final_payload is None
        or final_result is None
    ):
        raise ValueError("summary_input_manifest_mismatch")
    return SessionSummaryLLMResult(
        content=final_payload,
        model=final_result.model,
        requested_model=final_result.requested_model,
        request_log_id=final_result.request_log_id,
    )


def _summary_stable_hash(
    *,
    session_id: str,
    source_type: str,
    source_ids: list[int],
    payload: dict[str, Any],
) -> str:
    return hashlib.sha256(
        json.dumps({
            "kind": "llm_episode",
            "session_id": session_id,
            "source_type": source_type,
            "source_ids": source_ids,
            "summary": payload.get("summary") or "",
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _inheritance_audit_meta(audit: InheritanceAudit) -> dict[str, Any]:
    return {
        "obligation_count": audit.obligation_count,
        "carried_count": audit.carried_count,
        "updated_count": audit.updated_count,
        "resolved_count": audit.resolved_count,
        "normalized_count": audit.normalized_count,
        "state_sha256": audit.state_sha256,
    }


def _batch_trace_meta(trace: SummaryBatchTrace) -> dict[str, Any]:
    result: dict[str, Any] = {
        "batch_index": trace.batch_index,
        "fragment_hashes": list(trace.fragment_hashes),
        "model": trace.model,
        "requested_model": trace.requested_model,
        "request_log_id": trace.request_log_id,
        "inheritance": _inheritance_audit_meta(trace.inheritance_audit),
    }
    if trace.repair_inheritance_audit is not None:
        result["repair"] = {
            "attempt_count": 1,
            "kind": trace.repair_kind or "state_obligation_budget",
            "model": trace.repair_model,
            "requested_model": trace.repair_requested_model,
            "request_log_id": trace.repair_request_log_id,
            "inheritance": _inheritance_audit_meta(
                trace.repair_inheritance_audit
            ),
        }
    return result


def save_llm_session_summary(
    db: Session,
    *,
    job: SessionSummaryJob,
    payload: dict[str, Any],
    source_turns: list[ConversationTurn | ChatLog],
    model: str = "",
    requested_model: str = "",
    llm_request_log_id: int | None = None,
    batch_traces: tuple[SummaryBatchTrace, ...] = (),
) -> RollingSessionSummary:
    if not source_turns:
        raise ValueError("source_turns is required")
    source_type = normalize_summary_source_type(
        getattr(job, "source_type", SUMMARY_SOURCE_CONVERSATION_TURN)
    )
    fallback = (
        db.get(RollingSessionSummary, int(job.fallback_summary_id or 0))
        if job.fallback_summary_id
        else None
    )
    previous = (
        db.get(RollingSessionSummary, int(job.previous_summary_id or 0))
        if job.previous_summary_id
        else None
    )
    # 块式会话记忆(P2):LLM 升级继承 fallback 的 block_id,确保
    # get_best_session_summary(block_id=X) 能召回到 LLM 版本;归档/替换同块内进行。
    block_id = (
        int(fallback.block_id)
        if (
            source_type == SUMMARY_SOURCE_CONVERSATION_TURN
            and fallback is not None
            and getattr(fallback, "block_id", None) is not None
        )
        else None
    )
    superseded_query = db.query(RollingSessionSummary.id).filter(
        RollingSessionSummary.session_id == job.session_id,
        RollingSessionSummary.status == "active",
        RollingSessionSummary.source_type == source_type,
    )
    if block_id is not None:
        superseded_query = superseded_query.filter(
            RollingSessionSummary.block_id == block_id
        )
    superseded_document_ids = [
        int(row.id) for row in superseded_query.all() if int(row.id or 0) > 0
    ]
    archive_active_summaries_for_session(
        db,
        job.session_id,
        block_id=block_id,
        source_type=source_type,
    )

    pending_source_ids = [int(turn.id) for turn in source_turns]
    previous_source_ids = _json_list(summary_source_ids_json(previous))
    source_ids = list(dict.fromkeys([*previous_source_ids, *pending_source_ids]))
    covered_from_source_id = summary_covered_from(previous)
    if covered_from_source_id <= 0:
        covered_from_source_id = source_ids[0]
    legacy_source_ids = (
        source_ids if source_type == SUMMARY_SOURCE_CONVERSATION_TURN else []
    )
    legacy_covered_from = (
        covered_from_source_id
        if source_type == SUMMARY_SOURCE_CONVERSATION_TURN
        else 0
    )
    legacy_covered_until = (
        source_ids[-1]
        if source_type == SUMMARY_SOURCE_CONVERSATION_TURN
        else 0
    )
    try:
        job_meta = json.loads(job.meta_json or "{}")
        if not isinstance(job_meta, dict):
            job_meta = {}
    except (TypeError, json.JSONDecodeError):
        job_meta = {}
    recent_raw_source_ids = [
        int(item)
        for item in job_meta.get("recent_raw_turn_ids", [])
        if str(item).isdigit()
    ]
    raw_window_start_source_id = (
        recent_raw_source_ids[0] if recent_raw_source_ids else 0
    )
    if source_type == SUMMARY_SOURCE_CONVERSATION_TURN and fallback is not None:
        raw_window_start_source_id = int(
            getattr(fallback, "raw_window_start_turn_id", 0) or 0
        )
    quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
    issues = quality.get("issues") if isinstance(quality.get("issues"), list) else []
    summary_text = render_summary_text(payload)
    now = db_now_naive()
    row = RollingSessionSummary(
        session_id=job.session_id,
        user_id=job.user_id or "",
        chat_type=job.chat_type or "private",
        status="active",
        summary_kind="llm_episode",
        summary_text=summary_text,
        summary_json=json.dumps(payload, ensure_ascii=False),
        covered_from_turn_id=legacy_covered_from,
        covered_until_turn_id=legacy_covered_until,
        source_turn_ids_json=json.dumps(legacy_source_ids, ensure_ascii=False),
        source_type=source_type,
        covered_from_source_id=covered_from_source_id,
        covered_until_source_id=source_ids[-1],
        source_ids_json=json.dumps(source_ids, ensure_ascii=False),
        source_turn_count=len(source_ids),
        source_token_estimate=(
            int(getattr(previous, "source_token_estimate", 0) or 0)
            + sum(estimate_tokens(turn.content or "") for turn in source_turns)
        ),
        source_char_count=(
            int(getattr(previous, "source_char_count", 0) or 0)
            + sum(len(turn.content or "") for turn in source_turns)
        ),
        raw_window_start_turn_id=(
            raw_window_start_source_id
            if source_type == SUMMARY_SOURCE_CONVERSATION_TURN
            else 0
        ),
        raw_window_start_source_id=raw_window_start_source_id,
        quality_score=float(quality.get("score") or 0.0),
        issues_json=json.dumps(issues, ensure_ascii=False),
        model=model or "unknown",
        prompt_sha256=hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        llm_status="success",
        llm_model=model or "unknown",
        llm_request_log_id=llm_request_log_id,
        block_id=block_id,
        supersedes_summary_id=(
            int(fallback.id)
            if fallback and fallback.id
            else int(previous.id)
            if previous and previous.id
            else None
        ),
        stable_hash=_summary_stable_hash(
            session_id=job.session_id,
            source_type=source_type,
            source_ids=source_ids,
            payload=payload,
        ),
        meta_json=json.dumps({
            "schema_version": 3,
            "contract_version": 2,
            "created_by": "session_summary_worker",
            "summary_kind": "llm_episode",
            "source_type": source_type,
            "fallback_summary_id": int(getattr(fallback, "id", 0) or 0),
            "previous_summary_id": int(getattr(previous, "id", 0) or 0),
            "requested_model": requested_model or "unknown",
            "superseded_document_ids": superseded_document_ids,
            "batch_traces": [_batch_trace_meta(trace) for trace in batch_traces],
        }, ensure_ascii=False),
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def _safe_prepare_error_code(exc: BaseException) -> str:
    """只保留稳定内部错误码，避免把输入或异常正文写入 job。"""

    code = str(exc or "").split(":", 1)[0].strip()
    if re.fullmatch(r"[a-z][a-z0-9_]{2,127}", code):
        return code
    return "summary_prepare_invalid"


def _build_prepared_summary_contract(
    *,
    previous: RollingSessionSummary | None,
    source_turn_snapshots: tuple[SessionSummaryTurnSnapshot, ...],
) -> tuple[
    tuple[TurnFragment, ...],
    TurnCoverageManifest,
    dict[str, Any],
    tuple[SummaryObligation, ...],
    tuple[SummaryRequestBatch, ...],
    bool,
    dict[str, Any],
    bool,
]:
    """构建首批纯输入合同，并把确定性失败标为不可自动重试。"""

    try:
        fragments = _fragment_source_turns(source_turn_snapshots)
        manifest = build_coverage_manifest(fragments)
        previous_state = canonical_previous_state(
            previous,
            max_state_chars=config.SESSION_SUMMARY_LLM_MAX_STATE_CHARS,
        )
        previous_obligations = build_previous_summary_obligations(
            previous,
            max_state_chars=config.SESSION_SUMMARY_LLM_MAX_STATE_CHARS,
        )
        system_prompt = _render_session_summary_prompt(
            "tasks/session_summary_system"
        )
        output_instruction = _render_session_summary_prompt(
            "tasks/session_summary_output"
        )
        try:
            batch_contracts = build_summary_request_batches(
                system_prompt=system_prompt,
                previous_state=previous_state,
                available_obligations=previous_obligations,
                fragments=fragments,
                output_instruction=output_instruction,
                max_request_chars=config.SESSION_SUMMARY_LLM_MAX_REQUEST_CHARS,
                safety_chars=config.SESSION_SUMMARY_LLM_REQUEST_SAFETY_CHARS,
            )
        except ValueError as exc:
            # 超限 previous 仍需进入一次局部修复；这里的 batch_contracts
            # 只用于 coverage 校验，实际请求会在 repair 后重新构建。
            if (
                len(previous_obligations) <= SESSION_SUMMARY_MAX_STATE_OBLIGATIONS
                or str(exc) != "summary_request_budget_exceeded"
            ):
                raise
            batch_contracts = build_summary_request_batches(
                system_prompt=system_prompt,
                previous_state=previous_state,
                available_obligations=(),
                fragments=fragments,
                output_instruction=output_instruction,
                max_request_chars=config.SESSION_SUMMARY_LLM_MAX_REQUEST_CHARS,
                safety_chars=config.SESSION_SUMMARY_LLM_REQUEST_SAFETY_CHARS,
            )
        legacy_summary = False
        raw_summary = getattr(previous, "summary_json", None) if previous is not None else None
        if isinstance(raw_summary, str) and raw_summary.strip():
            try:
                parsed_summary = json.loads(raw_summary)
            except (TypeError, json.JSONDecodeError):
                parsed_summary = None
            legacy_summary = not (
                isinstance(parsed_summary, Mapping)
                and any(
                    field_name in parsed_summary
                    for field_name in (
                        "summary",
                        "open_threads",
                        "decisions",
                        "important_user_requests",
                        "resolved_items",
                        "artifacts",
                        "participants",
                        "keywords",
                    )
                )
            )
        previous_quality = (
            _previous_repair_quality(previous)
            if len(previous_obligations) > SESSION_SUMMARY_MAX_STATE_OBLIGATIONS
            else {"score": 0.0, "issues": []}
        )
        previous_quality_present = False
        if len(previous_obligations) > SESSION_SUMMARY_MAX_STATE_OBLIGATIONS:
            raw_quality_summary = (
                getattr(previous, "summary_json", None)
                if previous is not None else None
            )
            try:
                parsed_quality_summary = (
                    json.loads(raw_quality_summary)
                    if isinstance(raw_quality_summary, str)
                    else {}
                )
            except (TypeError, json.JSONDecodeError):
                parsed_quality_summary = {}
            previous_quality_present = (
                isinstance(parsed_quality_summary, Mapping)
                and "quality" in parsed_quality_summary
            )
    except NonRetryableSessionSummaryError:
        raise
    except (TypeError, ValueError) as exc:
        raise NonRetryableSessionSummaryError(
            _safe_prepare_error_code(exc)
        ) from exc
    return (
        fragments,
        manifest,
        previous_state,
        previous_obligations,
        batch_contracts,
        legacy_summary,
        previous_quality,
        previous_quality_present,
    )


def prepare_claimed_session_summary_job(
    db: Session,
    job_id: int | None = None,
    *,
    lease: SessionSummaryJobLease | None = None,
    owner: str = "",
) -> PreparedSessionSummaryJob | None:
    if lease is None:
        job = db.get(SessionSummaryJob, int(job_id or 0))
        if (
            job is None
            or job.status != "running"
            or str(job.lease_token or "")
            or (job.locked_by and job.locked_by != owner)
        ):
            return None
        raise SessionSummaryJobLeaseLost(
            "legacy_session_summary_job_has_no_fencing_lease"
        )
    if job_id is not None and int(job_id) != lease.job_id:
        raise ValueError("job_id 与 lease 不一致")
    if owner and owner != lease.worker_id:
        return None
    try:
        job = assert_summary_job_lease(db, lease)
    except SessionSummaryJobLeaseLost:
        return None
    source_turns = _load_source_turns(db, job)
    if not source_turns:
        raise NonRetryableSessionSummaryError("source_turns_empty")
    source_type = normalize_summary_source_type(
        getattr(job, "source_type", SUMMARY_SOURCE_CONVERSATION_TURN)
    )
    previous = (
        db.get(RollingSessionSummary, int(job.previous_summary_id or 0))
        if job.previous_summary_id
        else None
    )
    source_turn_snapshots = tuple(
        _snapshot_turn(turn, source_type=source_type)
        for turn in source_turns
    )
    (
        fragments,
        manifest,
        previous_state,
        previous_obligations,
        batch_contracts,
        previous_legacy_summary,
        previous_quality,
        previous_quality_present,
    ) = _build_prepared_summary_contract(
        previous=previous,
        source_turn_snapshots=source_turn_snapshots,
    )
    return PreparedSessionSummaryJob(
        job_id=int(job.id or 0),
        lease=lease,
        source_turns=source_turn_snapshots,
        fragments=fragments,
        manifest=manifest,
        batch_contracts=batch_contracts,
        previous_state=previous_state,
        previous_obligations=previous_obligations,
        previous_legacy_summary=previous_legacy_summary,
        previous_quality=previous_quality,
        previous_quality_present=previous_quality_present,
        source_type=source_type,
    )


def finalize_claimed_session_summary_job(
    db: Session,
    prepared: PreparedSessionSummaryJob,
    *,
    raw: Any,
    owner: str = "",
    model: str = "",
) -> bool:
    if owner and owner != prepared.lease.worker_id:
        return False
    try:
        job = assert_summary_job_lease(db, prepared.lease)
    except SessionSummaryJobLeaseLost:
        return False
    source_turns = _load_source_turns(db, job)
    if not source_turns:
        raise ValueError("summary_input_manifest_mismatch")
    source_type = normalize_summary_source_type(
        getattr(job, "source_type", SUMMARY_SOURCE_CONVERSATION_TURN)
    )
    if source_type != prepared.source_type:
        raise ValueError("summary_input_manifest_mismatch")
    current_snapshots = tuple(
        _snapshot_turn(turn, source_type=source_type)
        for turn in source_turns
    )
    current_fragments = _fragment_source_turns(
        current_snapshots,
        max_fragment_chars=prepared.max_fragment_chars,
    )
    current_manifest = build_coverage_manifest(current_fragments)
    _validate_prepared_coverage(prepared)
    _validate_completed_coverage(prepared)
    if current_fragments != prepared.fragments or current_manifest != prepared.manifest:
        raise ValueError("summary_input_manifest_mismatch")

    permit = acquire_summary_finalize_permit(
        db,
        lease=prepared.lease,
    )
    if permit.decision == "lost_lease":
        return False
    if permit.decision == "obsolete":
        mark_summary_job_obsolete(db, job, permit=permit)
        db.flush()
        return True

    llm_result = _normalize_llm_result(raw)
    payload = strip_summary_inheritance(
        parse_llm_summary_response(llm_result.content)
    )
    audit_ok, issues = audit_llm_session_summary(
        payload=payload,
        source_turns=list(current_snapshots),
        job=job,
    )
    if not audit_ok:
        raise ValueError(",".join(issues))

    summary = save_llm_session_summary(
        db,
        job=job,
        payload=payload,
        source_turns=source_turns,
        model=(
            llm_result.model
            if isinstance(raw, SessionSummaryLLMResult)
            else model or llm_result.model
        ),
        requested_model=llm_result.requested_model,
        llm_request_log_id=llm_result.request_log_id,
        batch_traces=tuple(prepared.batch_traces),
    )
    from core.semantic.jobs import enqueue_index_job
    from core.semantic.adapters import session_summary_source_revision

    try:
        summary_meta = json.loads(summary.meta_json or "{}")
        if not isinstance(summary_meta, dict):
            summary_meta = {}
    except (TypeError, json.JSONDecodeError):
        summary_meta = {}
    delete_source_ids = {
        str(item)
        for item in summary_meta.get("superseded_document_ids", [])
        if int(item or 0) > 0
    }
    delete_source_ids.add(str(summary.id))

    enqueue_index_job(
        db,
        source_type="session_summary",
        source_id=str(summary.session_id),
        job_type="replace",
        index_version="",
        source_revision=session_summary_source_revision(summary),
        meta={
            "contract_version": 2,
            "job_origin": "business",
            "document_id": int(summary.id or 0),
            "delete_source_ids": sorted(delete_source_ids),
        },
        commit=False,
    )
    mark_summary_job_done(db, job, result_summary_id=int(summary.id or 0))
    db.flush()
    return True


def fail_claimed_session_summary_job(
    db: Session,
    job_id: int | None = None,
    *,
    lease: SessionSummaryJobLease | None = None,
    owner: str = "",
    error: str,
    retryable: bool = True,
    now: datetime | None = None,
) -> bool:
    if lease is None:
        job = db.get(SessionSummaryJob, int(job_id or 0))
        if (
            job is None
            or job.status != "running"
            or str(job.lease_token or "")
            or (job.locked_by and job.locked_by != owner)
        ):
            return False
    else:
        if job_id is not None and int(job_id) != lease.job_id:
            raise ValueError("job_id 与 lease 不一致")
        if owner and owner != lease.worker_id:
            return False
        try:
            job = assert_summary_job_lease(db, lease, now=now)
        except SessionSummaryJobLeaseLost:
            return False
    if job is None:
        return False
    mark_summary_job_failed(
        db,
        job,
        error=error,
        retryable=retryable,
    )
    db.flush()
    return True


def _fail_claimed_job_with_factory(
    session_factory: Callable[[], Session],
    *,
    lease: SessionSummaryJobLease,
    error: str,
    retryable: bool = True,
) -> bool:
    db = session_factory()
    try:
        failed = fail_claimed_session_summary_job(
            db,
            lease=lease,
            error=error,
            retryable=retryable,
        )
        db.commit()
        return failed
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _renew_claimed_job_with_factory(
    session_factory: Callable[[], Session],
    *,
    lease: SessionSummaryJobLease,
) -> bool:
    db = session_factory()
    try:
        renewed = renew_summary_job_lease(
            db,
            lease=lease,
        )
        db.commit()
        return renewed
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _preflight_claimed_job_with_factory(
    session_factory: Callable[[], Session],
    *,
    lease: SessionSummaryJobLease,
) -> str:
    """在调用 LLM 前淘汰已被更高或同级 active coverage 阻塞的 job。"""

    db = session_factory()
    try:
        job = db.get(SessionSummaryJob, lease.job_id)
        permit = acquire_summary_finalize_permit(
            db,
            lease=lease,
        )
        if permit.decision == "obsolete" and job is not None:
            mark_summary_job_obsolete(db, job, permit=permit)
        db.commit()
        return permit.decision
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def process_claimed_session_summary_job_short_transactions(
    session_factory: Callable[[], Session],
    *,
    lease: SessionSummaryJobLease,
    summarizer: Callable[[list[dict[str, str]]], Any] | None = None,
) -> bool:
    """处理已抢占的 job，避免 LLM 调用期间持有写事务。"""
    summarizer = summarizer or default_llm_summary_summarizer
    job_id = lease.job_id
    db = session_factory()
    try:
        prepared = prepare_claimed_session_summary_job(db, lease=lease)
        db.commit()
    except Exception as exc:
        db.rollback()
        safe_error = _safe_session_summary_error(exc)
        _fail_claimed_job_with_factory(
            session_factory,
            lease=lease,
            error=safe_error,
            retryable=not isinstance(exc, NonRetryableSessionSummaryError),
        )
        return False
    finally:
        db.close()

    if prepared is None:
        return False

    try:
        preflight_decision = _preflight_claimed_job_with_factory(
            session_factory,
            lease=lease,
        )
    except Exception as exc:
        safe_error = _safe_session_summary_error(exc)
        logger.warning(
            "session summary job preflight failed: job_id=%s error=%s",
            job_id,
            safe_error,
        )
        _fail_claimed_job_with_factory(
            session_factory,
            lease=lease,
            error=safe_error,
            retryable=not isinstance(exc, NonRetryableSessionSummaryError),
        )
        return False
    if preflight_decision == "obsolete":
        return True
    if preflight_decision != "promote":
        return False

    try:
        raw = _summarize_prepared_sync(
            prepared,
            summarizer,
            renew_lease=lambda: _renew_claimed_job_with_factory(
                session_factory,
                lease=lease,
            ),
        )
    except Exception as exc:
        safe_error = _safe_session_summary_error(exc)
        logger.warning(
            "session summary job failed before finalize: job_id=%s error=%s",
            job_id,
            safe_error,
        )
        _fail_claimed_job_with_factory(
            session_factory,
            lease=lease,
            error=safe_error,
            retryable=not isinstance(exc, NonRetryableSessionSummaryError),
        )
        return False

    db = session_factory()
    try:
        ok = finalize_claimed_session_summary_job(
            db,
            prepared,
            raw=raw,
        )
        db.commit()
        return ok
    except Exception as exc:
        db.rollback()
        safe_error = _safe_session_summary_error(exc)
        logger.warning(
            "session summary job failed: job_id=%s error=%s",
            job_id,
            safe_error,
        )
        _fail_claimed_job_with_factory(
            session_factory,
            lease=lease,
            error=safe_error,
            retryable=not isinstance(exc, NonRetryableSessionSummaryError),
        )
        return False
    finally:
        db.close()


async def process_claimed_session_summary_job_short_transactions_async(
    session_factory: Callable[[], Session],
    *,
    lease: SessionSummaryJobLease,
    summarizer: Callable[[list[dict[str, str]]], Any] | None = None,
) -> bool:
    """异步处理已抢占的 job，LLM 调用不经过同步 bridge。"""
    summarizer = summarizer or default_llm_summary_summarizer_async
    job_id = lease.job_id
    db = session_factory()
    try:
        prepared = prepare_claimed_session_summary_job(db, lease=lease)
        db.commit()
    except Exception as exc:
        db.rollback()
        safe_error = _safe_session_summary_error(exc)
        _fail_claimed_job_with_factory(
            session_factory,
            lease=lease,
            error=safe_error,
            retryable=not isinstance(exc, NonRetryableSessionSummaryError),
        )
        return False
    finally:
        db.close()

    if prepared is None:
        return False

    try:
        preflight_decision = _preflight_claimed_job_with_factory(
            session_factory,
            lease=lease,
        )
    except Exception as exc:
        safe_error = _safe_session_summary_error(exc)
        logger.warning(
            "session summary job preflight failed: job_id=%s error=%s",
            job_id,
            safe_error,
        )
        _fail_claimed_job_with_factory(
            session_factory,
            lease=lease,
            error=safe_error,
            retryable=not isinstance(exc, NonRetryableSessionSummaryError),
        )
        return False
    if preflight_decision == "obsolete":
        return True
    if preflight_decision != "promote":
        return False

    try:
        raw = await _summarize_prepared_async(
            prepared,
            summarizer,
            renew_lease=lambda: _renew_claimed_job_with_factory(
                session_factory,
                lease=lease,
            ),
        )
    except Exception as exc:
        safe_error = _safe_session_summary_error(exc)
        logger.warning(
            "session summary job failed before finalize: job_id=%s error=%s",
            job_id,
            safe_error,
        )
        _fail_claimed_job_with_factory(
            session_factory,
            lease=lease,
            error=safe_error,
            retryable=not isinstance(exc, NonRetryableSessionSummaryError),
        )
        return False

    db = session_factory()
    try:
        ok = finalize_claimed_session_summary_job(
            db,
            prepared,
            raw=raw,
        )
        db.commit()
        return ok
    except Exception as exc:
        db.rollback()
        safe_error = _safe_session_summary_error(exc)
        logger.warning(
            "session summary job failed: job_id=%s error=%s",
            job_id,
            safe_error,
        )
        _fail_claimed_job_with_factory(
            session_factory,
            lease=lease,
            error=safe_error,
            retryable=not isinstance(exc, NonRetryableSessionSummaryError),
        )
        return False
    finally:
        db.close()


def process_session_summary_job(
    db: Session,
    job: SessionSummaryJob,
    *,
    summarizer: Callable[[list[dict[str, str]]], Any] | None = None,
    owner: str = "session-summary-worker",
) -> bool:
    """Legacy test helper.

    生产 worker 应使用 `workers.session_summary_worker.run_once()`，由它拆分
    claim、LLM 调用和 finalize 的事务边界。本函数保留给旧测试和临时诊断。
    """
    summarizer = summarizer or default_llm_summary_summarizer
    if job.status == "pending":
        claimed = claim_summary_job(db, int(job.id or 0), owner=owner)
        if claimed is None:
            return False
        job = claimed
    elif job.status != "running":
        return False
    elif job.locked_by and job.locked_by != owner:
        return False
    if not str(job.lease_token or ""):
        return False
    lease = session_summary_job_lease(job)
    try:
        prepared = prepare_claimed_session_summary_job(db, lease=lease)
        if prepared is None:
            return False
        raw = _summarize_prepared_sync(prepared, summarizer)
        ok = finalize_claimed_session_summary_job(
            db,
            prepared,
            raw=raw,
            model="custom_summarizer",
        )
        return ok
    except Exception as exc:
        safe_error = _safe_session_summary_error(exc)
        logger.warning(
            "session summary job failed: job_id=%s error=%s",
            getattr(job, "id", 0),
            safe_error,
        )
        fail_claimed_session_summary_job(
            db,
            lease=lease,
            error=safe_error,
            retryable=not isinstance(exc, NonRetryableSessionSummaryError),
        )
        db.flush()
        return False


def run_session_summary_worker_once(
    db: Session,
    *,
    summarizer: Callable[[list[dict[str, str]]], Any] | None = None,
    owner: str = "session-summary-worker",
    limit: int | None = None,
) -> dict[str, int]:
    """Legacy test helper.

    该函数使用调用方传入的 DB session，可能形成较长事务。生产 worker
    不应直接调用它，应走 `workers.session_summary_worker.run_once()`。
    """
    jobs = fetch_pending_summary_jobs(db, limit=limit)
    stats = {"processed": 0, "done": 0, "failed": 0}
    for job in jobs:
        claimed = claim_summary_job(db, int(job.id or 0), owner=owner)
        if claimed is None:
            continue
        stats["processed"] += 1
        ok = process_session_summary_job(
            db,
            claimed,
            summarizer=summarizer,
            owner=owner,
        )
        if ok:
            stats["done"] += 1
        else:
            stats["failed"] += 1
    return stats
