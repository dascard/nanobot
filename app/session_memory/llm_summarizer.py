"""异步 LLM rolling session summary 生成与审计。"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
import re
from collections.abc import Callable
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
from app.session_memory.rolling_summary import archive_active_summaries_for_session, audit_rolling_summary
from app.session_memory.summarizer import render_summary_text
from app.session_memory.windowing import estimate_tokens, is_context_eligible_turn
from core.db.models.chat import ConversationTurn
from core.db.models.session_memory import RollingSessionSummary, SessionSummaryJob
from core.time_utils import db_now_naive

logger = logging.getLogger("nanobot.session_summary.llm")

LEGACY_SYNC_WORKER_HELPERS = True

# Prompt 目标仍为 7 项；硬门禁容忍 1 项偏差，历史 12 项膨胀仍会失败。
SESSION_SUMMARY_MAX_STATE_OBLIGATIONS = 8
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
    "summary_state_output_budget_exceeded",
    "summary_state_output_token_budget_exceeded",
    "summary_too_long",
    "summary_turn_hash_invalid",
    "summary_turn_id_duplicate",
    "summary_turn_id_invalid",
    "sync_summarizer_returned_awaitable",
})


def _safe_session_summary_error(exc: BaseException) -> str:
    """将任意异常收敛为不含请求正文、地址或凭证的稳定错误码。"""

    raw = str(exc or "")
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


SessionSummaryTurn = ConversationTurn | SessionSummaryTurnSnapshot


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
    max_fragment_chars: int = SESSION_SUMMARY_FRAGMENT_MAX_CHARS
    batch_traces: list[SummaryBatchTrace] = field(default_factory=list, compare=False)

    @property
    def messages(self) -> list[dict[str, str]]:
        """兼容旧诊断调用，返回首批消息副本。"""

        if not self.batch_contracts:
            return []
        return [dict(message) for message in self.batch_contracts[0].messages]


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


def _load_source_turns(db: Session, job: SessionSummaryJob) -> list[ConversationTurn]:
    ids = _json_list(job.source_turn_ids_json)
    if not ids:
        return []
    rows = (
        db.query(ConversationTurn)
        .filter(ConversationTurn.id.in_(ids))
        .all()
    )
    by_id = {int(row.id): row for row in rows}
    return [by_id[turn_id] for turn_id in ids if turn_id in by_id]


def _snapshot_turn(turn: ConversationTurn) -> SessionSummaryTurnSnapshot:
    return SessionSummaryTurnSnapshot(
        id=int(turn.id),
        created_at=turn.created_at,
        role=str(turn.role or ""),
        content=str(turn.content or ""),
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
    _validate_previous_obligation_budget(obligations)
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

    expected_ids = _json_list(job.source_turn_ids_json)
    actual_ids = [int(turn.id) for turn in source_turns]
    if expected_ids != actual_ids:
        issues.append("source_turn_ids_mismatch")

    for turn in source_turns:
        ok, reason = is_context_eligible_turn(turn)
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
) -> None:
    turn_ids = [int(turn.id) for turn in source_turns]
    ephemeral_job = SimpleNamespace(
        source_turn_ids_json=json.dumps(turn_ids, ensure_ascii=False),
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
    """首批调用前拒绝 1200 tokens 下不可满足的历史审计合同。"""

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
    _audit_intermediate_summary_payload(
        business_payload,
        _source_turns_for_batch(prepared, batch),
    )
    state = canonical_summary_state(
        business_payload,
        max_state_chars=config.SESSION_SUMMARY_LLM_MAX_STATE_CHARS,
    )
    return (
        business_payload,
        state,
        _build_bounded_summary_obligations(state),
        inheritance_audit,
        llm_result,
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
        (
            final_payload,
            state,
            obligations,
            inheritance_audit,
            final_result,
        ) = _accept_summary_batch_payload(
            raw=raw,
            obligations=obligations,
            prepared=prepared,
            batch=batch,
        )
        prepared.batch_traces.append(SummaryBatchTrace(
            batch_index=batch.batch_index,
            fragment_hashes=batch.fragment_hashes,
            inheritance_audit=inheritance_audit,
            model=final_result.model,
            requested_model=final_result.requested_model,
            request_log_id=final_result.request_log_id,
        ))
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
        (
            final_payload,
            state,
            obligations,
            inheritance_audit,
            final_result,
        ) = _accept_summary_batch_payload(
            raw=raw,
            obligations=obligations,
            prepared=prepared,
            batch=batch,
        )
        prepared.batch_traces.append(SummaryBatchTrace(
            batch_index=batch.batch_index,
            fragment_hashes=batch.fragment_hashes,
            inheritance_audit=inheritance_audit,
            model=final_result.model,
            requested_model=final_result.requested_model,
            request_log_id=final_result.request_log_id,
        ))
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
    source_turn_ids: list[int],
    payload: dict[str, Any],
) -> str:
    return hashlib.sha256(
        json.dumps({
            "kind": "llm_episode",
            "session_id": session_id,
            "source_turn_ids": source_turn_ids,
            "summary": payload.get("summary") or "",
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def save_llm_session_summary(
    db: Session,
    *,
    job: SessionSummaryJob,
    payload: dict[str, Any],
    source_turns: list[ConversationTurn],
    model: str = "",
    requested_model: str = "",
    llm_request_log_id: int | None = None,
    batch_traces: tuple[SummaryBatchTrace, ...] = (),
) -> RollingSessionSummary:
    if not source_turns:
        raise ValueError("source_turns is required")
    fallback = db.get(RollingSessionSummary, int(job.fallback_summary_id or 0)) if job.fallback_summary_id else None
    previous = db.get(RollingSessionSummary, int(job.previous_summary_id or 0)) if job.previous_summary_id else None
    superseded_document_ids = [
        int(row.id)
        for row in db.query(RollingSessionSummary.id).filter(
            RollingSessionSummary.session_id == job.session_id,
            RollingSessionSummary.status == "active",
        ).all()
        if int(row.id or 0) > 0
    ]
    archive_active_summaries_for_session(db, job.session_id)

    pending_turn_ids = [int(turn.id) for turn in source_turns]
    previous_turn_ids = _json_list(getattr(previous, "source_turn_ids_json", "[]"))
    source_turn_ids = list(dict.fromkeys([*previous_turn_ids, *pending_turn_ids]))
    covered_from_turn_id = int(getattr(previous, "covered_from_turn_id", 0) or 0)
    if covered_from_turn_id <= 0:
        covered_from_turn_id = source_turn_ids[0]
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
        covered_from_turn_id=covered_from_turn_id,
        covered_until_turn_id=source_turn_ids[-1],
        source_turn_ids_json=json.dumps(source_turn_ids, ensure_ascii=False),
        source_turn_count=len(source_turn_ids),
        source_token_estimate=(
            int(getattr(previous, "source_token_estimate", 0) or 0)
            + sum(estimate_tokens(turn.content or "") for turn in source_turns)
        ),
        source_char_count=(
            int(getattr(previous, "source_char_count", 0) or 0)
            + sum(len(turn.content or "") for turn in source_turns)
        ),
        raw_window_start_turn_id=int(getattr(fallback, "raw_window_start_turn_id", 0) or 0),
        quality_score=float(quality.get("score") or 0.0),
        issues_json=json.dumps(issues, ensure_ascii=False),
        model=model or "unknown",
        prompt_sha256=hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        llm_status="success",
        llm_model=model or "unknown",
        llm_request_log_id=llm_request_log_id,
        supersedes_summary_id=int(fallback.id) if fallback and fallback.id else None,
        stable_hash=_summary_stable_hash(
            session_id=job.session_id,
            source_turn_ids=source_turn_ids,
            payload=payload,
        ),
        meta_json=json.dumps({
            "schema_version": 2,
            "contract_version": 2,
            "created_by": "session_summary_worker",
            "summary_kind": "llm_episode",
            "fallback_summary_id": int(getattr(fallback, "id", 0) or 0),
            "previous_summary_id": int(getattr(previous, "id", 0) or 0),
            "requested_model": requested_model or "unknown",
            "superseded_document_ids": superseded_document_ids,
            "batch_traces": [
                {
                    "batch_index": trace.batch_index,
                    "fragment_hashes": list(trace.fragment_hashes),
                    "model": trace.model,
                    "requested_model": trace.requested_model,
                    "request_log_id": trace.request_log_id,
                    "inheritance": {
                        "obligation_count": (
                            trace.inheritance_audit.obligation_count
                        ),
                        "carried_count": (
                            trace.inheritance_audit.carried_count
                        ),
                        "updated_count": (
                            trace.inheritance_audit.updated_count
                        ),
                        "resolved_count": (
                            trace.inheritance_audit.resolved_count
                        ),
                        "normalized_count": (
                            trace.inheritance_audit.normalized_count
                        ),
                        "state_sha256": (
                            trace.inheritance_audit.state_sha256
                        ),
                    },
                }
                for trace in batch_traces
            ],
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
        _validate_previous_obligation_budget(previous_obligations)
        batch_contracts = build_summary_request_batches(
            system_prompt=_render_session_summary_prompt("tasks/session_summary_system"),
            previous_state=previous_state,
            available_obligations=previous_obligations,
            fragments=fragments,
            output_instruction=_render_session_summary_prompt("tasks/session_summary_output"),
            max_request_chars=config.SESSION_SUMMARY_LLM_MAX_REQUEST_CHARS,
            safety_chars=config.SESSION_SUMMARY_LLM_REQUEST_SAFETY_CHARS,
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
    previous = db.get(RollingSessionSummary, int(job.previous_summary_id or 0)) if job.previous_summary_id else None
    source_turn_snapshots = tuple(_snapshot_turn(turn) for turn in source_turns)
    (
        fragments,
        manifest,
        previous_state,
        previous_obligations,
        batch_contracts,
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
    current_fragments = _fragment_source_turns(
        source_turns,
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
        source_turns=source_turns,
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
