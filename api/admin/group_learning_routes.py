"""群学习管理工作台的专用 Admin API。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from functools import wraps
import hashlib
import inspect
import json
import re
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.admin.common import audit_request, verify_admin
from api.admin.contract_models import GroupMemoryExtractResponse
from api.admin.group_learning_models import (
    GroupLearningCandidateDetailResponse,
    GroupLearningCandidateListResponse,
    GroupLearningDescriptorsResponse,
    GroupLearningDryRunRequest,
    GroupLearningDryRunResponse,
    GroupLearningExtractRequest,
    GroupLearningFeatureResponse,
    GroupLearningFeatureUpdateRequest,
    GroupLearningGovernanceResponse,
    GroupLearningOverviewResponse,
    GroupLearningReviewRequest,
    GroupLearningRuleActivationRequest,
    GroupLearningRuleActivationResponse,
    GroupLearningRunListResponse,
    GroupLearningScheduleMutationResponse,
    GroupLearningSchedulePauseRequest,
    GroupLearningSchedulePutRequest,
    GroupLearningSessionListResponse,
)
from api.endpoint_contracts import standard_error_responses
from app.group_learning.governance_service import (
    GroupLearningGovernanceService,
)
from app.group_learning.query_service import (
    GroupLearningQueryService,
    require_canonical_group_stream_id,
)
from app.group_learning.schedule_service import (
    GroupLearningScheduleService,
)
from app.session_config import discover_chat_streams
from api.admin.runtime_routes import _runtime_snapshot
from core.chat_stream_identity import (
    identity_storage_aliases,
    parse_canonical_chat_stream_id,
)
from core.admin.idempotency import (
    AdminIdempotencyCorrupt,
    AdminIdempotencyError,
    AdminIdempotencyInProgress,
    AdminIdempotencyService,
    admin_request_sha256,
)
from core.database import get_db
from core.db import (
    group_learning_governance_repository,
    group_learning_query_repository,
    system_setting_repository,
)
from core.db.group_learning_schedule_adapter import (
    SqlAlchemyGroupLearningScheduleRepository,
)
from core.db.models import (
    ChatLog,
    ChatStreamConfig,
    GroupLearningCandidate,
    GroupLearningRun,
    GroupMemory,
)
from core.group_learning import (
    GROUP_ANALYSIS_ASPECT_REGISTRY,
    GROUP_LEARNING_EVIDENCE_POLICY_REGISTRY,
    GROUP_LEARNING_MEMORY_TYPES,
    GROUP_LEARNING_SCHEDULE_POLICY,
    LEARNING_SIGNAL_RULE_REGISTRY,
    dry_run_learning_rules,
    list_group_analysis_aspects,
)
from core.group_learning.rule_activation import (
    GROUP_LEARNING_RULE_CONTROLS_SETTING,
    GroupLearningRuleControls,
)
from core.group_learning.states import (
    GROUP_LEARNING_CANDIDATE_SOURCES,
    GROUP_LEARNING_CANDIDATE_STATUSES,
    GROUP_LEARNING_CONFLICT_RESOLUTIONS,
    GROUP_LEARNING_HUMAN_ACTIONS,
    GROUP_LEARNING_MODEL_ACTIONS,
    GROUP_LEARNING_RUN_STATUSES,
)
from core.settings_admin_service import (
    SystemSettingCommandService,
)
from core.settings_service import settings
from core.time_utils import db_now_naive


router = APIRouter(tags=["admin-group-learning"])

_FEATURE_SETTING = "group_learning.enabled"
_PREVIEW_LIMIT = 240
_URL_SECRET_RE = re.compile(
    r"(?i)([?&](?:token|access_token|refresh_token|api_key|key|"
    r"secret|signature|auth)=)([^&#\s]+)"
)
_INLINE_SECRET_RE = re.compile(
    r"(?i)\b(authorization|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|password|passwd|secret)\b"
    r"(\s*[:=]\s*)([^\s,;]{4,})"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return str(value or "")


def _json_list(raw: object) -> list[str]:
    try:
        value = json.loads(str(raw or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _feature_enabled(db: Session) -> bool:
    return bool(settings.get_for_session(db, _FEATURE_SETTING, False))


def _rule_controls(db: Session) -> GroupLearningRuleControls:
    raw = settings.get_for_session(
        db,
        GROUP_LEARNING_RULE_CONTROLS_SETTING,
        "",
    )
    try:
        return GroupLearningRuleControls.from_json(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "group_learning_rule_controls_invalid",
                "message": str(exc),
            },
        ) from exc


def _schedule_service(db: Session) -> GroupLearningScheduleService:
    return GroupLearningScheduleService(
        repository=SqlAlchemyGroupLearningScheduleRepository(db),
        enabled=lambda: _feature_enabled(db),
    )


def _query_service(db: Session) -> GroupLearningQueryService:
    return GroupLearningQueryService(
        group_learning_query_repository(db)
    )


def _governance_service(db: Session) -> GroupLearningGovernanceService:
    return GroupLearningGovernanceService(
        repository=group_learning_governance_repository(db),
        enabled=lambda: _feature_enabled(db),
    )


def _audit_mutation(
    db: Session,
    request: Request,
    *,
    action: str,
    target_type: str,
    target_id: str,
    request_id: str,
    reason: str,
    result: dict[str, Any],
    detail: dict[str, Any] | None = None,
) -> None:
    audit_request(
        db,
        request,
        action,
        target_type,
        target_id,
        {
            "request_id": request_id,
            "reason": reason,
            "result": result,
            **(detail or {}),
        },
    )


MutationValues = Mapping[str, Any]
TargetIdResolver = Callable[[MutationValues], str]
MutationDetailBuilder = Callable[
    [MutationValues, Mapping[str, object]],
    dict[str, object],
]
StoredResultBuilder = Callable[
    [MutationValues, Mapping[str, object]],
    dict[str, object],
]
ReplayResultBuilder = Callable[
    [MutationValues, Mapping[str, object]],
    dict[str, object],
]


def _idempotency_http_error(
    exc: AdminIdempotencyError,
) -> HTTPException:
    unavailable = isinstance(exc, AdminIdempotencyCorrupt)
    return HTTPException(
        status_code=503 if unavailable else 409,
        detail={
            "code": exc.code,
            "message": str(exc),
            "retryable": isinstance(
                exc,
                AdminIdempotencyInProgress,
            ),
        },
    )


def _operation_error_code(exc: BaseException) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and detail.get("code"):
            return str(detail["code"])
        return f"http_{exc.status_code}"
    return type(exc).__name__.lower()


def _safe_mark_idempotency_failed(
    service: AdminIdempotencyService,
    *,
    request_id: str,
    exc: BaseException,
) -> None:
    try:
        service.fail(
            request_id=request_id,
            error_code=_operation_error_code(exc),
        )
    except Exception as settle_error:
        add_note = getattr(exc, "add_note", None)
        if callable(add_note):
            add_note(
                "Admin 幂等失败终态写入异常："
                f"{type(settle_error).__name__}"
            )


def _idempotent_group_learning_mutation(
    *,
    action: str,
    target_type: str,
    target_id: TargetIdResolver,
    detail_builder: MutationDetailBuilder | None = None,
    stored_result_builder: StoredResultBuilder | None = None,
    replay_result_builder: ReplayResultBuilder | None = None,
):
    """用数据库唯一账本包装一个类型化群学习 Admin 写操作。"""

    def decorate(function):
        async_call = inspect.iscoroutinefunction(function)

        def prepare(values: dict[str, Any]):
            body = values["body"]
            db = values["db"]
            resolved_target = str(target_id(values))
            payload = body.model_dump(
                mode="json",
                exclude={"request_id"},
            )
            request_sha256 = admin_request_sha256(payload)
            service = AdminIdempotencyService(db)
            try:
                begin = service.begin(
                    request_id=body.request_id,
                    action=action,
                    target_id=resolved_target,
                    request_sha256=request_sha256,
                )
            except AdminIdempotencyError as exc:
                raise _idempotency_http_error(exc) from exc
            if begin.replay_result is None:
                return body, db, resolved_target, service, None
            stored = begin.replay_result
            if replay_result_builder is not None:
                replay = replay_result_builder(values, stored)
            else:
                replay = dict(stored)
                if "replayed" in replay:
                    replay["replayed"] = True
            return body, db, resolved_target, service, replay

        def finalize(
            values: dict[str, Any],
            *,
            body: Any,
            db: Session,
            resolved_target: str,
            service: AdminIdempotencyService,
            result: Mapping[str, object],
        ) -> dict[str, object]:
            stored = (
                stored_result_builder(values, result)
                if stored_result_builder is not None
                else dict(result)
            )
            detail = (
                detail_builder(values, result)
                if detail_builder is not None
                else {}
            )
            _audit_mutation(
                db,
                values["request"],
                action=action,
                target_type=target_type,
                target_id=resolved_target,
                request_id=body.request_id,
                reason=body.reason,
                result=stored,
                detail=detail,
            )
            service.succeed(
                request_id=body.request_id,
                result=stored,
            )
            return dict(result)

        if async_call:
            @wraps(function)
            async def async_wrapper(*args, **kwargs):
                values = dict(kwargs)
                (
                    body,
                    db,
                    resolved_target,
                    service,
                    replay,
                ) = prepare(values)
                if replay is not None:
                    return replay
                try:
                    result = await function(*args, **kwargs)
                    return finalize(
                        values,
                        body=body,
                        db=db,
                        resolved_target=resolved_target,
                        service=service,
                        result=result,
                    )
                except BaseException as exc:
                    _safe_mark_idempotency_failed(
                        service,
                        request_id=body.request_id,
                        exc=exc,
                    )
                    raise

            return async_wrapper

        @wraps(function)
        def sync_wrapper(*args, **kwargs):
            values = dict(kwargs)
            (
                body,
                db,
                resolved_target,
                service,
                replay,
            ) = prepare(values)
            if replay is not None:
                return replay
            try:
                result = function(*args, **kwargs)
                return finalize(
                    values,
                    body=body,
                    db=db,
                    resolved_target=resolved_target,
                    service=service,
                    result=result,
                )
            except BaseException as exc:
                _safe_mark_idempotency_failed(
                    service,
                    request_id=body.request_id,
                    exc=exc,
                )
                raise

        return sync_wrapper

    return decorate


def _canonical_session_target(values: MutationValues) -> str:
    try:
        return require_canonical_group_stream_id(
            str(values["chat_stream_id"])
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _rule_activation_target(values: MutationValues) -> str:
    body = values["body"]
    return (
        f"{values['rule_id']}@{body.chat_stream_id}"
        if body.chat_stream_id
        else f"{values['rule_id']}@global"
    )


def _candidate_review_audit_detail(
    values: MutationValues,
    _result: Mapping[str, object],
) -> dict[str, object]:
    body = values["body"]
    content = body.reviewed_content
    meaning = body.reviewed_meaning
    return {
        "governance_action": body.action,
        "target_memory_id": body.target_memory_id,
        "conflict_resolution": body.conflict_resolution,
        "reviewed_chars": len(content) + len(meaning),
        "reviewed_sha256": hashlib.sha256(
            f"{content}\0{meaning}".encode("utf-8")
        ).hexdigest(),
    }


_EXTRACT_SAFE_RESULT_KEYS = (
    "ok",
    "group_id",
    "group_name",
    "window_hours",
    "raw_count",
    "eligible_count",
    "deduped_count",
    "message_count",
    "source_log_count",
    "stats",
    "memory_count",
    "active_count",
    "injectable_count",
)


def _extract_stored_result(
    _values: MutationValues,
    result: Mapping[str, object],
) -> dict[str, object]:
    return {
        key: result[key]
        for key in _EXTRACT_SAFE_RESULT_KEYS
        if key in result
    }


def _extract_replay_result(
    values: MutationValues,
    stored: Mapping[str, object],
) -> dict[str, object]:
    from api.admin.group_memory_routes import _group_memories_payload

    payload = dict(stored)
    payload.update(
        _group_memories_payload(
            values["db"],
            str(stored.get("group_id") or ""),
        )
    )
    return payload


def _extract_audit_detail(
    values: MutationValues,
    _result: Mapping[str, object],
) -> dict[str, object]:
    body = values["body"]
    return {
        "aspects": list(body.aspects or ()),
        "instructions_chars": len(body.instructions),
        "instructions_sha256": hashlib.sha256(
            body.instructions.encode("utf-8")
        ).hexdigest(),
    }


def _schedule_dict(value: object) -> dict[str, Any]:
    payload = asdict(value)
    aspects = payload.pop("aspects", None)
    if aspects is None:
        aspects = _json_list(payload.pop("aspects_json", "[]"))
    payload["aspects"] = list(aspects)
    for key, item in tuple(payload.items()):
        if isinstance(item, datetime):
            payload[key] = _iso(item)
    return payload


def _state_dict(value: object) -> dict[str, Any]:
    payload = asdict(value)
    for key, item in tuple(payload.items()):
        if isinstance(item, datetime):
            payload[key] = _iso(item)
    return payload


def _candidate_dict(value: object) -> dict[str, Any]:
    return {
        "id": int(value.id),
        "candidate_id": value.candidate_id,
        "chat_stream_id": value.chat_stream_id,
        "candidate_type": value.candidate_type,
        "content": value.content,
        "meaning": value.meaning,
        "source": value.source,
        "status": value.status,
        "rule_id": value.rule_id,
        "rule_version": int(value.rule_version),
        "hit_count": int(value.hit_count),
        "source_run_id": value.source_run_id,
        "model_decision": value.model_decision,
        "model_contract_version": value.model_contract_version,
        "model_review_run_id": value.model_review_run_id,
        "reviewed_content": value.reviewed_content,
        "reviewed_meaning": value.reviewed_meaning,
        "merge_target_memory_id": value.merge_target_memory_id,
        "alias_target_memory_id": value.alias_target_memory_id,
        "promoted_group_memory_id": value.promoted_group_memory_id,
        "conflict_group_id": value.conflict_group_id,
        "approval_source": value.approval_source,
        "human_reviewer_id": value.human_reviewer_id,
        "human_reviewed_at": _iso(value.human_reviewed_at),
        "human_action": value.human_action,
        "rejection_reason_code": value.rejection_reason_code,
        "waiting_reason_code": value.waiting_reason_code,
        "version": int(value.version),
        "first_seen_at": _iso(value.first_seen_at),
        "last_seen_at": _iso(value.last_seen_at),
        "updated_at": _iso(value.updated_at),
    }


def _run_dict(value: object) -> dict[str, Any]:
    return {
        "run_id": value.run_id,
        "chat_stream_id": value.chat_stream_id,
        "trigger": value.trigger,
        "mode": value.mode,
        "selected_aspects": _json_list(value.selected_aspects_json),
        "cursor_start_chat_log_id": value.cursor_start_chat_log_id,
        "cursor_end_chat_log_id": value.cursor_end_chat_log_id,
        "context_start_chat_log_id": value.context_start_chat_log_id,
        "context_end_chat_log_id": value.context_end_chat_log_id,
        "candidate_watermark": value.candidate_watermark,
        "rules_generation": value.rules_generation,
        "task_contract_version": value.task_contract_version,
        "model_route": value.model_route,
        "provider": value.provider,
        "model": value.model,
        "task_run_id": value.task_run_id,
        "status": value.status,
        "raw_message_count": value.raw_message_count,
        "cleaned_message_count": value.cleaned_message_count,
        "eligible_message_count": value.eligible_message_count,
        "candidate_count": value.candidate_count,
        "accepted_count": value.accepted_count,
        "rejected_count": value.rejected_count,
        "conflict_count": value.conflict_count,
        "waiting_count": value.waiting_count,
        "error_code": value.error_code,
        "input_chars": value.input_chars,
        "input_tokens": value.input_tokens,
        "output_tokens": value.output_tokens,
        "total_tokens": value.total_tokens,
        "cost_microusd": value.cost_microusd,
        "latency_ms": value.latency_ms,
        "attempt_count": value.attempt_count,
        "raw_output_bytes": value.raw_output_bytes,
        "raw_output_sha256": value.raw_output_sha256,
        "trace_id": value.trace_id,
        "job_id": value.job_id,
        "started_at": _iso(value.started_at),
        "completed_at": _iso(value.completed_at),
        "created_at": _iso(value.created_at),
        "updated_at": _iso(value.updated_at),
    }


def _redacted_preview(content: object) -> tuple[str, bool, bool]:
    text = str(content or "").replace("\x00", "")
    redacted = _URL_SECRET_RE.sub(
        lambda match: f"{match.group(1)}[redacted]",
        text,
    )
    redacted = _INLINE_SECRET_RE.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}[redacted]"
        ),
        redacted,
    )
    redacted = _BEARER_RE.sub("Bearer [redacted]", redacted)
    was_redacted = redacted != text
    truncated = len(redacted) > _PREVIEW_LIMIT
    if truncated:
        redacted = redacted[:_PREVIEW_LIMIT] + "…"
    return redacted, truncated, was_redacted


def _evidence_dict(
    db: Session,
    value: object,
    *,
    chat_stream_id: str,
) -> dict[str, Any]:
    row = db.get(ChatLog, int(value.chat_log_id))
    identity = parse_canonical_chat_stream_id(chat_stream_id)
    aliases = set(identity_storage_aliases(identity))
    available = bool(
        row is not None
        and str(row.session_id or "") in aliases
        and str(row.role or "") in {"ambient", "user"}
    )
    preview = ""
    truncated = False
    redacted = False
    if available:
        try:
            meta = json.loads(str(row.meta_json or "{}"))
        except json.JSONDecodeError:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        moderation = meta.get("moderation")
        moderation = moderation if isinstance(moderation, dict) else {}
        if bool(meta.get("no_context")) or bool(
            moderation.get("no_context")
        ):
            available = False
        else:
            preview, truncated, redacted = _redacted_preview(row.content)
    sender_hash = hashlib.sha256(
        str(value.sender_id or "").encode("utf-8")
    ).hexdigest()[:12]
    return {
        "id": int(value.id),
        "evidence_id": value.evidence_id,
        "candidate_id": value.candidate_id,
        "chat_log_id": int(value.chat_log_id),
        "sender_ref": f"sender:{sender_hash}" if sender_hash else "",
        "source_run_id": value.source_run_id,
        "batch_id": value.batch_id,
        "evidence_kind": value.evidence_kind,
        "created_at": _iso(value.created_at),
        "content_preview": preview,
        "preview_truncated": truncated,
        "preview_redacted": redacted,
        "available": available,
    }


def _count_maps(db: Session) -> tuple[
    dict[str, dict[str, int]],
    dict[str, dict[str, int]],
]:
    candidate_map: dict[str, dict[str, int]] = {}
    for chat_stream_id, status, count in (
        db.query(
            GroupLearningCandidate.chat_stream_id,
            GroupLearningCandidate.status,
            func.count(GroupLearningCandidate.id),
        )
        .group_by(
            GroupLearningCandidate.chat_stream_id,
            GroupLearningCandidate.status,
        )
        .all()
    ):
        candidate_map.setdefault(str(chat_stream_id), {})[
            str(status)
        ] = int(count or 0)
    memory_map: dict[str, dict[str, int]] = {}
    for chat_stream_id, status, count in (
        db.query(
            GroupMemory.chat_stream_id,
            GroupMemory.status,
            func.count(GroupMemory.id),
        )
        .filter(GroupMemory.chat_stream_id.is_not(None))
        .group_by(GroupMemory.chat_stream_id, GroupMemory.status)
        .all()
    ):
        memory_map.setdefault(str(chat_stream_id), {})[
            str(status)
        ] = int(count or 0)
    return candidate_map, memory_map


@router.get(
    "/group-learning/descriptors",
    operation_id="adminGroupLearningDescriptors",
    response_model=GroupLearningDescriptorsResponse,
    responses=standard_error_responses(401, 503),
)
def group_learning_descriptors(
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    controls = _rule_controls(db)
    globally_disabled = set(controls.global_disabled)
    policy = GROUP_LEARNING_SCHEDULE_POLICY
    return {
        "feature_enabled": _feature_enabled(db),
        "aspect_registry": {
            "generation": GROUP_ANALYSIS_ASPECT_REGISTRY.generation,
            "sha256": GROUP_ANALYSIS_ASPECT_REGISTRY.sha256,
        },
        "rule_registry": {
            "generation": LEARNING_SIGNAL_RULE_REGISTRY.generation,
            "sha256": LEARNING_SIGNAL_RULE_REGISTRY.sha256,
        },
        "evidence_policy_registry": {
            "generation": (
                GROUP_LEARNING_EVIDENCE_POLICY_REGISTRY.generation
            ),
            "sha256": GROUP_LEARNING_EVIDENCE_POLICY_REGISTRY.sha256,
        },
        "aspects": [asdict(item) for item in list_group_analysis_aspects()],
        "rules": [
            {
                **asdict(item),
                "positive_fixtures": list(item.positive_fixtures),
                "negative_fixtures": list(item.negative_fixtures),
                "globally_enabled": item.rule_id not in globally_disabled,
            }
            for item in LEARNING_SIGNAL_RULE_REGISTRY
        ],
        "evidence_policies": [
            {
                **asdict(item),
                "explicit_evidence_kinds": list(
                    item.explicit_evidence_kinds
                ),
            }
            for item in GROUP_LEARNING_EVIDENCE_POLICY_REGISTRY
        ],
        "schedule_policy": {
            key: value
            for key, value in asdict(policy).items()
            if key != "job_schedule_policy_id"
        },
        "candidate_types": list(GROUP_LEARNING_MEMORY_TYPES),
        "candidate_statuses": list(
            GROUP_LEARNING_CANDIDATE_STATUSES
        ),
        "candidate_sources": list(GROUP_LEARNING_CANDIDATE_SOURCES),
        "run_statuses": list(GROUP_LEARNING_RUN_STATUSES),
        "human_actions": list(GROUP_LEARNING_HUMAN_ACTIONS),
        "conflict_resolutions": list(
            GROUP_LEARNING_CONFLICT_RESOLUTIONS
        ),
        "model_actions": list(GROUP_LEARNING_MODEL_ACTIONS),
    }


@router.get(
    "/group-learning/sessions",
    operation_id="adminGroupLearningSessions",
    response_model=GroupLearningSessionListResponse,
    responses=standard_error_responses(401, 422),
)
def group_learning_sessions(
    limit: int = 300,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    bounded_limit = max(1, min(int(limit), 1000))
    discovered = tuple(
        item
        for item in discover_chat_streams(
            db,
            runtime_snapshot=_runtime_snapshot(),
        )
        if item.chat_type == "group"
        and item.identity_status == "canonical"
        and not item.identity_conflict
    )[:bounded_limit]
    query_repository = group_learning_query_repository(db)
    schedules = {
        row.chat_stream_id: row
        for row in query_repository.list_schedules(limit=2000)
    }
    candidate_map, memory_map = _count_maps(db)
    configs = {
        str(row.chat_stream_id): row
        for row in db.query(ChatStreamConfig).filter(
            ChatStreamConfig.chat_stream_id.in_(
                tuple(item.chat_stream_id for item in discovered)
                or ("__none__",)
            )
        )
    }
    latest_runs: dict[str, GroupLearningRun] = {}
    for row in (
        db.query(GroupLearningRun)
        .order_by(
            GroupLearningRun.chat_stream_id.asc(),
            GroupLearningRun.created_at.desc(),
            GroupLearningRun.run_id.desc(),
        )
        .all()
    ):
        latest_runs.setdefault(str(row.chat_stream_id), row)
    items = []
    for item in discovered:
        schedule = schedules.get(item.chat_stream_id)
        candidates = candidate_map.get(item.chat_stream_id, {})
        memories = memory_map.get(item.chat_stream_id, {})
        latest_run = latest_runs.get(item.chat_stream_id)
        config = configs.get(item.chat_stream_id)
        items.append({
            "chat_stream_id": item.chat_stream_id,
            "session_id": item.session_id,
            "session_name": item.session_name,
            "runtime_session_id": item.runtime_session_id,
            "identity_status": item.identity_status,
            "schedule_exists": schedule is not None,
            "schedule_enabled": bool(
                schedule is not None and schedule.enabled
            ),
            "selected_aspects": (
                _json_list(schedule.aspects_json)
                if schedule is not None
                else []
            ),
            "memory_count": sum(memories.values()),
            "candidate_count": sum(candidates.values()),
            "conflict_count": candidates.get("conflict", 0),
            "waiting_count": candidates.get(
                "waiting_for_evidence",
                0,
            ),
            "last_run_status": (
                str(latest_run.status or "") if latest_run else ""
            ),
            "last_run_at": (
                _iso(
                    latest_run.completed_at
                    or latest_run.started_at
                    or latest_run.created_at
                )
                if latest_run
                else ""
            ),
            "group_profile_mode": (
                str(config.group_profile_mode or "off")
                if config is not None
                else "off"
            ),
        })
    return {"total": len(items), "items": items}


@router.get(
    "/group-learning/sessions/{chat_stream_id:path}/overview",
    operation_id="adminGroupLearningOverview",
    response_model=GroupLearningOverviewResponse,
    responses=standard_error_responses(400, 401, 503),
)
def group_learning_overview(
    chat_stream_id: str,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        overview = _query_service(db).overview(chat_stream_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    controls = _rule_controls(db)
    candidates = {
        str(status): int(count or 0)
        for status, count in (
            db.query(
                GroupLearningCandidate.status,
                func.count(GroupLearningCandidate.id),
            )
            .filter(
                GroupLearningCandidate.chat_stream_id
                == overview.chat_stream_id
            )
            .group_by(GroupLearningCandidate.status)
            .all()
        )
    }
    memory_counts = {
        str(status): int(count or 0)
        for status, count in (
            db.query(GroupMemory.status, func.count(GroupMemory.id))
            .filter(
                GroupMemory.chat_stream_id == overview.chat_stream_id
            )
            .group_by(GroupMemory.status)
            .all()
        )
    }
    config = db.get(ChatStreamConfig, overview.chat_stream_id)
    recent_runs = _query_service(db).list_runs(
        overview.chat_stream_id,
        limit=1,
    )
    return {
        "chat_stream_id": overview.chat_stream_id,
        "feature_enabled": _feature_enabled(db),
        "schedule": (
            _schedule_dict(overview.schedule)
            if overview.schedule is not None
            else None
        ),
        "stream_state": (
            _state_dict(overview.stream_state)
            if overview.stream_state is not None
            else None
        ),
        "counts": {
            "memories": sum(memory_counts.values()),
            "active_memories": memory_counts.get("active", 0),
            "candidates": sum(candidates.values()),
            "conflicts": candidates.get("conflict", 0),
            "waiting": candidates.get("waiting_for_evidence", 0),
            "rejected": candidates.get("rejected", 0),
        },
        "selected_aspects": (
            _json_list(overview.schedule.aspects_json)
            if overview.schedule is not None
            else []
        ),
        "disabled_rule_ids": list(
            controls.disabled_rule_ids(overview.chat_stream_id)
        ),
        "enabled_rule_ids": list(
            controls.enabled_rule_ids(overview.chat_stream_id)
        ),
        "group_profile_mode": (
            str(config.group_profile_mode or "off")
            if config is not None
            else "off"
        ),
        "recent_run": (
            _run_dict(recent_runs[0]) if recent_runs else None
        ),
        "registry": {
            "aspects": {
                "generation": GROUP_ANALYSIS_ASPECT_REGISTRY.generation,
                "sha256": GROUP_ANALYSIS_ASPECT_REGISTRY.sha256,
            },
            "rules": {
                "generation": LEARNING_SIGNAL_RULE_REGISTRY.generation,
                "sha256": LEARNING_SIGNAL_RULE_REGISTRY.sha256,
            },
            "evidence": {
                "generation": (
                    GROUP_LEARNING_EVIDENCE_POLICY_REGISTRY.generation
                ),
                "sha256": (
                    GROUP_LEARNING_EVIDENCE_POLICY_REGISTRY.sha256
                ),
            },
        },
    }


@router.get(
    "/group-learning/sessions/{chat_stream_id:path}/candidates",
    operation_id="adminGroupLearningCandidates",
    response_model=GroupLearningCandidateListResponse,
    responses=standard_error_responses(400, 401, 422),
)
def group_learning_candidates(
    chat_stream_id: str,
    candidate_type: str = "",
    status: str = "",
    after_id: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        page = _query_service(db).list_candidates(
            chat_stream_id,
            candidate_type=candidate_type,
            status=status,
            after_id=after_id,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "chat_stream_id": page.chat_stream_id,
        "items": [_candidate_dict(item) for item in page.items],
        "next_after_id": page.next_after_id,
    }


@router.get(
    "/group-learning/candidates/{candidate_id}",
    operation_id="adminGroupLearningCandidateDetail",
    response_model=GroupLearningCandidateDetailResponse,
    responses=standard_error_responses(401, 404, 422),
)
def group_learning_candidate_detail(
    candidate_id: str,
    evidence_after_id: int = 0,
    evidence_limit: int = 100,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        detail = _query_service(db).candidate_detail(
            candidate_id,
            evidence_after_id=evidence_after_id,
            evidence_limit=evidence_limit,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "candidate": _candidate_dict(detail.candidate),
        "evidence": [
            _evidence_dict(
                db,
                item,
                chat_stream_id=detail.candidate.chat_stream_id,
            )
            for item in detail.evidence
        ],
        "next_evidence_after_id": detail.next_evidence_after_id,
    }


@router.get(
    "/group-learning/sessions/{chat_stream_id:path}/runs",
    operation_id="adminGroupLearningRuns",
    response_model=GroupLearningRunListResponse,
    responses=standard_error_responses(400, 401, 422),
)
def group_learning_runs(
    chat_stream_id: str,
    limit: int = 100,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        canonical_id = require_canonical_group_stream_id(chat_stream_id)
        rows = _query_service(db).list_runs(
            canonical_id,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "chat_stream_id": canonical_id,
        "items": [_run_dict(row) for row in rows],
    }


@router.put(
    "/group-learning/sessions/{chat_stream_id:path}/schedule",
    operation_id="adminGroupLearningSchedulePut",
    response_model=GroupLearningScheduleMutationResponse,
    responses=standard_error_responses(400, 401, 409, 422, 503),
)
@_idempotent_group_learning_mutation(
    action="group_learning.schedule.put",
    target_type="group_learning_schedule",
    target_id=_canonical_session_target,
)
def group_learning_schedule_put(
    chat_stream_id: str,
    body: GroupLearningSchedulePutRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        schedule = _schedule_service(db).put_schedule(
            chat_stream_id=chat_stream_id,
            aspects=(
                tuple(body.aspects)
                if body.aspects is not None
                else None
            ),
            interval_minutes=body.interval_minutes,
            window_hours=body.window_hours,
            enabled=body.enabled,
            now=db_now_naive(),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    result = {
        "ok": True,
        "replayed": False,
        "schedule": _schedule_dict(schedule),
    }
    return result


@router.post(
    "/group-learning/sessions/{chat_stream_id:path}/schedule/pause",
    operation_id="adminGroupLearningSchedulePause",
    response_model=GroupLearningScheduleMutationResponse,
    responses=standard_error_responses(
        400,
        401,
        404,
        409,
        422,
        503,
    ),
)
@_idempotent_group_learning_mutation(
    action="group_learning.schedule.pause",
    target_type="group_learning_schedule",
    target_id=_canonical_session_target,
)
def group_learning_schedule_pause(
    chat_stream_id: str,
    body: GroupLearningSchedulePauseRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        schedule = _schedule_service(db).disable_schedule(
            chat_stream_id=chat_stream_id,
            now=db_now_naive(),
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    result = {
        "ok": True,
        "replayed": False,
        "schedule": _schedule_dict(schedule),
    }
    return result


@router.post(
    "/group-learning/candidates/{candidate_id}/review",
    operation_id="adminGroupLearningCandidateReview",
    response_model=GroupLearningGovernanceResponse,
    responses=standard_error_responses(
        400,
        401,
        404,
        409,
        422,
        503,
    ),
)
@_idempotent_group_learning_mutation(
    action="group_learning.candidate.review",
    target_type="group_learning_candidate",
    target_id=lambda values: str(values["candidate_id"]),
    detail_builder=_candidate_review_audit_detail,
)
def group_learning_candidate_review(
    candidate_id: str,
    body: GroupLearningReviewRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin_user: str = Depends(verify_admin),
):
    if not _feature_enabled(db):
        raise HTTPException(
            409,
            {
                "code": "group_learning_disabled",
                "message": "群学习 kill switch 当前关闭",
            },
        )
    try:
        result_value = _governance_service(db).review_human_candidate(
            candidate_id=candidate_id,
            reviewer_id=admin_user,
            action=body.action,
            reviewed_content=body.reviewed_content,
            reviewed_meaning=body.reviewed_meaning,
            target_memory_id=body.target_memory_id,
            conflict_resolution=body.conflict_resolution,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "群学习人工治理发生唯一约束冲突") from exc
    result = {
        "ok": True,
        "replayed": False,
        "result": asdict(result_value),
    }
    return result


@router.post(
    "/group-learning/rules/dry-run",
    operation_id="adminGroupLearningRulesDryRun",
    response_model=GroupLearningDryRunResponse,
    responses=standard_error_responses(400, 401, 422, 503),
)
def group_learning_rules_dry_run(
    body: GroupLearningDryRunRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        if body.chat_stream_id:
            effective = _rule_controls(db).enabled_rule_ids(
                body.chat_stream_id
            )
        else:
            effective = tuple(
                rule_id
                for rule_id in LEARNING_SIGNAL_RULE_REGISTRY.ordered_ids
                if rule_id
                not in set(_rule_controls(db).global_disabled)
            )
        selected = (
            tuple(body.rule_ids)
            if body.rule_ids is not None
            else effective
        )
        if not set(selected) <= set(effective):
            raise ValueError("dry-run 不能绕过已停用规则")
        result = dry_run_learning_rules(body.text, rule_ids=selected)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "input_chars": result.input_chars,
        "elapsed_ms": result.elapsed_ms,
        "registry_generation": result.registry_generation,
        "registry_sha256": result.registry_sha256,
        "effective_rule_ids": list(selected),
        "matches": [asdict(item) for item in result.matches],
    }


@router.put(
    "/group-learning/features",
    operation_id="adminGroupLearningFeatureUpdate",
    response_model=GroupLearningFeatureResponse,
    responses=standard_error_responses(401, 409, 422, 503),
)
@_idempotent_group_learning_mutation(
    action="group_learning.feature.update",
    target_type="system_setting",
    target_id=lambda _values: _FEATURE_SETTING,
)
def group_learning_feature_update(
    body: GroupLearningFeatureUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    SystemSettingCommandService(
        system_setting_repository(db)
    ).upsert(
        key=_FEATURE_SETTING,
        value="1" if body.enabled else "0",
        description="群学习候选扫描、模型审核和治理写入总开关",
    )
    settings.invalidate()
    result = {
        "ok": True,
        "replayed": False,
        "enabled": body.enabled,
    }
    return result


@router.put(
    "/group-learning/rules/{rule_id}/activation",
    operation_id="adminGroupLearningRuleActivation",
    response_model=GroupLearningRuleActivationResponse,
    responses=standard_error_responses(400, 401, 409, 422, 503),
)
@_idempotent_group_learning_mutation(
    action="group_learning.rule.activation",
    target_type="group_learning_rule",
    target_id=_rule_activation_target,
)
def group_learning_rule_activation(
    rule_id: str,
    body: GroupLearningRuleActivationRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        controls = _rule_controls(db).with_rule_enabled(
            rule_id=rule_id,
            enabled=body.enabled,
            chat_stream_id=body.chat_stream_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    SystemSettingCommandService(
        system_setting_repository(db)
    ).upsert(
        key=GROUP_LEARNING_RULE_CONTROLS_SETTING,
        value=controls.to_json(),
        description="群学习规则的全局与会话级启停配置",
    )
    settings.invalidate()
    result = {
        "ok": True,
        "replayed": False,
        "rule_id": rule_id,
        "chat_stream_id": body.chat_stream_id,
        "enabled": body.enabled,
        "global_disabled": list(controls.global_disabled),
        "session_disabled": {
            key: list(value)
            for key, value in controls.session_disabled.items()
        },
    }
    return result


@router.post(
    "/group-learning/sessions/{chat_stream_id:path}/extract",
    operation_id="adminGroupLearningExtract",
    response_model=GroupMemoryExtractResponse,
    responses=standard_error_responses(
        400,
        401,
        404,
        409,
        422,
        502,
        503,
    ),
)
@_idempotent_group_learning_mutation(
    action="group_learning.extract",
    target_type="group_learning_session",
    target_id=_canonical_session_target,
    detail_builder=_extract_audit_detail,
    stored_result_builder=_extract_stored_result,
    replay_result_builder=_extract_replay_result,
)
async def group_learning_extract(
    chat_stream_id: str,
    body: GroupLearningExtractRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from api.admin.group_memory_routes import _group_memories_payload
    from app.group_memory import extraction_service

    try:
        canonical_id = require_canonical_group_stream_id(chat_stream_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    identity = parse_canonical_chat_stream_id(canonical_id)
    try:
        result_value = await extraction_service.extract_group_memories(
            db,
            identity.legacy_runtime_session_id,
            window_hours=body.window_hours,
            instructions=body.instructions,
            aspects=body.aspects,
        )
    except extraction_service.GroupMemoryGroupNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except extraction_service.GroupMemoryInsufficientData as exc:
        raise HTTPException(400, str(exc)) from exc
    except extraction_service.GroupMemoryLearningDisabled as exc:
        raise HTTPException(409, str(exc)) from exc
    except extraction_service.GroupMemoryLearningFailed as exc:
        raise HTTPException(502, str(exc)) from exc
    result = result_value.to_dict()
    result.update(
        _group_memories_payload(db, result_value.group_id)
    )
    return result


__all__ = ["router"]
