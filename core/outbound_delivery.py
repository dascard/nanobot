"""通用主动出站的同步事务状态机。"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import exists, func, or_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core.database import (
    ConversationTurn,
    OutboundDeliveryAttempt,
    OutboundDeliveryCircuit,
    OutboundDeliveryControl,
    OutboundDeliveryOutbox,
    OutboundGenerationAttempt,
    OutboundRun,
    ProactiveOutreachLog,
    ScheduledTask,
    User,
)
from core.message_envelope import envelope_to_message, is_html_reply


OUTBOUND_PROTOCOL_VERSION = 2
PROACTIVE_PREPARED_TASK_KIND = "proactive_outreach_prepared"
PROACTIVE_GENERATED_TASK_KIND = "proactive_outreach_generated"
PROACTIVE_GENERATION_METADATA_KEY = "_outbound_generation"
PROACTIVE_GENERATION_KINDS = frozenset({"message", "research", "forced"})
_RUN_CLAIM_STATUSES = frozenset({"claimed", "generating"})
_DUE_OUTBOX_STATUSES = frozenset({"pending", "retry_wait", "blocked"})
_CIRCUIT_SCOPE_TYPES = frozenset({"endpoint", "destination", "payload_contract"})
_TRANSIENT_CLIENT_HTTP_STATUSES = frozenset({408, 425, 429})
_STABLE_SERVER_HTTP_STATUSES = frozenset({501, 505})
_RESULT_CATEGORY_OUTCOMES = {
    "success": "succeeded",
    "transient": "transient_failure",
    "ambiguous": "ambiguous",
    "endpoint": "permanent_failure",
    "destination": "permanent_failure",
    "destination_missing": "permanent_failure",
    "destination_rejected": "permanent_failure",
    "destination_deleted": "permanent_failure",
    "payload": "permanent_failure",
    "payload_contract": "permanent_failure",
}
_SEMANTIC_2XX_FAILURE_CATEGORIES = frozenset(
    {
        "destination",
        "destination_missing",
        "destination_rejected",
        "destination_deleted",
        "payload",
        "payload_contract",
    }
)
_CONTROL_TRANSITIONS = {
    ("legacy_direct", "outbox_hold"),
    ("outbox_hold", "outbox_active"),
    ("outbox_active", "outbox_draining"),
    ("outbox_draining", "legacy_direct"),
}
_OUTBOUND_CONTEXT_TIMEZONE = ZoneInfo("Asia/Shanghai")
_OUTBOUND_CONTEXT_TEXT_LIMIT = 800


class OutboundDeliveryError(RuntimeError):
    """主动出站状态机错误基类。"""


class OutboundConflictError(OutboundDeliveryError):
    """幂等身份相同但不可变事实不同。"""


class OutboundFencingError(OutboundDeliveryError):
    """owner、token、租约或活动叶已经失效。"""


class OutboundSafetyError(OutboundDeliveryError):
    """当前 circuit、control 或风险状态禁止继续。"""


class InvalidOutboundTransitionError(OutboundDeliveryError):
    """请求了未定义的状态转换。"""


@dataclass(frozen=True, slots=True)
class WriterLeaseDecision:
    acquired: bool
    source_type: str
    owner: str
    token: str
    protocol_version: int
    writer_version: int
    lease_expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class WriterReleaseResult:
    applied: bool
    source_type: str
    writer_version: int


@dataclass(frozen=True, slots=True)
class RunClaimDecision:
    acquired: bool
    run_id: int
    status: str
    owner: str
    claim_token: str
    claim_expires_at: datetime | None
    delivery_mode: str
    cutover_epoch: int
    source_snapshot_json: str
    source_snapshot_sha256: str
    delivery_contract_json: str
    delivery_contract_sha256: str


@dataclass(frozen=True, slots=True)
class RunClaimRenewal:
    applied: bool
    run_id: int
    claim_expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class GenerationAttemptHandle:
    run_id: int
    attempt_id: int | None
    attempt_no: int | None
    owner: str
    fencing_token: str
    status: str
    reason_type: str


@dataclass(frozen=True, slots=True)
class OutboxCommitResult:
    outbox_id: int | None
    run_id: int
    created: bool
    payload_sha256: str
    status: str
    reason_type: str


@dataclass(frozen=True, slots=True)
class DeliveryClaimHandle:
    outbox_id: int
    run_id: int
    attempt_id: int
    attempt_no: int
    worker_owner: str
    lease_token: str
    lease_expires_at: datetime
    endpoint_key: str
    target_type: str
    endpoint_config_revision: str
    destination_snapshot_json: str
    payload_json: str
    payload_sha256: str
    payload_contract_fingerprint: str


@dataclass(frozen=True, slots=True)
class RequestStartResult:
    applied: bool
    outbox_id: int
    attempt_id: int
    request_started_count: int


@dataclass(frozen=True, slots=True)
class DeliverySettlementResult:
    applied: bool
    outbox_id: int
    attempt_id: int
    outbox_status: str
    run_status: str


@dataclass(frozen=True, slots=True)
class LeaseExpirySummary:
    abandoned_before_send: int
    ambiguous: int

    @property
    def total(self) -> int:
        return self.abandoned_before_send + self.ambiguous


@dataclass(frozen=True, slots=True)
class SourceCancellationSummary:
    cancelled: int
    unsafe: int


@dataclass(frozen=True, slots=True)
class ReplayResult:
    outbox_id: int
    run_id: int
    replay_sequence: int
    created: bool


@dataclass(frozen=True, slots=True)
class CircuitResetResult:
    applied: bool
    circuit_id: int | None
    status: str


@dataclass(frozen=True, slots=True)
class ControlTransitionResult:
    applied: bool
    source_type: str
    mode: str
    cutover_epoch: int
    writer_version: int
    effective_from: datetime


@dataclass(frozen=True, slots=True)
class OutboxCancellationResult:
    applied: bool
    outbox_id: int
    run_id: int
    status: str


@dataclass(frozen=True, slots=True)
class LegacyOutreachResolutionResult:
    applied: bool
    outreach_log_id: int
    status: str


@dataclass(frozen=True, slots=True)
class OutboundGenerationGate:
    allowed: bool
    delivery_mode: str
    cutover_epoch: int
    reason_type: str
    reason_summary: str


def _utc_naive(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if not isinstance(value, datetime):
        raise TypeError("时间参数必须是 datetime 或 null")
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _local_naive_to_utc_naive(value: datetime) -> datetime:
    """把 legacy 本地 naive 时间显式换算为出站账本使用的 UTC naive。"""

    if not isinstance(value, datetime):
        raise TypeError("本地时间必须是 datetime")
    if value.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo or timezone.utc
        value = value.replace(tzinfo=local_tz)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _positive_seconds(value: int | float, *, name: str) -> float:
    if type(value) not in (int, float):
        raise TypeError(f"{name} 必须是有限正数")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{name} 必须是有限正数")
    return normalized


def _text(value: Any, *, name: str, max_length: int) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} 必须是字符串")
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise ValueError(f"{name} 必须为 1-{max_length} 字符")
    return normalized


def _summary(value: Any) -> str:
    return str(value or "")[:1000]


def _outbound_context_local_time(value: datetime) -> datetime:
    utc_value = _utc_naive(value).replace(tzinfo=timezone.utc)
    return utc_value.astimezone(_OUTBOUND_CONTEXT_TIMEZONE).replace(tzinfo=None)


def _compact_outbound_context_text(value: str) -> str:
    compact = " ".join(str(value or "").split())
    if len(compact) <= _OUTBOUND_CONTEXT_TEXT_LIMIT:
        return compact
    return compact[:_OUTBOUND_CONTEXT_TEXT_LIMIT] + "…"


def _json_object_or_empty(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError):
        return {}
    return parsed if type(parsed) is dict else {}


def _append_delivered_outbound_context(
    db: Session,
    *,
    run: OutboundRun,
    outbox: OutboundDeliveryOutbox,
    delivered_at: datetime,
) -> None:
    """为已确认投递的私聊外呼写入幂等的精简上下文事件。"""

    source_type = str(run.source_type or "")
    if source_type not in {"proactive_outreach", "scheduled_task"}:
        return
    if str(outbox.target_type or "") != "private":
        return
    destination = _json_object_or_empty(outbox.destination_snapshot_json)
    user_id = str(destination.get("target_id") or "").strip()
    if not user_id:
        return
    event_id = f"outbound-delivery:{source_type}:{int(outbox.id)}"
    source_ids_json = json.dumps([event_id], ensure_ascii=False)
    already_exists = db.query(
        exists().where(
            ConversationTurn.user_id == user_id,
            ConversationTurn.session_id == f"private_{user_id}",
            ConversationTurn.source_message_ids_json == source_ids_json,
        )
    ).scalar()
    if already_exists:
        return

    payload = _json_object_or_empty(outbox.payload_json)
    message = envelope_to_message(payload)
    if source_type == "proactive_outreach":
        if is_html_reply(message):
            content = f"[主动外呼已发送] HTML报告，{len(message)}字符"
        else:
            body = _compact_outbound_context_text(message)
            content = f"[主动外呼已发送] {body or '已投递'}"
    else:
        snapshot = _json_object_or_empty(run.source_snapshot_json)
        task_name = _compact_outbound_context_text(
            str(snapshot.get("name") or "定时任务")
        )
        if is_html_reply(message):
            content = (
                f"[定时任务已发送] {task_name}"
                f"（HTML报告，{len(message)}字符）"
            )
        else:
            body = _compact_outbound_context_text(message)
            content = f"[定时任务已发送] {task_name}：{body or '已投递'}"

    db.add(ConversationTurn(
        user_id=user_id,
        session_id=f"private_{user_id}",
        role="assistant",
        content=content,
        created_at=_outbound_context_local_time(delivered_at),
        source_message_ids_json=source_ids_json,
        meta_json=json.dumps(
            {
                "kind": "outbound_delivery_summary",
                "source_type": source_type,
                "run_id": int(run.id),
                "outbox_id": int(outbox.id),
                "delivered_at_utc": _utc_naive(delivered_at).isoformat(),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    ))


def _scheduled_task_id(run: OutboundRun) -> int | None:
    if str(run.source_type) != "scheduled_task":
        return None
    try:
        task_id = int(str(run.source_id))
    except (TypeError, ValueError):
        return None
    return task_id if task_id > 0 else None


def _project_scheduled_task(
    db: Session,
    *,
    run: OutboundRun,
    status: str,
    current: datetime,
    error_summary: Any = "",
    bind_run: bool = False,
    attempted: bool = False,
    succeeded: bool = False,
) -> None:
    """在出站账本事务内维护定时任务兼容投影。"""

    task_id = _scheduled_task_id(run)
    if task_id is None:
        return
    normalized_status = str(status or "")[:48]
    normalized_summary = _summary(error_summary)
    if succeeded:
        task = db.get(ScheduledTask, task_id)
        if task is not None and (
            task.last_success_at is None or task.last_success_at < current
        ):
            task.last_success_at = current
            db.flush()

    values: dict[Any, Any] = {
        ScheduledTask.delivery_status: normalized_status,
        ScheduledTask.last_error_summary: normalized_summary,
    }
    if bind_run:
        values[ScheduledTask.last_run_id] = int(run.id)
    if attempted:
        values[ScheduledTask.last_attempt_at] = current
        values[ScheduledTask.last_run_at] = current
    query = db.query(ScheduledTask).filter(ScheduledTask.id == task_id)
    if not bind_run:
        query = query.filter(ScheduledTask.last_run_id == int(run.id))
    query.update(values, synchronize_session=False)


def _canonical_json(value: Mapping[str, Any], *, name: str) -> tuple[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} 必须是 JSON object")
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    decoded = json.loads(encoded)
    if type(decoded) is not dict:
        raise TypeError(f"{name} 必须是 JSON object")
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def proactive_outreach_occurrence_key(
    *,
    log_id: int,
    idempotency_key: str,
    source_revision: str = "",
) -> str:
    """把业务幂等事实映射为不暴露原始 key 的 run occurrence。"""

    if type(log_id) is not int or log_id <= 0:
        raise ValueError("log_id 必须是正整数")
    raw_key = _text(
        idempotency_key,
        name="idempotency_key",
        max_length=1024,
    )
    revision = str(source_revision or "").strip()
    digest = hashlib.sha256(
        f"{raw_key}\0{revision}".encode("utf-8")
    ).hexdigest()
    return f"proactive-outreach:{log_id}:{digest}"


def proactive_outreach_delivery_key(
    *,
    log_id: int,
    idempotency_key: str,
    source_revision: str = "",
) -> str:
    occurrence = proactive_outreach_occurrence_key(
        log_id=log_id,
        idempotency_key=idempotency_key,
        source_revision=source_revision,
    )
    digest = hashlib.sha256(occurrence.encode("utf-8")).hexdigest()
    return f"proactive-delivery:{log_id}:{digest}"


def proactive_outreach_destination_fingerprint(user_id: str) -> str:
    target = _text(user_id, name="user_id", max_length=255)
    return hashlib.sha256(
        f"qq_push\0private\0{target}".encode("utf-8")
    ).hexdigest()


def _proactive_grounding(raw: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise OutboundConflictError("主动外呼 grounding 不是有效 JSON object") from exc
    if type(parsed) is not dict:
        raise OutboundConflictError("主动外呼 grounding 必须是 JSON object")
    return parsed


def _normalized_proactive_generation_metadata(
    value: Any,
) -> dict[str, Any]:
    if type(value) is not dict:
        raise OutboundConflictError("主动外呼 generated 来源缺少生成元数据")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise OutboundConflictError("主动外呼 generated 生成元数据版本无效")
    kind = value.get("kind")
    if type(kind) is not str or kind not in PROACTIVE_GENERATION_KINDS:
        raise OutboundConflictError("主动外呼 generated 生成类型无效")
    judge = value.get("judge")
    if type(judge) is not dict:
        raise OutboundConflictError("主动外呼 generated 来源缺少冻结 Judge")
    required_judge_fields = {
        "should_reach_out",
        "reason",
        "next_check_at",
        "next_intent",
        "outreach_kind",
        "research_query",
        "error_type",
    }
    if not required_judge_fields.issubset(judge):
        raise OutboundConflictError("主动外呼 generated 冻结 Judge 字段不完整")
    if judge.get("should_reach_out") is not True:
        raise OutboundConflictError("主动外呼 generated 冻结 Judge 必须允许生成")
    outreach_kind = judge.get("outreach_kind")
    if outreach_kind not in {"message", "research"}:
        raise OutboundConflictError("主动外呼 generated 冻结 Judge 类型无效")
    if kind == "research" and outreach_kind != "research":
        raise OutboundConflictError("主动外呼 research 类型与冻结 Judge 不一致")
    if kind in {"message", "forced"} and outreach_kind != "message":
        raise OutboundConflictError("主动外呼消息类型与冻结 Judge 不一致")
    input_grounding = value.get("input_grounding")
    if type(input_grounding) is not dict:
        raise OutboundConflictError("主动外呼 generated 来源缺少冻结输入")
    normalized_input, input_sha256 = _canonical_json(
        input_grounding,
        name="proactive_outreach_generation_input",
    )
    if value.get("input_sha256") != input_sha256:
        raise OutboundConflictError("主动外呼 generated 输入摘要不一致")
    normalized_judge, _judge_sha256 = _canonical_json(
        judge,
        name="proactive_outreach_generation_judge",
    )
    normalized = {
        "schema_version": 1,
        "kind": kind,
        "judge": json.loads(normalized_judge),
        "input_grounding": json.loads(normalized_input),
        "input_sha256": input_sha256,
    }
    encoded, _digest = _canonical_json(
        normalized,
        name="proactive_outreach_generation_metadata",
    )
    return json.loads(encoded)


def prepare_proactive_generation_grounding(
    *,
    grounding: Mapping[str, Any],
    kind: str,
    judge: Mapping[str, Any],
) -> dict[str, Any]:
    """构造 generated 来源的权威冻结输入，不包含任何生成后输出。"""

    normalized_input, input_sha256 = _canonical_json(
        grounding,
        name="proactive_outreach_generation_input",
    )
    normalized_judge, _judge_sha256 = _canonical_json(
        judge,
        name="proactive_outreach_generation_judge",
    )
    metadata = _normalized_proactive_generation_metadata({
        "schema_version": 1,
        "kind": kind,
        "judge": json.loads(normalized_judge),
        "input_grounding": json.loads(normalized_input),
        "input_sha256": input_sha256,
    })
    result = json.loads(normalized_input)
    result[PROACTIVE_GENERATION_METADATA_KEY] = metadata
    return result


def proactive_generation_metadata(
    grounding: Mapping[str, Any],
) -> dict[str, Any]:
    """读取并严格校验 generated 来源中的权威生成元数据。"""

    if not isinstance(grounding, Mapping):
        raise OutboundConflictError("主动外呼 grounding 必须是 JSON object")
    return _normalized_proactive_generation_metadata(
        dict(grounding).get(PROACTIVE_GENERATION_METADATA_KEY)
    )


def _snapshot_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _utc_naive(value).isoformat(timespec="microseconds")


def proactive_outreach_source_snapshot(
    row: ProactiveOutreachLog,
) -> dict[str, Any]:
    """生成不包含可变投影状态的主动外呼候选快照。"""

    if row.id is None or int(row.id) <= 0:
        raise OutboundConflictError("主动外呼候选尚未持久化")
    user_id = _text(row.user_id, name="user_id", max_length=255)
    idempotency_key = _text(
        row.idempotency_key,
        name="idempotency_key",
        max_length=1024,
    )
    if row.created_at is None:
        raise OutboundConflictError("主动外呼候选缺少 created_at")
    return {
        "schema_version": 1,
        "log_id": int(row.id),
        "user_id": user_id,
        "idempotency_key": idempotency_key,
        "grounding": _proactive_grounding(row.grounding_json),
        "judge_should": (
            None if row.judge_should is None else bool(row.judge_should)
        ),
        "judge_reason": str(row.judge_reason or ""),
        "next_check_at": _snapshot_datetime(row.next_check_at),
        "next_intent": str(row.next_intent or ""),
        "message": str(row.message or ""),
        "forced": bool(row.forced),
        "created_at": _snapshot_datetime(row.created_at),
    }


def proactive_outreach_generated_source_snapshot(
    row: ProactiveOutreachLog,
) -> dict[str, Any]:
    """冻结正文生成前输入，允许同事务追加受控的生成输出字段。"""

    if row.id is None or int(row.id) <= 0:
        raise OutboundConflictError("主动外呼候选尚未持久化")
    user_id = _text(row.user_id, name="user_id", max_length=255)
    idempotency_key = _text(
        row.idempotency_key,
        name="idempotency_key",
        max_length=1024,
    )
    if row.created_at is None:
        raise OutboundConflictError("主动外呼候选缺少 created_at")
    grounding = _proactive_grounding(row.grounding_json)
    metadata = proactive_generation_metadata(grounding)
    input_grounding = metadata["input_grounding"]
    normalized_input, input_sha256 = _canonical_json(
        input_grounding,
        name="proactive_outreach_generation_input",
    )
    if str(metadata.get("input_sha256") or "") != input_sha256:
        raise OutboundConflictError("主动外呼 generated 输入摘要不一致")
    current_input = dict(grounding)
    current_input.pop(PROACTIVE_GENERATION_METADATA_KEY, None)
    for output_key in ("research", "forced_fallback", "generation_error"):
        current_input.pop(output_key, None)
    normalized_current, _current_sha256 = _canonical_json(
        current_input,
        name="proactive_outreach_generation_current_input",
    )
    if normalized_current != normalized_input:
        raise OutboundConflictError("主动外呼 generated 冻结输入已变化")
    return {
        "schema_version": 2,
        "log_id": int(row.id),
        "user_id": user_id,
        "idempotency_key": idempotency_key,
        "generation": metadata,
        "created_at": _snapshot_datetime(row.created_at),
    }


def proactive_outreach_source_revision(row: ProactiveOutreachLog) -> str:
    _encoded, digest = _canonical_json(
        proactive_outreach_source_snapshot(row),
        name="proactive_outreach_source_snapshot",
    )
    return digest


def proactive_outreach_generated_source_revision(
    row: ProactiveOutreachLog,
) -> str:
    _encoded, digest = _canonical_json(
        proactive_outreach_generated_source_snapshot(row),
        name="proactive_outreach_generated_source_snapshot",
    )
    return digest


def _validated_proactive_source(
    db: Session,
    run: OutboundRun,
) -> tuple[ProactiveOutreachLog, dict[str, Any]]:
    try:
        log_id = int(str(run.source_id))
    except (TypeError, ValueError) as exc:
        raise OutboundFencingError("主动外呼 run 的 source_id 无效") from exc
    row = db.get(ProactiveOutreachLog, log_id)
    if row is None:
        raise OutboundFencingError("主动外呼来源记录不存在")
    try:
        parsed = json.loads(str(run.source_snapshot_json or ""))
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise OutboundFencingError("主动外呼冻结快照无法解析") from exc
    if type(parsed) is not dict:
        raise OutboundFencingError("主动外呼冻结快照必须是 JSON object")
    canonical, digest = _canonical_json(parsed, name="proactive_outreach_source_snapshot")
    if (
        canonical != str(run.source_snapshot_json)
        or digest != str(run.source_snapshot_sha256)
        or digest != str(run.source_revision)
    ):
        raise OutboundFencingError("主动外呼冻结快照完整性校验失败")
    current_snapshot = (
        proactive_outreach_generated_source_snapshot(row)
        if str(run.task_kind) == PROACTIVE_GENERATED_TASK_KIND
        else proactive_outreach_source_snapshot(row)
    )
    current_json, current_revision = _canonical_json(
        current_snapshot,
        name="proactive_outreach_source_snapshot",
    )
    expected_occurrence = proactive_outreach_occurrence_key(
        log_id=log_id,
        idempotency_key=str(row.idempotency_key or ""),
        source_revision=str(run.source_revision),
    )
    if (
        current_json != canonical
        or current_revision != str(run.source_revision)
        or parsed.get("log_id") != log_id
        or str(run.occurrence_key) != expected_occurrence
    ):
        raise OutboundFencingError("主动外呼来源 revision 已变化")
    return row, current_snapshot


def proactive_outreach_linkage_is_current(
    db: Session,
    *,
    run_id: int,
) -> bool:
    """校验主动外呼来源与冻结 run 是否仍为同一 revision。"""

    run = db.get(OutboundRun, int(run_id))
    if run is None or str(run.source_type) != "proactive_outreach":
        return False
    try:
        row, _snapshot = _validated_proactive_source(db, run)
    except (OutboundFencingError, OutboundConflictError):
        return False
    return row.outbound_run_id == int(run.id)


def _proactive_projection_query(
    db: Session,
    *,
    row: ProactiveOutreachLog,
    run: OutboundRun,
):
    query = db.query(ProactiveOutreachLog).filter(
        ProactiveOutreachLog.id == int(row.id),
        ProactiveOutreachLog.user_id == row.user_id,
        ProactiveOutreachLog.idempotency_key == row.idempotency_key,
        ProactiveOutreachLog.grounding_json == row.grounding_json,
        ProactiveOutreachLog.judge_reason == row.judge_reason,
        ProactiveOutreachLog.next_check_at == row.next_check_at,
        ProactiveOutreachLog.next_intent == row.next_intent,
        ProactiveOutreachLog.message == row.message,
        ProactiveOutreachLog.forced.is_(bool(row.forced)),
        ProactiveOutreachLog.created_at == row.created_at,
    )
    if row.judge_should is None:
        query = query.filter(ProactiveOutreachLog.judge_should.is_(None))
    else:
        query = query.filter(
            ProactiveOutreachLog.judge_should.is_(bool(row.judge_should))
        )
    return query


def _project_proactive_outreach(
    db: Session,
    *,
    run: OutboundRun,
    status: str,
    current: datetime,
    error_summary: Any = "",
    bind_run: bool = False,
    attempted: bool = False,
    succeeded: bool = False,
) -> None:
    del current, error_summary, attempted
    if str(run.source_type) != "proactive_outreach":
        return
    row, _snapshot = _validated_proactive_source(db, run)
    normalized = str(status or "")
    projection_status = {
        "claimed": "candidate",
        "generating": "candidate",
        "queued": "queued",
        "delivering": "delivering",
        "retry_wait": "retry_wait",
        "blocked": "blocked",
        "failed": "failed",
        "cancelled": "cancelled",
        "ambiguous": "ambiguous",
        "delivered": "sent",
        "succeeded": "sent",
        "succeeded_after_ambiguous_replay": "sent_after_ambiguous_replay",
    }.get(normalized)
    if succeeded:
        projection_status = (
            "sent_after_ambiguous_replay"
            if bool(run.has_ambiguous_ancestor)
            else "sent"
        )
    if projection_status is None:
        raise OutboundFencingError("主动外呼来源投影状态无效")
    query = _proactive_projection_query(db, row=row, run=run)
    if bind_run:
        query = query.filter(
            ProactiveOutreachLog.status.in_(("pending", "candidate")),
            or_(
                ProactiveOutreachLog.outbound_run_id.is_(None),
                ProactiveOutreachLog.outbound_run_id == int(run.id),
            ),
        )
    else:
        query = query.filter(
            ProactiveOutreachLog.outbound_run_id == int(run.id)
        )
    values: dict[Any, Any] = {ProactiveOutreachLog.status: projection_status}
    if bind_run:
        values[ProactiveOutreachLog.outbound_run_id] = int(run.id)
    updated = query.update(values, synchronize_session=False)
    if updated != 1:
        raise OutboundFencingError("主动外呼来源投影 CAS 失败")


def _project_outbound_source(
    db: Session,
    *,
    run: OutboundRun,
    status: str,
    current: datetime,
    error_summary: Any = "",
    bind_run: bool = False,
    attempted: bool = False,
    succeeded: bool = False,
) -> None:
    _project_scheduled_task(
        db,
        run=run,
        status=status,
        current=current,
        error_summary=error_summary,
        bind_run=bind_run,
        attempted=attempted,
        succeeded=succeeded,
    )
    _project_proactive_outreach(
        db,
        run=run,
        status=status,
        current=current,
        error_summary=error_summary,
        bind_run=bind_run,
        attempted=attempted,
        succeeded=succeeded,
    )


def _audit_request_sha256(
    namespace: str,
    facts: Mapping[str, Any],
) -> str:
    encoded, _digest = _canonical_json(facts, name=f"{namespace}_request")
    return _fingerprint(namespace, encoded)


def _audit_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="microseconds")


def _delivery_contract(
    *,
    destination_snapshot: Mapping[str, Any],
    destination_fingerprint: str,
    target_type: str,
    endpoint_key: str,
    payload_contract_fingerprint: str,
) -> tuple[str, str]:
    destination_json, _ = _canonical_json(
        destination_snapshot,
        name="destination_snapshot",
    )
    return _canonical_json(
        {
            "destination_snapshot": json.loads(destination_json),
            "destination_fingerprint": _text(
                destination_fingerprint,
                name="destination_fingerprint",
                max_length=64,
            ),
            "target_type": _text(target_type, name="target_type", max_length=16),
            "endpoint_key": _text(
                endpoint_key,
                name="endpoint_key",
                max_length=64,
            ),
            "payload_contract_fingerprint": _text(
                payload_contract_fingerprint,
                name="payload_contract_fingerprint",
                max_length=64,
            ),
        },
        name="delivery_contract",
    )


def _load_delivery_contract(run: OutboundRun) -> dict[str, Any]:
    try:
        value = json.loads(str(run.delivery_contract_json))
    except (TypeError, ValueError) as exc:
        raise OutboundSafetyError("run 的冻结投递合同无法解析") from exc
    if type(value) is not dict:
        raise OutboundSafetyError("run 的冻结投递合同必须是 JSON object")
    encoded, digest = _canonical_json(value, name="delivery_contract")
    if (
        encoded != str(run.delivery_contract_json)
        or digest != str(run.delivery_contract_sha256)
    ):
        raise OutboundSafetyError("run 的冻结投递合同完整性校验失败")
    return value


def _assert_delivery_contract(
    run: OutboundRun,
    *,
    destination_snapshot: Mapping[str, Any],
    destination_fingerprint: str,
    target_type: str,
    endpoint_key: str,
    payload_contract_fingerprint: str,
) -> dict[str, Any]:
    frozen_contract = _load_delivery_contract(run)
    normalized_frozen_contract = dict(frozen_contract)
    normalized_frozen_contract.pop("endpoint_config_revision", None)
    encoded, _digest = _delivery_contract(
        destination_snapshot=destination_snapshot,
        destination_fingerprint=destination_fingerprint,
        target_type=target_type,
        endpoint_key=endpoint_key,
        payload_contract_fingerprint=payload_contract_fingerprint,
    )
    if encoded != _canonical_json(
        normalized_frozen_contract,
        name="delivery_contract",
    )[0]:
        raise OutboundConflictError("提交事实与 occurrence 冻结投递合同不一致")
    return frozen_contract


def _fingerprint(namespace: str, *parts: str) -> str:
    material = "\0".join((namespace, *parts))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def endpoint_circuit_fingerprint(endpoint_key: str) -> str:
    endpoint = _text(endpoint_key, name="endpoint_key", max_length=64)
    return _fingerprint("endpoint", endpoint)


def destination_circuit_fingerprint(
    endpoint_key: str,
    destination_fingerprint: str,
) -> str:
    endpoint = _text(endpoint_key, name="endpoint_key", max_length=64)
    destination = _text(
        destination_fingerprint,
        name="destination_fingerprint",
        max_length=64,
    )
    return _fingerprint("destination", endpoint, destination)


def payload_contract_circuit_fingerprint(
    endpoint_key: str,
    payload_contract_fingerprint: str,
) -> str:
    endpoint = _text(endpoint_key, name="endpoint_key", max_length=64)
    contract = _text(
        payload_contract_fingerprint,
        name="payload_contract_fingerprint",
        max_length=64,
    )
    return _fingerprint("payload_contract", endpoint, contract)


def _circuit_facts(
    *,
    endpoint_key: str,
    destination_fingerprint: str,
    payload_contract_fingerprint: str,
    config_revision: str,
) -> tuple[tuple[str, str, str], ...]:
    revision = _text(
        config_revision,
        name="endpoint_config_revision",
        max_length=128,
    )
    return (
        ("endpoint", endpoint_circuit_fingerprint(endpoint_key), revision),
        (
            "destination",
            destination_circuit_fingerprint(endpoint_key, destination_fingerprint),
            revision,
        ),
        (
            "payload_contract",
            payload_contract_circuit_fingerprint(
                endpoint_key,
                payload_contract_fingerprint,
            ),
            revision,
        ),
    )


def _open_circuit_row(
    db: Session,
    facts: tuple[tuple[str, str, str], ...],
) -> OutboundDeliveryCircuit | None:
    for scope_type, scope_fingerprint, config_revision in facts:
        row = (
            db.query(OutboundDeliveryCircuit)
            .filter(
                OutboundDeliveryCircuit.scope_type == scope_type,
                OutboundDeliveryCircuit.scope_fingerprint == scope_fingerprint,
                OutboundDeliveryCircuit.config_revision == config_revision,
                OutboundDeliveryCircuit.status == "open",
            )
            .first()
        )
        if row is not None:
            return row
    return None


def _control(db: Session, source_type: str) -> OutboundDeliveryControl:
    row = db.get(OutboundDeliveryControl, source_type)
    if row is None:
        raise OutboundSafetyError(f"source control 不存在: {source_type}")
    return row


def _lock_source_control(db: Session, source_type: str) -> None:
    """以无语义变更的 CAS 获取当前 source 的 SQLite 写锁。"""
    db.flush()
    updated = (
        db.query(OutboundDeliveryControl)
        .filter(OutboundDeliveryControl.source_type == source_type)
        .update(
            {
                OutboundDeliveryControl.writer_version: (
                    OutboundDeliveryControl.writer_version
                ),
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        raise OutboundSafetyError(f"source control 不存在: {source_type}")
    db.flush()
    db.expire_all()


def _locked_current(
    db: Session,
    *,
    source_type: str,
    now: datetime | None,
) -> datetime:
    _lock_source_control(db, source_type)
    return _utc_naive(now)


def lock_outbound_source_control(
    db: Session,
    *,
    source_type: str,
    now: datetime | None = None,
) -> datetime:
    """锁定来源控制行并清除 ORM 缓存，由调用方事务持有到提交。"""

    source = _text(source_type, name="source_type", max_length=32)
    return _locked_current(db, source_type=source, now=now)


def acquire_or_renew_delivery_writer(
    db: Session,
    *,
    source_type: str,
    owner: str,
    token: str,
    protocol_version: int,
    lease_seconds: int | float,
    now: datetime | None = None,
) -> WriterLeaseDecision:
    source = _text(source_type, name="source_type", max_length=32)
    normalized_owner = _text(owner, name="owner", max_length=128)
    normalized_token = _text(token, name="token", max_length=64)
    if type(protocol_version) is not int or protocol_version < 1:
        raise ValueError("protocol_version 必须是正整数")
    seconds = _positive_seconds(lease_seconds, name="lease_seconds")
    current = _locked_current(db, source_type=source, now=now)
    expires_at = current + timedelta(seconds=seconds)
    updated = (
        db.query(OutboundDeliveryControl)
        .filter(
            OutboundDeliveryControl.source_type == source,
            OutboundDeliveryControl.protocol_version <= protocol_version,
            or_(
                OutboundDeliveryControl.writer_lease_expires_at.is_(None),
                OutboundDeliveryControl.writer_lease_expires_at <= current,
                (
                    (OutboundDeliveryControl.writer_owner == normalized_owner)
                    & (OutboundDeliveryControl.writer_token == normalized_token)
                ),
            ),
        )
        .update(
            {
                OutboundDeliveryControl.protocol_version: protocol_version,
                OutboundDeliveryControl.writer_version: (
                    OutboundDeliveryControl.writer_version + 1
                ),
                OutboundDeliveryControl.writer_owner: normalized_owner,
                OutboundDeliveryControl.writer_token: normalized_token,
                OutboundDeliveryControl.writer_lease_expires_at: expires_at,
                OutboundDeliveryControl.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    db.flush()
    db.expire_all()
    row = _control(db, source)
    return WriterLeaseDecision(
        acquired=updated == 1,
        source_type=source,
        owner=normalized_owner if updated == 1 else str(row.writer_owner or ""),
        token=normalized_token if updated == 1 else "",
        protocol_version=int(row.protocol_version),
        writer_version=int(row.writer_version),
        lease_expires_at=row.writer_lease_expires_at,
    )


def release_delivery_writer(
    db: Session,
    *,
    source_type: str,
    owner: str,
    token: str,
    protocol_version: int,
    expected_writer_version: int,
    now: datetime | None = None,
) -> WriterReleaseResult:
    source = _text(source_type, name="source_type", max_length=32)
    normalized_owner = _text(owner, name="owner", max_length=128)
    normalized_token = _text(token, name="token", max_length=64)
    if type(protocol_version) is not int or protocol_version < 1:
        raise ValueError("protocol_version 必须是正整数")
    if type(expected_writer_version) is not int or expected_writer_version < 0:
        raise ValueError("expected_writer_version 必须是非负整数")
    current = _locked_current(db, source_type=source, now=now)
    control = _require_writer(
        db,
        source_type=source,
        owner=normalized_owner,
        token=normalized_token,
        protocol_version=protocol_version,
        current=current,
    )
    if int(control.writer_version) != expected_writer_version:
        raise OutboundFencingError("writer_version CAS 已失效")
    next_writer_version = expected_writer_version + 1
    updated = (
        db.query(OutboundDeliveryControl)
        .filter(
            OutboundDeliveryControl.source_type == source,
            OutboundDeliveryControl.writer_owner == normalized_owner,
            OutboundDeliveryControl.writer_token == normalized_token,
            OutboundDeliveryControl.protocol_version == protocol_version,
            OutboundDeliveryControl.writer_version == expected_writer_version,
            OutboundDeliveryControl.writer_lease_expires_at > current,
        )
        .update(
            {
                OutboundDeliveryControl.writer_version: next_writer_version,
                OutboundDeliveryControl.writer_owner: None,
                OutboundDeliveryControl.writer_token: None,
                OutboundDeliveryControl.writer_lease_expires_at: None,
                OutboundDeliveryControl.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        raise OutboundFencingError("writer release CAS 失败")
    db.flush()
    result = WriterReleaseResult(
        applied=True,
        source_type=source,
        writer_version=next_writer_version,
    )
    db.expire_all()
    return result


def _require_writer(
    db: Session,
    *,
    source_type: str,
    owner: str,
    token: str,
    protocol_version: int,
    current: datetime,
) -> OutboundDeliveryControl:
    row = _control(db, source_type)
    if (
        row.writer_owner != owner
        or row.writer_token != token
        or int(row.protocol_version) != int(protocol_version)
        or row.writer_lease_expires_at is None
        or row.writer_lease_expires_at <= current
    ):
        raise OutboundFencingError("writer lease 已失效")
    return row


def _mode_for_occurrence(
    control: OutboundDeliveryControl,
    *,
    occurrence_at: datetime,
) -> tuple[str, int]:
    if (
        control.mode != "legacy_direct"
        and int(control.protocol_version) < OUTBOUND_PROTOCOL_VERSION
    ):
        raise OutboundSafetyError("outbox control 的协议版本不兼容")
    if control.mode in {"outbox_hold", "outbox_active"}:
        if occurrence_at < control.effective_from:
            return "legacy_direct", max(0, int(control.cutover_epoch) - 1)
        return "outbox", int(control.cutover_epoch)
    if control.mode == "outbox_draining":
        raise OutboundSafetyError("outbox_draining 禁止创建新 occurrence")
    if control.mode == "legacy_direct":
        if int(control.cutover_epoch) > 0 and occurrence_at < control.effective_from:
            raise OutboundSafetyError("该 occurrence 属于已经关闭的旧 epoch")
        return "legacy_direct", int(control.cutover_epoch)
    raise OutboundSafetyError(f"未知 delivery control mode: {control.mode}")


def check_outbound_generation_gate(
    db: Session,
    *,
    source_type: str,
    occurrence_at: datetime,
    endpoint_key: str,
    destination_fingerprint: str,
    endpoint_config_revision: str,
    payload_contract_fingerprint: str,
    now: datetime | None = None,
) -> OutboundGenerationGate:
    """在调用昂贵生成链路前检查 cutover 与持久 circuit。"""

    source = _text(source_type, name="source_type", max_length=32)
    _locked_current(db, source_type=source, now=now)
    control = _control(db, source)
    try:
        delivery_mode, cutover_epoch = _mode_for_occurrence(
            control,
            occurrence_at=_utc_naive(occurrence_at),
        )
    except OutboundSafetyError as exc:
        return OutboundGenerationGate(
            allowed=False,
            delivery_mode="",
            cutover_epoch=int(control.cutover_epoch),
            reason_type="cutover_blocked",
            reason_summary=_summary(exc),
        )
    circuit = _open_circuit_row(db, _circuit_facts(
        endpoint_key=endpoint_key,
        destination_fingerprint=destination_fingerprint,
        payload_contract_fingerprint=payload_contract_fingerprint,
        config_revision=endpoint_config_revision,
    ))
    if circuit is not None:
        return OutboundGenerationGate(
            allowed=False,
            delivery_mode=delivery_mode,
            cutover_epoch=cutover_epoch,
            reason_type="circuit_open",
            reason_summary=f"{circuit.scope_type} circuit 已打开",
        )
    return OutboundGenerationGate(
        allowed=True,
        delivery_mode=delivery_mode,
        cutover_epoch=cutover_epoch,
        reason_type="",
        reason_summary="",
    )


def _run_decision(
    row: OutboundRun,
    *,
    acquired: bool,
    owner: str = "",
    claim_token: str = "",
) -> RunClaimDecision:
    return RunClaimDecision(
        acquired=acquired,
        run_id=int(row.id),
        status=str(row.status),
        owner=owner if acquired else "",
        claim_token=claim_token if acquired else "",
        claim_expires_at=row.claim_expires_at if acquired else None,
        delivery_mode=str(row.delivery_mode),
        cutover_epoch=int(row.cutover_epoch),
        source_snapshot_json=str(row.source_snapshot_json),
        source_snapshot_sha256=str(row.source_snapshot_sha256),
        delivery_contract_json=str(row.delivery_contract_json),
        delivery_contract_sha256=str(row.delivery_contract_sha256),
    )


def _find_run(
    db: Session,
    *,
    source_type: str,
    source_id: str,
    occurrence_key: str,
) -> OutboundRun | None:
    return (
        db.query(OutboundRun)
        .filter(
            OutboundRun.source_type == source_type,
            OutboundRun.source_id == source_id,
            OutboundRun.occurrence_key == occurrence_key,
        )
        .first()
    )


def quarantine_expired_generation_run(
    db: Session,
    *,
    run_id: int,
    expected_source_type: str,
    target_status: str,
    reason_type: str,
    safe_summary: Any,
    now: datetime | None = None,
) -> bool:
    """终结无投递 leaf 的过期生成租约，不提交调用方事务。"""

    source = _text(
        expected_source_type,
        name="expected_source_type",
        max_length=32,
    )
    status = str(target_status or "").strip()
    if status not in {"failed", "blocked"}:
        raise ValueError("target_status 只支持 failed/blocked")
    reason = _text(reason_type, name="reason_type", max_length=64)
    summary = _summary(safe_summary)
    current = _locked_current(db, source_type=source, now=now)
    run = db.get(OutboundRun, int(run_id))
    if run is None or str(run.source_type) != source:
        return False
    no_outbox = ~exists().where(
        OutboundDeliveryOutbox.run_id == OutboundRun.id
    )
    updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run.id),
            OutboundRun.source_type == source,
            OutboundRun.status.in_(tuple(_RUN_CLAIM_STATUSES)),
            OutboundRun.claim_expires_at.is_not(None),
            OutboundRun.claim_expires_at <= current,
            OutboundRun.active_outbox_id.is_(None),
            no_outbox,
        )
        .update(
            {
                OutboundRun.status: status,
                OutboundRun.claim_owner: None,
                OutboundRun.claim_token: None,
                OutboundRun.claim_expires_at: None,
                OutboundRun.failure_type: reason,
                OutboundRun.failure_summary: summary,
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        db.flush()
        db.expire_all()
        return False
    (
        db.query(OutboundGenerationAttempt)
        .filter(
            OutboundGenerationAttempt.run_id == int(run.id),
            OutboundGenerationAttempt.status == "started",
        )
        .update(
            {
                OutboundGenerationAttempt.status: "abandoned",
                OutboundGenerationAttempt.completed_at: current,
                OutboundGenerationAttempt.error_type: reason,
                OutboundGenerationAttempt.error_summary: summary,
            },
            synchronize_session=False,
        )
    )
    _project_outbound_source(
        db,
        run=run,
        status=status,
        current=current,
        error_summary=summary,
    )
    db.flush()
    db.expire_all()
    return True


def claim_outbound_run(
    db: Session,
    *,
    source_type: str,
    source_id: str,
    occurrence_key: str,
    source_revision: str,
    source_snapshot: Mapping[str, Any],
    destination_snapshot: Mapping[str, Any],
    target_type: str,
    task_kind: str,
    scheduled_for: datetime | None,
    trigger_type: str,
    owner: str,
    claim_lease_seconds: int | float,
    writer_owner: str,
    writer_token: str,
    writer_protocol_version: int,
    writer_lease_seconds: int | float,
    endpoint_key: str,
    destination_fingerprint: str,
    endpoint_config_revision: str,
    payload_contract_fingerprint: str,
    now: datetime | None = None,
) -> RunClaimDecision:
    source = _text(source_type, name="source_type", max_length=32)
    source_identity = _text(source_id, name="source_id", max_length=255)
    occurrence = _text(occurrence_key, name="occurrence_key", max_length=255)
    revision = _text(source_revision, name="source_revision", max_length=128)
    kind = _text(task_kind, name="task_kind", max_length=64)
    trigger = _text(trigger_type, name="trigger_type", max_length=32)
    normalized_owner = _text(owner, name="owner", max_length=128)
    normalized_writer_owner = _text(
        writer_owner,
        name="writer_owner",
        max_length=128,
    )
    normalized_writer_token = _text(
        writer_token,
        name="writer_token",
        max_length=64,
    )
    claim_seconds = _positive_seconds(
        claim_lease_seconds,
        name="claim_lease_seconds",
    )
    observed_at = _utc_naive(now)
    logical_occurrence = (
        _utc_naive(scheduled_for) if scheduled_for else observed_at
    )
    snapshot_json, snapshot_sha256 = _canonical_json(
        source_snapshot,
        name="source_snapshot",
    )
    delivery_contract_json, delivery_contract_sha256 = _delivery_contract(
        destination_snapshot=destination_snapshot,
        destination_fingerprint=destination_fingerprint,
        target_type=target_type,
        endpoint_key=endpoint_key,
        payload_contract_fingerprint=payload_contract_fingerprint,
    )
    circuit_facts = _circuit_facts(
        endpoint_key=endpoint_key,
        destination_fingerprint=destination_fingerprint,
        payload_contract_fingerprint=payload_contract_fingerprint,
        config_revision=endpoint_config_revision,
    )

    existing = _find_run(
        db,
        source_type=source,
        source_id=source_identity,
        occurrence_key=occurrence,
    )
    if existing is not None and not (
        existing.active_outbox_id is None
        and (
            (
                existing.status in _RUN_CLAIM_STATUSES
                and existing.claim_expires_at is not None
                and existing.claim_expires_at <= observed_at
            )
            or existing.status == "blocked"
        )
    ):
        return _run_decision(existing, acquired=False)

    writer = acquire_or_renew_delivery_writer(
        db,
        source_type=source,
        owner=normalized_writer_owner,
        token=normalized_writer_token,
        protocol_version=writer_protocol_version,
        lease_seconds=writer_lease_seconds,
        now=now,
    )
    if not writer.acquired:
        existing = _find_run(
            db,
            source_type=source,
            source_id=source_identity,
            occurrence_key=occurrence,
        )
        if existing is not None:
            return _run_decision(existing, acquired=False)
        raise OutboundSafetyError("其他 writer 仍持有 source lease")

    current = _utc_naive(now)

    control = _require_writer(
        db,
        source_type=source,
        owner=normalized_writer_owner,
        token=normalized_writer_token,
        protocol_version=writer_protocol_version,
        current=current,
    )
    delivery_mode, cutover_epoch = _mode_for_occurrence(
        control,
        occurrence_at=logical_occurrence,
    )
    open_circuit = _open_circuit_row(db, circuit_facts)
    claim_token = secrets.token_hex(32)
    claim_expires_at = current + timedelta(seconds=claim_seconds)

    existing = _find_run(
        db,
        source_type=source,
        source_id=source_identity,
        occurrence_key=occurrence,
    )
    if existing is not None:
        if existing.active_outbox_id is not None:
            return _run_decision(existing, acquired=False)
        if open_circuit is not None:
            if existing.status == "blocked":
                return _run_decision(existing, acquired=False)
            if (
                existing.status in _RUN_CLAIM_STATUSES
                and existing.claim_expires_at is not None
                and existing.claim_expires_at <= current
                and quarantine_expired_generation_run(
                    db,
                    run_id=int(existing.id),
                    expected_source_type=source,
                    target_status="blocked",
                    reason_type="circuit_open",
                    safe_summary=(
                        f"{open_circuit.scope_type} circuit 已打开"
                    ),
                    now=current,
                )
            ):
                blocked = db.get(OutboundRun, int(existing.id))
                if blocked is None:
                    raise RuntimeError("熔断终结后未找到 run")
                return _run_decision(blocked, acquired=False)
            return _run_decision(existing, acquired=False)
        if (
            existing.status == "blocked"
            or (
                existing.status in _RUN_CLAIM_STATUSES
                and existing.claim_expires_at is not None
                and existing.claim_expires_at <= current
            )
        ):
            updated = (
                db.query(OutboundRun)
                .filter(
                    OutboundRun.id == existing.id,
                    OutboundRun.active_outbox_id.is_(None),
                    or_(
                        OutboundRun.status == "blocked",
                        (
                            OutboundRun.status.in_(tuple(_RUN_CLAIM_STATUSES))
                            & (OutboundRun.claim_expires_at <= current)
                        ),
                    ),
                )
                .update(
                    {
                        OutboundRun.status: "claimed",
                        OutboundRun.claim_owner: normalized_owner,
                        OutboundRun.claim_token: claim_token,
                        OutboundRun.claim_expires_at: claim_expires_at,
                        OutboundRun.failure_type: "",
                        OutboundRun.failure_summary: "",
                        OutboundRun.writer_owner: normalized_writer_owner,
                        OutboundRun.writer_token: normalized_writer_token,
                        OutboundRun.writer_protocol_version: writer_protocol_version,
                        OutboundRun.updated_at: current,
                    },
                    synchronize_session=False,
                )
            )
            db.flush()
            row = db.get(OutboundRun, int(existing.id))
            if updated == 1 and row is not None:
                _project_outbound_source(
                    db,
                    run=row,
                    status="claimed",
                    current=current,
                )
                db.flush()
                db.expire_all()
                return _run_decision(
                    row,
                    acquired=True,
                    owner=normalized_owner,
                    claim_token=claim_token,
                )
        return _run_decision(existing, acquired=False)

    status = "blocked" if open_circuit is not None else "claimed"
    db.execute(
        sqlite_insert(OutboundRun)
        .values(
            source_type=source,
            source_id=source_identity,
            occurrence_key=occurrence,
            source_revision=revision,
            source_snapshot_json=snapshot_json,
            source_snapshot_sha256=snapshot_sha256,
            delivery_contract_json=delivery_contract_json,
            delivery_contract_sha256=delivery_contract_sha256,
            writer_owner=normalized_writer_owner,
            writer_token=normalized_writer_token,
            writer_protocol_version=writer_protocol_version,
            task_kind=kind,
            scheduled_for=logical_occurrence if scheduled_for is not None else None,
            trigger_type=trigger,
            status=status,
            claim_owner=normalized_owner if status == "claimed" else None,
            claim_token=claim_token if status == "claimed" else None,
            claim_expires_at=claim_expires_at if status == "claimed" else None,
            attempted_at=None,
            generated_at=None,
            succeeded_at=None,
            failure_type=("circuit_open" if open_circuit is not None else ""),
            failure_summary=(
                f"{open_circuit.scope_type} circuit 已打开"
                if open_circuit is not None
                else ""
            ),
            active_outbox_id=None,
            has_ambiguous_ancestor=False,
            delivery_mode=delivery_mode,
            cutover_epoch=cutover_epoch,
            created_at=current,
            updated_at=current,
        )
        .on_conflict_do_nothing(
            index_elements=["source_type", "source_id", "occurrence_key"]
        )
    )
    db.flush()
    db.expire_all()
    row = _find_run(
        db,
        source_type=source,
        source_id=source_identity,
        occurrence_key=occurrence,
    )
    if row is None:
        raise RuntimeError("原子登记 occurrence 后未找到 run")
    inserted_by_caller = row.claim_token == claim_token and row.status == "claimed"
    if inserted_by_caller or row.status == "blocked":
        _project_outbound_source(
            db,
            run=row,
            status=str(row.status),
            current=current,
            error_summary=str(row.failure_summary or ""),
            bind_run=True,
        )
        db.flush()
        db.expire_all()
    return _run_decision(
        row,
        acquired=inserted_by_caller,
        owner=normalized_owner,
        claim_token=claim_token,
    )


def renew_outbound_run_claim(
    db: Session,
    *,
    run_id: int,
    owner: str,
    claim_token: str,
    lease_seconds: int | float,
    now: datetime | None = None,
) -> RunClaimRenewal:
    normalized_owner = _text(owner, name="owner", max_length=128)
    token = _text(claim_token, name="claim_token", max_length=64)
    seconds = _positive_seconds(lease_seconds, name="lease_seconds")
    run_probe = db.get(OutboundRun, int(run_id))
    if run_probe is None:
        return RunClaimRenewal(
            applied=False,
            run_id=int(run_id),
            claim_expires_at=None,
        )
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    expires_at = current + timedelta(seconds=seconds)
    run = db.get(OutboundRun, int(run_id))
    if run is None:
        return RunClaimRenewal(
            applied=False,
            run_id=int(run_id),
            claim_expires_at=None,
        )
    try:
        _require_writer(
            db,
            source_type=str(run.source_type),
            owner=str(run.writer_owner),
            token=str(run.writer_token),
            protocol_version=int(run.writer_protocol_version),
            current=current,
        )
    except OutboundFencingError:
        return RunClaimRenewal(
            applied=False,
            run_id=int(run_id),
            claim_expires_at=None,
        )
    updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run_id),
            OutboundRun.status.in_(tuple(_RUN_CLAIM_STATUSES)),
            OutboundRun.claim_owner == normalized_owner,
            OutboundRun.claim_token == token,
            OutboundRun.claim_expires_at > current,
            OutboundRun.active_outbox_id.is_(None),
        )
        .update(
            {
                OutboundRun.claim_expires_at: expires_at,
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    db.flush()
    result = RunClaimRenewal(
        applied=updated == 1,
        run_id=int(run_id),
        claim_expires_at=expires_at if updated == 1 else None,
    )
    db.expire_all()
    return result


def _require_generation_run(
    db: Session,
    *,
    run_id: int,
    owner: str,
    claim_token: str,
    current: datetime,
    allowed_statuses: set[str],
) -> OutboundRun:
    row = db.get(OutboundRun, int(run_id))
    if (
        row is None
        or row.status not in allowed_statuses
        or row.claim_owner != owner
        or row.claim_token != claim_token
        or row.claim_expires_at is None
        or row.claim_expires_at <= current
        or row.active_outbox_id is not None
    ):
        raise OutboundFencingError("generation claim 已失效")
    return row


def _assert_run_control(
    db: Session,
    *,
    run: OutboundRun,
    current: datetime,
) -> OutboundDeliveryControl:
    control = _control(db, str(run.source_type))
    mode, epoch = _mode_for_occurrence(
        control,
        occurrence_at=run.scheduled_for or current,
    )
    if mode != run.delivery_mode or epoch != int(run.cutover_epoch):
        raise OutboundSafetyError("run 与当前 cutover control 不一致")
    return control


def _block_generation_claim(
    db: Session,
    *,
    run: OutboundRun,
    owner: str,
    claim_token: str,
    reason_type: str,
    reason_summary: str,
    current: datetime,
) -> bool:
    updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run.id),
            OutboundRun.status == "claimed",
            OutboundRun.claim_owner == owner,
            OutboundRun.claim_token == claim_token,
            OutboundRun.claim_expires_at > current,
            OutboundRun.active_outbox_id.is_(None),
        )
        .update(
            {
                OutboundRun.status: "blocked",
                OutboundRun.claim_owner: None,
                OutboundRun.claim_token: None,
                OutboundRun.claim_expires_at: None,
                OutboundRun.failure_type: reason_type[:64],
                OutboundRun.failure_summary: _summary(reason_summary),
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if updated == 1:
        _project_outbound_source(
            db,
            run=run,
            status="blocked",
            current=current,
            error_summary=reason_summary,
        )
    db.flush()
    db.expire_all()
    return updated == 1


def start_generation_attempt(
    db: Session,
    *,
    run_id: int,
    owner: str,
    claim_token: str,
    writer_owner: str,
    writer_token: str,
    writer_protocol_version: int,
    endpoint_key: str,
    destination_fingerprint: str,
    endpoint_config_revision: str,
    payload_contract_fingerprint: str,
    now: datetime | None = None,
) -> GenerationAttemptHandle:
    normalized_owner = _text(owner, name="owner", max_length=128)
    token = _text(claim_token, name="claim_token", max_length=64)
    run_probe = db.get(OutboundRun, int(run_id))
    if run_probe is None:
        raise OutboundFencingError("generation claim 已失效")
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    run = _require_generation_run(
        db,
        run_id=run_id,
        owner=normalized_owner,
        claim_token=token,
        current=current,
        allowed_statuses={"claimed"},
    )
    normalized_writer_owner = _text(
        writer_owner,
        name="writer_owner",
        max_length=128,
    )
    normalized_writer_token = _text(
        writer_token,
        name="writer_token",
        max_length=64,
    )
    if (
        run.writer_owner != normalized_writer_owner
        or run.writer_token != normalized_writer_token
        or int(run.writer_protocol_version) != int(writer_protocol_version)
    ):
        raise OutboundFencingError("generation writer fence 与 occurrence 不一致")
    _require_writer(
        db,
        source_type=str(run.source_type),
        owner=normalized_writer_owner,
        token=normalized_writer_token,
        protocol_version=writer_protocol_version,
        current=current,
    )
    revision = _text(
        endpoint_config_revision,
        name="endpoint_config_revision",
        max_length=128,
    )
    frozen_contract = _load_delivery_contract(run)
    normalized_frozen_contract = dict(frozen_contract)
    normalized_frozen_contract.pop("endpoint_config_revision", None)
    expected_circuit_facts = {
        "endpoint_key": _text(endpoint_key, name="endpoint_key", max_length=64),
        "destination_fingerprint": _text(
            destination_fingerprint,
            name="destination_fingerprint",
            max_length=64,
        ),
        "payload_contract_fingerprint": _text(
            payload_contract_fingerprint,
            name="payload_contract_fingerprint",
            max_length=64,
        ),
    }
    if any(
        normalized_frozen_contract.get(name) != value
        for name, value in expected_circuit_facts.items()
    ):
        raise OutboundConflictError("生成事实与 occurrence 冻结投递合同不一致")
    try:
        _assert_run_control(db, run=run, current=current)
    except OutboundSafetyError as exc:
        _block_generation_claim(
            db,
            run=run,
            owner=normalized_owner,
            claim_token=token,
            reason_type="cutover_changed",
            reason_summary=str(exc),
            current=current,
        )
        return GenerationAttemptHandle(
            run_id=int(run.id),
            attempt_id=None,
            attempt_no=None,
            owner="",
            fencing_token="",
            status="blocked",
            reason_type="cutover_changed",
        )
    circuit = _open_circuit_row(db, _circuit_facts(
        endpoint_key=endpoint_key,
        destination_fingerprint=destination_fingerprint,
        payload_contract_fingerprint=payload_contract_fingerprint,
        config_revision=revision,
    ))
    if circuit is not None:
        _block_generation_claim(
            db,
            run=run,
            owner=normalized_owner,
            claim_token=token,
            reason_type="circuit_open",
            reason_summary=f"{circuit.scope_type} circuit 已打开",
            current=current,
        )
        return GenerationAttemptHandle(
            run_id=int(run.id),
            attempt_id=None,
            attempt_no=None,
            owner="",
            fencing_token="",
            status="blocked",
            reason_type="circuit_open",
        )

    (
        db.query(OutboundGenerationAttempt)
        .filter(
            OutboundGenerationAttempt.run_id == run.id,
            OutboundGenerationAttempt.status == "started",
            OutboundGenerationAttempt.fencing_token != token,
        )
        .update(
            {
                OutboundGenerationAttempt.status: "abandoned",
                OutboundGenerationAttempt.completed_at: current,
                OutboundGenerationAttempt.error_type: "claim_expired",
                OutboundGenerationAttempt.error_summary: "生成租约已被新 owner 接管",
            },
            synchronize_session=False,
        )
    )
    next_attempt_no = int(
        db.query(func.max(OutboundGenerationAttempt.attempt_no))
        .filter(OutboundGenerationAttempt.run_id == run.id)
        .scalar()
        or 0
    ) + 1
    updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == run.id,
            OutboundRun.status == "claimed",
            OutboundRun.claim_owner == normalized_owner,
            OutboundRun.claim_token == token,
            OutboundRun.claim_expires_at > current,
            OutboundRun.active_outbox_id.is_(None),
        )
        .update(
            {
                OutboundRun.status: "generating",
                OutboundRun.attempted_at: func.coalesce(
                    OutboundRun.attempted_at,
                    current,
                ),
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        raise OutboundFencingError("generation claim CAS 失败")
    attempt = OutboundGenerationAttempt(
        run_id=int(run.id),
        attempt_no=next_attempt_no,
        owner=normalized_owner,
        fencing_token=token,
        status="started",
        started_at=current,
        completed_at=None,
        model_trace_id="",
        content_sha256="",
        error_type="",
        error_summary="",
        created_at=current,
    )
    db.add(attempt)
    db.flush()
    _project_outbound_source(
        db,
        run=run,
        status="generating",
        current=current,
        attempted=True,
    )
    db.flush()
    result = GenerationAttemptHandle(
        run_id=int(run.id),
        attempt_id=int(attempt.id),
        attempt_no=next_attempt_no,
        owner=normalized_owner,
        fencing_token=token,
        status="started",
        reason_type="",
    )
    db.expire_all()
    return result


def _same_outbox_facts(
    row: OutboundDeliveryOutbox,
    *,
    run_id: int,
    idempotency_key: str,
    destination_snapshot_json: str,
    destination_fingerprint: str,
    target_type: str,
    endpoint_key: str,
    payload_json: str,
    payload_sha256: str,
    max_attempts: int,
    retry_deadline_at: datetime,
    endpoint_config_revision: str,
    payload_contract_fingerprint: str,
) -> bool:
    return (
        int(row.run_id) == int(run_id)
        and row.idempotency_key == idempotency_key
        and row.destination_snapshot_json == destination_snapshot_json
        and row.destination_fingerprint == destination_fingerprint
        and row.target_type == target_type
        and row.endpoint_key == endpoint_key
        and row.payload_json == payload_json
        and row.payload_sha256 == payload_sha256
        and int(row.max_attempts) == int(max_attempts)
        and row.retry_deadline_at == retry_deadline_at
        and row.endpoint_config_revision == endpoint_config_revision
        and row.payload_contract_fingerprint == payload_contract_fingerprint
        and int(row.replay_sequence) == 0
        and row.replay_of_outbox_id is None
    )


def _assert_terminal_generation_settlement(
    db: Session,
    *,
    run_id: int,
    generation_attempt_id: int,
    owner: str,
    claim_token: str,
    outbox: OutboundDeliveryOutbox,
    payload_sha256: str,
    model_trace_id: str,
    generation_error_type: str,
    generation_error_summary: str,
) -> None:
    """验证已有 outbox 确由本次 generation attempt 按相同事实结算。"""

    db.expire_all()
    run = db.get(OutboundRun, int(run_id))
    attempt = db.get(OutboundGenerationAttempt, int(generation_attempt_id))
    if (
        run is None
        or int(outbox.run_id) != int(run_id)
        or run.active_outbox_id != int(outbox.id)
        or attempt is None
        or int(attempt.run_id) != int(run_id)
        or attempt.owner != owner
        or attempt.fencing_token != claim_token
    ):
        raise OutboundFencingError("已有 outbox 不属于当前 generation attempt")
    if attempt.status in {"started", "abandoned"}:
        raise OutboundFencingError("generation attempt 不是 outbox 的终态赢家")
    expected_status = "failed" if generation_error_type else "succeeded"
    expected_content_sha256 = "" if generation_error_type else payload_sha256
    if (
        attempt.status != expected_status
        or str(attempt.content_sha256 or "") != expected_content_sha256
        or str(attempt.error_type or "") != generation_error_type
        or str(attempt.error_summary or "") != generation_error_summary
        or str(attempt.model_trace_id or "") != model_trace_id
    ):
        raise OutboundConflictError("已有 outbox 的 generation 结算事实不一致")


def _block_prepared_outbound_run(
    db: Session,
    *,
    run: OutboundRun,
    owner: str,
    claim_token: str,
    reason_type: str,
    reason_summary: str,
    current: datetime,
) -> None:
    updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run.id),
            OutboundRun.status == "claimed",
            OutboundRun.claim_owner == owner,
            OutboundRun.claim_token == claim_token,
            OutboundRun.claim_expires_at > current,
            OutboundRun.active_outbox_id.is_(None),
        )
        .update(
            {
                OutboundRun.status: "blocked",
                OutboundRun.claim_owner: None,
                OutboundRun.claim_token: None,
                OutboundRun.claim_expires_at: None,
                OutboundRun.failure_type: reason_type[:64],
                OutboundRun.failure_summary: _summary(reason_summary),
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        raise OutboundFencingError("预生成候选阻断 CAS 失败")
    _project_outbound_source(
        db,
        run=run,
        status="blocked",
        current=current,
        error_summary=reason_summary,
    )
    db.flush()


def commit_prepared_outbox(
    db: Session,
    *,
    run_id: int,
    owner: str,
    claim_token: str,
    idempotency_key: str,
    destination_snapshot: Mapping[str, Any],
    destination_fingerprint: str,
    target_type: str,
    endpoint_key: str,
    payload: Mapping[str, Any],
    max_attempts: int,
    retry_deadline_at: datetime,
    endpoint_config_revision: str,
    payload_contract_fingerprint: str,
    now: datetime | None = None,
) -> OutboxCommitResult:
    """把已完成业务评估的候选原子提交为 outbox，不伪造模型 attempt。"""

    normalized_owner = _text(owner, name="owner", max_length=128)
    token = _text(claim_token, name="claim_token", max_length=64)
    run_probe = db.get(OutboundRun, int(run_id))
    if run_probe is None:
        raise OutboundFencingError("prepared candidate claim 已失效")
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    key = _text(idempotency_key, name="idempotency_key", max_length=255)
    destination_json, _destination_sha = _canonical_json(
        destination_snapshot,
        name="destination_snapshot",
    )
    destination = _text(
        destination_fingerprint,
        name="destination_fingerprint",
        max_length=64,
    )
    normalized_target_type = _text(target_type, name="target_type", max_length=16)
    endpoint = _text(endpoint_key, name="endpoint_key", max_length=64)
    payload_json, payload_sha256 = _canonical_json(payload, name="payload")
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts 必须是正整数")
    deadline = _utc_naive(retry_deadline_at)
    if deadline <= current:
        raise ValueError("retry_deadline_at 必须晚于 now")
    revision = _text(
        endpoint_config_revision,
        name="endpoint_config_revision",
        max_length=128,
    )
    contract = _text(
        payload_contract_fingerprint,
        name="payload_contract_fingerprint",
        max_length=64,
    )

    existing = (
        db.query(OutboundDeliveryOutbox)
        .filter(OutboundDeliveryOutbox.idempotency_key == key)
        .first()
    )
    if existing is not None:
        if not _same_outbox_facts(
            existing,
            run_id=run_id,
            idempotency_key=key,
            destination_snapshot_json=destination_json,
            destination_fingerprint=destination,
            target_type=normalized_target_type,
            endpoint_key=endpoint,
            payload_json=payload_json,
            payload_sha256=payload_sha256,
            max_attempts=max_attempts,
            retry_deadline_at=deadline,
            endpoint_config_revision=revision,
            payload_contract_fingerprint=contract,
        ):
            raise OutboundConflictError("同一 idempotency_key 的不可变事实不一致")
        return OutboxCommitResult(
            outbox_id=int(existing.id),
            run_id=int(existing.run_id),
            created=False,
            payload_sha256=str(existing.payload_sha256),
            status=str(existing.status),
            reason_type="",
        )

    run = _require_generation_run(
        db,
        run_id=run_id,
        owner=normalized_owner,
        claim_token=token,
        current=current,
        allowed_statuses={"claimed"},
    )
    _require_writer(
        db,
        source_type=str(run.source_type),
        owner=str(run.writer_owner),
        token=str(run.writer_token),
        protocol_version=int(run.writer_protocol_version),
        current=current,
    )
    _assert_delivery_contract(
        run,
        destination_snapshot=destination_snapshot,
        destination_fingerprint=destination,
        target_type=normalized_target_type,
        endpoint_key=endpoint,
        payload_contract_fingerprint=contract,
    )
    try:
        _assert_run_control(db, run=run, current=current)
    except OutboundSafetyError as exc:
        _block_prepared_outbound_run(
            db,
            run=run,
            owner=normalized_owner,
            claim_token=token,
            reason_type="cutover_changed",
            reason_summary=str(exc),
            current=current,
        )
        return OutboxCommitResult(
            outbox_id=None,
            run_id=int(run.id),
            created=False,
            payload_sha256=payload_sha256,
            status="blocked",
            reason_type="cutover_changed",
        )
    open_circuit = _open_circuit_row(db, _circuit_facts(
        endpoint_key=endpoint,
        destination_fingerprint=destination,
        payload_contract_fingerprint=contract,
        config_revision=revision,
    ))
    if open_circuit is not None:
        _block_prepared_outbound_run(
            db,
            run=run,
            owner=normalized_owner,
            claim_token=token,
            reason_type="circuit_open",
            reason_summary=f"{open_circuit.scope_type} circuit 已打开",
            current=current,
        )
        return OutboxCommitResult(
            outbox_id=None,
            run_id=int(run.id),
            created=False,
            payload_sha256=payload_sha256,
            status="blocked",
            reason_type="circuit_open",
        )

    insert_result = db.execute(
        sqlite_insert(OutboundDeliveryOutbox)
        .values(
            run_id=int(run.id),
            idempotency_key=key,
            destination_snapshot_json=destination_json,
            destination_fingerprint=destination,
            target_type=normalized_target_type,
            endpoint_key=endpoint,
            payload_json=payload_json,
            payload_sha256=payload_sha256,
            status="pending",
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            next_attempt_at=None,
            allocated_attempt_count=0,
            request_started_count=0,
            max_attempts=max_attempts,
            retry_deadline_at=deadline,
            last_error_type="",
            last_error_summary="",
            delivered_at=None,
            cancelled_at=None,
            cancel_reason_type=None,
            replay_of_outbox_id=None,
            replay_sequence=0,
            replay_request_sha256="",
            cutover_epoch=int(run.cutover_epoch),
            endpoint_config_revision=revision,
            payload_contract_fingerprint=contract,
            created_at=current,
            updated_at=current,
        )
        .on_conflict_do_nothing()
    )
    created = insert_result.rowcount == 1
    outbox = (
        db.query(OutboundDeliveryOutbox)
        .filter(
            or_(
                OutboundDeliveryOutbox.idempotency_key == key,
                (
                    (OutboundDeliveryOutbox.run_id == int(run.id))
                    & (
                        OutboundDeliveryOutbox.destination_fingerprint
                        == destination
                    )
                    & (OutboundDeliveryOutbox.replay_sequence == 0)
                ),
            )
        )
        .order_by(OutboundDeliveryOutbox.id.asc())
        .first()
    )
    if outbox is None or not _same_outbox_facts(
        outbox,
        run_id=run_id,
        idempotency_key=key,
        destination_snapshot_json=destination_json,
        destination_fingerprint=destination,
        target_type=normalized_target_type,
        endpoint_key=endpoint,
        payload_json=payload_json,
        payload_sha256=payload_sha256,
        max_attempts=max_attempts,
        retry_deadline_at=deadline,
        endpoint_config_revision=revision,
        payload_contract_fingerprint=contract,
    ):
        raise OutboundConflictError("outbox 唯一叶已存在不同事实")
    if not created:
        return OutboxCommitResult(
            outbox_id=int(outbox.id),
            run_id=int(outbox.run_id),
            created=False,
            payload_sha256=str(outbox.payload_sha256),
            status=str(outbox.status),
            reason_type="",
        )

    run_updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run.id),
            OutboundRun.status == "claimed",
            OutboundRun.claim_owner == normalized_owner,
            OutboundRun.claim_token == token,
            OutboundRun.claim_expires_at > current,
            OutboundRun.active_outbox_id.is_(None),
        )
        .update(
            {
                OutboundRun.status: "queued",
                OutboundRun.claim_owner: None,
                OutboundRun.claim_token: None,
                OutboundRun.claim_expires_at: None,
                OutboundRun.attempted_at: current,
                OutboundRun.generated_at: current,
                OutboundRun.active_outbox_id: int(outbox.id),
                OutboundRun.failure_type: "",
                OutboundRun.failure_summary: "",
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if run_updated != 1:
        raise OutboundFencingError("预生成候选提交 CAS 失败")
    _project_outbound_source(
        db,
        run=run,
        status="queued",
        current=current,
    )
    db.flush()
    result = OutboxCommitResult(
        outbox_id=int(outbox.id),
        run_id=int(run.id),
        created=True,
        payload_sha256=payload_sha256,
        status="pending",
        reason_type="",
    )
    db.expire_all()
    return result


def _abandon_generated_result(
    db: Session,
    *,
    run: OutboundRun,
    attempt: OutboundGenerationAttempt,
    owner: str,
    claim_token: str,
    reason_type: str,
    reason_summary: str,
    current: datetime,
) -> None:
    attempt_updated = (
        db.query(OutboundGenerationAttempt)
        .filter(
            OutboundGenerationAttempt.id == int(attempt.id),
            OutboundGenerationAttempt.run_id == int(run.id),
            OutboundGenerationAttempt.status == "started",
            OutboundGenerationAttempt.owner == owner,
            OutboundGenerationAttempt.fencing_token == claim_token,
        )
        .update(
            {
                OutboundGenerationAttempt.status: "abandoned",
                OutboundGenerationAttempt.completed_at: current,
                OutboundGenerationAttempt.error_type: reason_type[:64],
                OutboundGenerationAttempt.error_summary: _summary(reason_summary),
            },
            synchronize_session=False,
        )
    )
    run_updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run.id),
            OutboundRun.status == "generating",
            OutboundRun.claim_owner == owner,
            OutboundRun.claim_token == claim_token,
            OutboundRun.claim_expires_at > current,
            OutboundRun.active_outbox_id.is_(None),
        )
        .update(
            {
                OutboundRun.status: "blocked",
                OutboundRun.claim_owner: None,
                OutboundRun.claim_token: None,
                OutboundRun.claim_expires_at: None,
                OutboundRun.failure_type: reason_type[:64],
                OutboundRun.failure_summary: _summary(reason_summary),
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if attempt_updated != 1 or run_updated != 1:
        raise OutboundFencingError("生成结果废弃 CAS 失败")
    _project_outbound_source(
        db,
        run=run,
        status="blocked",
        current=current,
        error_summary=reason_summary,
    )
    db.flush()
    db.expire_all()


def commit_generated_outbox(
    db: Session,
    *,
    run_id: int,
    generation_attempt_id: int,
    owner: str,
    claim_token: str,
    idempotency_key: str,
    destination_snapshot: Mapping[str, Any],
    destination_fingerprint: str,
    target_type: str,
    endpoint_key: str,
    payload: Mapping[str, Any],
    max_attempts: int,
    retry_deadline_at: datetime,
    endpoint_config_revision: str,
    payload_contract_fingerprint: str,
    model_trace_id: str = "",
    generation_error_type: str = "",
    generation_error_summary: Any = "",
    now: datetime | None = None,
) -> OutboxCommitResult:
    normalized_owner = _text(owner, name="owner", max_length=128)
    token = _text(claim_token, name="claim_token", max_length=64)
    run_probe = db.get(OutboundRun, int(run_id))
    if run_probe is None:
        raise OutboundFencingError("generation claim 已失效")
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    key = _text(idempotency_key, name="idempotency_key", max_length=255)
    destination_json, _destination_sha = _canonical_json(
        destination_snapshot,
        name="destination_snapshot",
    )
    destination = _text(
        destination_fingerprint,
        name="destination_fingerprint",
        max_length=64,
    )
    normalized_target_type = _text(target_type, name="target_type", max_length=16)
    endpoint = _text(endpoint_key, name="endpoint_key", max_length=64)
    payload_json, payload_sha256 = _canonical_json(payload, name="payload")
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts 必须是正整数")
    deadline = _utc_naive(retry_deadline_at)
    if deadline <= current:
        raise ValueError("retry_deadline_at 必须晚于 now")
    revision = _text(
        endpoint_config_revision,
        name="endpoint_config_revision",
        max_length=128,
    )
    contract = _text(
        payload_contract_fingerprint,
        name="payload_contract_fingerprint",
        max_length=64,
    )
    trace_id = str(model_trace_id or "")[:128]
    fallback_error_type = str(generation_error_type or "").strip()
    if len(fallback_error_type) > 64:
        raise ValueError("generation_error_type 不能超过 64 字符")
    fallback_error_summary = (
        _summary(generation_error_summary) if fallback_error_type else ""
    )

    existing = (
        db.query(OutboundDeliveryOutbox)
        .filter(OutboundDeliveryOutbox.idempotency_key == key)
        .first()
    )
    if existing is not None:
        if not _same_outbox_facts(
            existing,
            run_id=run_id,
            idempotency_key=key,
            destination_snapshot_json=destination_json,
            destination_fingerprint=destination,
            target_type=normalized_target_type,
            endpoint_key=endpoint,
            payload_json=payload_json,
            payload_sha256=payload_sha256,
            max_attempts=max_attempts,
            retry_deadline_at=deadline,
            endpoint_config_revision=revision,
            payload_contract_fingerprint=contract,
        ):
            raise OutboundConflictError("同一 idempotency_key 的不可变事实不一致")
        _assert_terminal_generation_settlement(
            db,
            run_id=run_id,
            generation_attempt_id=generation_attempt_id,
            owner=normalized_owner,
            claim_token=token,
            outbox=existing,
            payload_sha256=payload_sha256,
            model_trace_id=trace_id,
            generation_error_type=fallback_error_type,
            generation_error_summary=fallback_error_summary,
        )
        return OutboxCommitResult(
            outbox_id=int(existing.id),
            run_id=int(existing.run_id),
            created=False,
            payload_sha256=str(existing.payload_sha256),
            status=str(existing.status),
            reason_type="",
        )

    run = _require_generation_run(
        db,
        run_id=run_id,
        owner=normalized_owner,
        claim_token=token,
        current=current,
        allowed_statuses={"generating"},
    )
    _require_writer(
        db,
        source_type=str(run.source_type),
        owner=str(run.writer_owner),
        token=str(run.writer_token),
        protocol_version=int(run.writer_protocol_version),
        current=current,
    )
    _assert_delivery_contract(
        run,
        destination_snapshot=destination_snapshot,
        destination_fingerprint=destination,
        target_type=normalized_target_type,
        endpoint_key=endpoint,
        payload_contract_fingerprint=contract,
    )
    attempt = db.get(OutboundGenerationAttempt, int(generation_attempt_id))
    if (
        attempt is None
        or int(attempt.run_id) != int(run.id)
        or attempt.status != "started"
        or attempt.owner != normalized_owner
        or attempt.fencing_token != token
    ):
        raise OutboundFencingError("generation attempt 已失效")
    try:
        _assert_run_control(db, run=run, current=current)
    except OutboundSafetyError as exc:
        _abandon_generated_result(
            db,
            run=run,
            attempt=attempt,
            owner=normalized_owner,
            claim_token=token,
            reason_type="cutover_changed",
            reason_summary=str(exc),
            current=current,
        )
        return OutboxCommitResult(
            outbox_id=None,
            run_id=int(run.id),
            created=False,
            payload_sha256=payload_sha256,
            status="blocked",
            reason_type="cutover_changed",
        )
    open_circuit = _open_circuit_row(db, _circuit_facts(
        endpoint_key=endpoint,
        destination_fingerprint=destination,
        payload_contract_fingerprint=contract,
        config_revision=revision,
    ))
    if open_circuit is not None:
        _abandon_generated_result(
            db,
            run=run,
            attempt=attempt,
            owner=normalized_owner,
            claim_token=token,
            reason_type="circuit_open",
            reason_summary=f"{open_circuit.scope_type} circuit 已打开",
            current=current,
        )
        return OutboxCommitResult(
            outbox_id=None,
            run_id=int(run.id),
            created=False,
            payload_sha256=payload_sha256,
            status="blocked",
            reason_type="circuit_open",
        )

    insert_result = db.execute(
        sqlite_insert(OutboundDeliveryOutbox)
        .values(
            run_id=int(run.id),
            idempotency_key=key,
            destination_snapshot_json=destination_json,
            destination_fingerprint=destination,
            target_type=normalized_target_type,
            endpoint_key=endpoint,
            payload_json=payload_json,
            payload_sha256=payload_sha256,
            status="pending",
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            next_attempt_at=None,
            allocated_attempt_count=0,
            request_started_count=0,
            max_attempts=max_attempts,
            retry_deadline_at=deadline,
            last_error_type="",
            last_error_summary="",
            delivered_at=None,
            cancelled_at=None,
            cancel_reason_type=None,
            replay_of_outbox_id=None,
            replay_sequence=0,
            replay_request_sha256="",
            cutover_epoch=int(run.cutover_epoch),
            endpoint_config_revision=revision,
            payload_contract_fingerprint=contract,
            created_at=current,
            updated_at=current,
        )
        .on_conflict_do_nothing()
    )
    created = insert_result.rowcount == 1
    outbox = (
        db.query(OutboundDeliveryOutbox)
        .filter(
            or_(
                OutboundDeliveryOutbox.idempotency_key == key,
                (
                    (OutboundDeliveryOutbox.run_id == int(run.id))
                    & (
                        OutboundDeliveryOutbox.destination_fingerprint
                        == destination
                    )
                    & (OutboundDeliveryOutbox.replay_sequence == 0)
                ),
            )
        )
        .order_by(OutboundDeliveryOutbox.id.asc())
        .first()
    )
    if outbox is None or not _same_outbox_facts(
        outbox,
        run_id=run_id,
        idempotency_key=key,
        destination_snapshot_json=destination_json,
        destination_fingerprint=destination,
        target_type=normalized_target_type,
        endpoint_key=endpoint,
        payload_json=payload_json,
        payload_sha256=payload_sha256,
        max_attempts=max_attempts,
        retry_deadline_at=deadline,
        endpoint_config_revision=revision,
        payload_contract_fingerprint=contract,
    ):
        raise OutboundConflictError("outbox 唯一叶已存在不同事实")
    if not created:
        _assert_terminal_generation_settlement(
            db,
            run_id=run_id,
            generation_attempt_id=generation_attempt_id,
            owner=normalized_owner,
            claim_token=token,
            outbox=outbox,
            payload_sha256=payload_sha256,
            model_trace_id=trace_id,
            generation_error_type=fallback_error_type,
            generation_error_summary=fallback_error_summary,
        )
        return OutboxCommitResult(
            outbox_id=int(outbox.id),
            run_id=int(outbox.run_id),
            created=False,
            payload_sha256=str(outbox.payload_sha256),
            status=str(outbox.status),
            reason_type="",
        )

    attempt_values = (
        {
            OutboundGenerationAttempt.status: "failed",
            OutboundGenerationAttempt.completed_at: current,
            OutboundGenerationAttempt.model_trace_id: trace_id,
            OutboundGenerationAttempt.content_sha256: "",
            OutboundGenerationAttempt.error_type: fallback_error_type,
            OutboundGenerationAttempt.error_summary: fallback_error_summary,
        }
        if fallback_error_type
        else {
            OutboundGenerationAttempt.status: "succeeded",
            OutboundGenerationAttempt.completed_at: current,
            OutboundGenerationAttempt.model_trace_id: trace_id,
            OutboundGenerationAttempt.content_sha256: payload_sha256,
            OutboundGenerationAttempt.error_type: "",
            OutboundGenerationAttempt.error_summary: "",
        }
    )
    attempt_updated = (
        db.query(OutboundGenerationAttempt)
        .filter(
            OutboundGenerationAttempt.id == int(attempt.id),
            OutboundGenerationAttempt.run_id == int(run.id),
            OutboundGenerationAttempt.status == "started",
            OutboundGenerationAttempt.owner == normalized_owner,
            OutboundGenerationAttempt.fencing_token == token,
        )
        .update(attempt_values, synchronize_session=False)
    )
    run_updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run.id),
            OutboundRun.status == "generating",
            OutboundRun.claim_owner == normalized_owner,
            OutboundRun.claim_token == token,
            OutboundRun.claim_expires_at > current,
            OutboundRun.active_outbox_id.is_(None),
        )
        .update(
            {
                OutboundRun.status: "queued",
                OutboundRun.claim_owner: None,
                OutboundRun.claim_token: None,
                OutboundRun.claim_expires_at: None,
                OutboundRun.generated_at: current,
                OutboundRun.active_outbox_id: int(outbox.id),
                OutboundRun.failure_type: "",
                OutboundRun.failure_summary: "",
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if attempt_updated != 1 or run_updated != 1:
        raise OutboundFencingError("生成提交 CAS 失败")
    _project_outbound_source(
        db,
        run=run,
        status="queued",
        current=current,
    )
    db.flush()
    result = OutboxCommitResult(
        outbox_id=int(outbox.id),
        run_id=int(run.id),
        created=True,
        payload_sha256=payload_sha256,
        status="pending",
        reason_type="",
    )
    db.expire_all()
    return result


def fail_outbound_generation(
    db: Session,
    *,
    run_id: int,
    generation_attempt_id: int,
    owner: str,
    claim_token: str,
    error_type: str,
    error_summary: Any,
    now: datetime | None = None,
) -> bool:
    normalized_owner = _text(owner, name="owner", max_length=128)
    token = _text(claim_token, name="claim_token", max_length=64)
    normalized_error_type = _text(
        error_type,
        name="error_type",
        max_length=64,
    )
    normalized_summary = _summary(error_summary)
    run_probe = db.get(OutboundRun, int(run_id))
    if run_probe is None:
        raise OutboundFencingError("generation claim 已失效")
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    run = _require_generation_run(
        db,
        run_id=run_id,
        owner=normalized_owner,
        claim_token=token,
        current=current,
        allowed_statuses={"generating"},
    )
    _require_writer(
        db,
        source_type=str(run.source_type),
        owner=str(run.writer_owner),
        token=str(run.writer_token),
        protocol_version=int(run.writer_protocol_version),
        current=current,
    )
    attempt = db.get(OutboundGenerationAttempt, int(generation_attempt_id))
    if (
        attempt is None
        or int(attempt.run_id) != int(run.id)
        or attempt.status != "started"
        or attempt.owner != normalized_owner
        or attempt.fencing_token != token
    ):
        raise OutboundFencingError("generation attempt 已失效")
    attempt_updated = (
        db.query(OutboundGenerationAttempt)
        .filter(
            OutboundGenerationAttempt.id == int(attempt.id),
            OutboundGenerationAttempt.run_id == int(run.id),
            OutboundGenerationAttempt.status == "started",
            OutboundGenerationAttempt.owner == normalized_owner,
            OutboundGenerationAttempt.fencing_token == token,
        )
        .update(
            {
                OutboundGenerationAttempt.status: "failed",
                OutboundGenerationAttempt.completed_at: current,
                OutboundGenerationAttempt.error_type: normalized_error_type,
                OutboundGenerationAttempt.error_summary: normalized_summary,
            },
            synchronize_session=False,
        )
    )
    run_updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run.id),
            OutboundRun.status == "generating",
            OutboundRun.claim_owner == normalized_owner,
            OutboundRun.claim_token == token,
            OutboundRun.claim_expires_at > current,
            OutboundRun.active_outbox_id.is_(None),
            OutboundRun.writer_owner == str(run.writer_owner),
            OutboundRun.writer_token == str(run.writer_token),
            OutboundRun.writer_protocol_version
            == int(run.writer_protocol_version),
        )
        .update(
            {
                OutboundRun.status: "failed",
                OutboundRun.claim_owner: None,
                OutboundRun.claim_token: None,
                OutboundRun.claim_expires_at: None,
                OutboundRun.failure_type: normalized_error_type,
                OutboundRun.failure_summary: normalized_summary,
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if attempt_updated != 1 or run_updated != 1:
        raise OutboundFencingError("生成失败结算 CAS 失败")
    _project_outbound_source(
        db,
        run=run,
        status="failed",
        current=current,
        error_summary=normalized_summary,
    )
    db.flush()
    return True


def _outbox_circuit_facts(
    outbox: OutboundDeliveryOutbox,
    *,
    actual_config_revision: str,
) -> tuple[tuple[str, str, str], ...]:
    return _circuit_facts(
        endpoint_key=str(outbox.endpoint_key),
        destination_fingerprint=str(outbox.destination_fingerprint),
        payload_contract_fingerprint=str(outbox.payload_contract_fingerprint),
        config_revision=actual_config_revision,
    )


def _worker_control_allows(
    db: Session,
    *,
    run: OutboundRun,
    outbox: OutboundDeliveryOutbox,
) -> bool:
    control = _control(db, str(run.source_type))
    return (
        run.delivery_mode == "outbox"
        and control.mode in {"outbox_active", "outbox_draining"}
        and int(control.cutover_epoch) == int(run.cutover_epoch)
        and int(outbox.cutover_epoch) == int(run.cutover_epoch)
    )


def _live_delivery_control_allows(
    db: Session,
    *,
    run: OutboundRun,
    outbox: OutboundDeliveryOutbox,
    current: datetime,
) -> bool:
    if _worker_control_allows(db, run=run, outbox=outbox):
        return True
    if run.delivery_mode != "legacy_direct":
        return False
    try:
        mode, epoch = _mode_for_occurrence(
            _control(db, str(run.source_type)),
            occurrence_at=run.scheduled_for or current,
        )
    except OutboundSafetyError:
        return False
    return (
        mode == "legacy_direct"
        and epoch == int(run.cutover_epoch)
        and int(outbox.cutover_epoch) == int(run.cutover_epoch)
    )


def claim_legacy_direct_outbox(
    db: Session,
    *,
    outbox_id: int,
    worker_owner: str,
    lease_seconds: int | float,
    writer_owner: str,
    writer_token: str,
    writer_protocol_version: int,
    writer_lease_seconds: int | float,
    endpoint_key: str,
    endpoint_config_revision: str,
    expected_writer_version: int | None = None,
    now: datetime | None = None,
) -> DeliveryClaimHandle | None:
    """由 source-specific 兼容入口领取指定 legacy leaf。"""

    owner = _text(worker_owner, name="worker_owner", max_length=128)
    seconds = _positive_seconds(lease_seconds, name="lease_seconds")
    normalized_writer_owner = _text(
        writer_owner,
        name="writer_owner",
        max_length=128,
    )
    normalized_writer_token = _text(
        writer_token,
        name="writer_token",
        max_length=64,
    )
    normalized_endpoint = _text(
        endpoint_key,
        name="endpoint_key",
        max_length=64,
    )
    normalized_writer_lease_seconds = _positive_seconds(
        writer_lease_seconds,
        name="writer_lease_seconds",
    )
    revision = _text(
        endpoint_config_revision,
        name="endpoint_config_revision",
        max_length=128,
    )
    if (
        expected_writer_version is not None
        and (
            type(expected_writer_version) is not int
            or expected_writer_version < 0
        )
    ):
        raise ValueError("expected_writer_version 必须是非负整数")
    outbox_probe = db.get(OutboundDeliveryOutbox, int(outbox_id))
    run_probe = (
        db.get(OutboundRun, int(outbox_probe.run_id))
        if outbox_probe is not None
        else None
    )
    if outbox_probe is None or run_probe is None:
        raise OutboundFencingError("legacy direct leaf 不存在")
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    outbox = db.get(OutboundDeliveryOutbox, int(outbox_id))
    run = db.get(OutboundRun, int(run_probe.id))
    if (
        outbox is None
        or run is None
        or run.delivery_mode != "legacy_direct"
        or run.active_outbox_id != outbox.id
        or outbox.endpoint_key != normalized_endpoint
        or outbox.status not in _DUE_OUTBOX_STATUSES
        or int(outbox.request_started_count) >= int(outbox.max_attempts)
        or outbox.retry_deadline_at <= current
        or (
            outbox.status == "retry_wait"
            and (
                outbox.next_attempt_at is None
                or outbox.next_attempt_at > current
            )
        )
    ):
        return None
    if expected_writer_version is None:
        writer = acquire_or_renew_delivery_writer(
            db,
            source_type=str(run.source_type),
            owner=normalized_writer_owner,
            token=normalized_writer_token,
            protocol_version=writer_protocol_version,
            lease_seconds=normalized_writer_lease_seconds,
            now=current,
        )
        if not writer.acquired:
            return None
    else:
        try:
            writer_control = _require_writer(
                db,
                source_type=str(run.source_type),
                owner=normalized_writer_owner,
                token=normalized_writer_token,
                protocol_version=writer_protocol_version,
                current=current,
            )
        except OutboundFencingError:
            return None
        if int(writer_control.writer_version) != expected_writer_version:
            return None
    outbox = db.get(OutboundDeliveryOutbox, int(outbox_id))
    run = db.get(OutboundRun, int(run_probe.id))
    if (
        outbox is None
        or run is None
        or run.delivery_mode != "legacy_direct"
        or run.active_outbox_id != outbox.id
        or outbox.endpoint_key != normalized_endpoint
        or outbox.status not in _DUE_OUTBOX_STATUSES
        or int(outbox.request_started_count) >= int(outbox.max_attempts)
        or outbox.retry_deadline_at <= current
        or (
            outbox.status == "retry_wait"
            and (
                outbox.next_attempt_at is None
                or outbox.next_attempt_at > current
            )
        )
    ):
        return None
    if (
        run.writer_owner != normalized_writer_owner
        or run.writer_token != normalized_writer_token
        or int(run.writer_protocol_version) != int(writer_protocol_version)
    ):
        rebound = (
            db.query(OutboundRun)
            .filter(
                OutboundRun.id == int(run.id),
                OutboundRun.active_outbox_id == int(outbox.id),
                OutboundRun.delivery_mode == "legacy_direct",
                OutboundRun.writer_owner == run.writer_owner,
                OutboundRun.writer_token == run.writer_token,
                OutboundRun.writer_protocol_version
                == int(run.writer_protocol_version),
                OutboundRun.status.in_(("queued", "blocked")),
            )
            .update(
                {
                    OutboundRun.writer_owner: normalized_writer_owner,
                    OutboundRun.writer_token: normalized_writer_token,
                    OutboundRun.writer_protocol_version: writer_protocol_version,
                    OutboundRun.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if rebound != 1:
            db.expire_all()
            return None
        db.flush()
        db.expire_all()
        outbox = db.get(OutboundDeliveryOutbox, int(outbox_id))
        run = db.get(OutboundRun, int(run_probe.id))
        if outbox is None or run is None:
            return None
    _require_writer(
        db,
        source_type=str(run.source_type),
        owner=normalized_writer_owner,
        token=normalized_writer_token,
        protocol_version=writer_protocol_version,
        current=current,
    )
    if not _live_delivery_control_allows(
        db,
        run=run,
        outbox=outbox,
        current=current,
    ):
        raise OutboundSafetyError("legacy direct leaf 与当前 control 不一致")
    open_circuit = _open_circuit_row(
        db,
        _outbox_circuit_facts(outbox, actual_config_revision=revision),
    )
    if open_circuit is not None:
        if outbox.status != "blocked":
            outbox.status = "blocked"
            outbox.next_attempt_at = None
            outbox.last_error_type = "circuit_open"
            outbox.last_error_summary = (
                f"{open_circuit.scope_type} circuit 已打开"
            )
            outbox.updated_at = current
            run.status = "blocked"
            run.failure_type = "circuit_open"
            run.failure_summary = outbox.last_error_summary
            run.updated_at = current
            _project_outbound_source(
                db,
                run=run,
                status="blocked",
                current=current,
                error_summary=outbox.last_error_summary,
            )
            db.flush()
        return None

    previous_status = str(outbox.status)
    attempt_no = int(outbox.allocated_attempt_count) + 1
    lease_token = secrets.token_hex(32)
    lease_expires_at = current + timedelta(seconds=seconds)
    updated = (
        db.query(OutboundDeliveryOutbox)
        .filter(
            OutboundDeliveryOutbox.id == int(outbox.id),
            OutboundDeliveryOutbox.status == previous_status,
            OutboundDeliveryOutbox.request_started_count
            < OutboundDeliveryOutbox.max_attempts,
            OutboundDeliveryOutbox.retry_deadline_at > current,
        )
        .update(
            {
                OutboundDeliveryOutbox.status: "leased",
                OutboundDeliveryOutbox.lease_owner: owner,
                OutboundDeliveryOutbox.lease_token: lease_token,
                OutboundDeliveryOutbox.lease_expires_at: lease_expires_at,
                OutboundDeliveryOutbox.next_attempt_at: None,
                OutboundDeliveryOutbox.allocated_attempt_count: attempt_no,
                OutboundDeliveryOutbox.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        db.expire_all()
        return None
    attempt = OutboundDeliveryAttempt(
        outbox_id=int(outbox.id),
        attempt_no=attempt_no,
        worker_owner=owner,
        lease_token=lease_token,
        status="started",
        transport_phase="allocated",
        request_started=False,
        endpoint_config_revision=revision,
        http_status=None,
        result_category="",
        error_type="",
        safe_summary="",
        duration_ms=None,
        settlement_retry_at=None,
        settlement_circuit_scope_type=None,
        settlement_request_sha256="",
        started_at=current,
        request_started_at=None,
        completed_at=None,
        created_at=current,
    )
    db.add(attempt)
    run_updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run.id),
            OutboundRun.active_outbox_id == int(outbox.id),
            OutboundRun.status.in_(("queued", "blocked")),
            OutboundRun.delivery_mode == "legacy_direct",
        )
        .update(
            {
                OutboundRun.status: "delivering",
                OutboundRun.failure_type: "",
                OutboundRun.failure_summary: "",
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if run_updated != 1:
        raise OutboundFencingError("legacy direct active leaf 已变化")
    db.flush()
    _project_outbound_source(
        db,
        run=run,
        status="delivering",
        current=current,
    )
    db.flush()
    result = DeliveryClaimHandle(
        outbox_id=int(outbox.id),
        run_id=int(run.id),
        attempt_id=int(attempt.id),
        attempt_no=attempt_no,
        worker_owner=owner,
        lease_token=lease_token,
        lease_expires_at=lease_expires_at,
        endpoint_key=str(outbox.endpoint_key),
        target_type=str(outbox.target_type),
        endpoint_config_revision=revision,
        destination_snapshot_json=str(outbox.destination_snapshot_json),
        payload_json=str(outbox.payload_json),
        payload_sha256=str(outbox.payload_sha256),
        payload_contract_fingerprint=str(outbox.payload_contract_fingerprint),
    )
    db.expire_all()
    return result


def _terminalize_expired_outbox_leaf(
    db: Session,
    *,
    outbox_id: int,
    endpoint_key: str,
    now: datetime | None,
) -> bool:
    outbox_probe = db.get(OutboundDeliveryOutbox, outbox_id)
    if outbox_probe is None:
        return False
    run_probe = db.get(OutboundRun, int(outbox_probe.run_id))
    if run_probe is None:
        return False
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    outbox = db.get(OutboundDeliveryOutbox, outbox_id)
    if (
        outbox is None
        or outbox.endpoint_key != endpoint_key
        or outbox.status not in {"pending", "retry_wait", "blocked"}
        or outbox.retry_deadline_at > current
    ):
        return False
    previous_status = str(outbox.status)
    updated = (
        db.query(OutboundDeliveryOutbox)
        .filter(
            OutboundDeliveryOutbox.id == outbox_id,
            OutboundDeliveryOutbox.endpoint_key == endpoint_key,
            OutboundDeliveryOutbox.status == previous_status,
            OutboundDeliveryOutbox.retry_deadline_at <= current,
        )
        .update(
            {
                OutboundDeliveryOutbox.status: "failed",
                OutboundDeliveryOutbox.next_attempt_at: None,
                OutboundDeliveryOutbox.last_error_type: "retry_exhausted",
                OutboundDeliveryOutbox.last_error_summary: (
                    "投递重试期限已到期"
                ),
                OutboundDeliveryOutbox.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        return False
    run = db.get(OutboundRun, int(outbox.run_id))
    if run is not None and run.active_outbox_id == outbox.id:
        run_updated = (
            db.query(OutboundRun)
            .filter(
                OutboundRun.id == int(run.id),
                OutboundRun.active_outbox_id == int(outbox.id),
                OutboundRun.status.in_(("queued", "blocked")),
            )
            .update(
                {
                    OutboundRun.status: "failed",
                    OutboundRun.failure_type: "retry_exhausted",
                    OutboundRun.failure_summary: "投递重试期限已到期",
                    OutboundRun.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if run_updated != 1:
            raise OutboundFencingError("过期 outbox 的 run CAS 失败")
        try:
            with db.begin_nested():
                _project_outbound_source(
                    db,
                    run=run,
                    status="failed",
                    current=current,
                    error_summary="投递重试期限已到期",
                )
        except (OutboundFencingError, OutboundConflictError):
            # 来源 revision 已变化时保留新来源，只终结冻结的投递账本。
            db.expire_all()
    db.flush()
    return True


def terminalize_expired_outboxes(
    db: Session,
    *,
    endpoint_key: str,
    source_type: str | None = None,
    delivery_mode: str | None = None,
    now: datetime | None,
) -> int:
    """将超过重试期限的安全 leaf 收敛为失败；调用方负责提交。"""

    endpoint = _text(endpoint_key, name="endpoint_key", max_length=64)
    source = (
        _text(source_type, name="source_type", max_length=32)
        if source_type is not None
        else None
    )
    mode = (
        _text(delivery_mode, name="delivery_mode", max_length=24)
        if delivery_mode is not None
        else None
    )
    if mode is not None and mode not in {"legacy_direct", "outbox"}:
        raise ValueError("delivery_mode 只支持 legacy_direct/outbox")
    observed_at = _utc_naive(now)
    candidate_query = (
        db.query(OutboundDeliveryOutbox)
        .join(OutboundRun, OutboundRun.id == OutboundDeliveryOutbox.run_id)
        .filter(
            OutboundDeliveryOutbox.endpoint_key == endpoint,
            OutboundDeliveryOutbox.status.in_((
                "pending",
                "retry_wait",
                "blocked",
            )),
            OutboundDeliveryOutbox.retry_deadline_at <= observed_at,
        )
    )
    if source is not None:
        candidate_query = candidate_query.filter(
            OutboundRun.source_type == source
        )
    if mode is not None:
        candidate_query = candidate_query.filter(
            OutboundRun.delivery_mode == mode
        )
    candidates = (
        candidate_query.order_by(OutboundDeliveryOutbox.id.asc())
        .limit(100)
        .all()
    )
    terminalized = 0
    for probe in candidates:
        try:
            with db.begin_nested():
                applied = _terminalize_expired_outbox_leaf(
                    db,
                    outbox_id=int(probe.id),
                    endpoint_key=endpoint,
                    now=now,
                )
        except (OutboundFencingError, OutboundConflictError):
            db.expire_all()
            continue
        if applied:
            terminalized += 1
    db.flush()
    return terminalized


def claim_due_outbox(
    db: Session,
    *,
    worker_owner: str,
    lease_seconds: int | float,
    endpoint_config_revision: str,
    endpoint_key: str = "qq_push",
    now: datetime | None = None,
) -> DeliveryClaimHandle | None:
    owner = _text(worker_owner, name="worker_owner", max_length=128)
    seconds = _positive_seconds(lease_seconds, name="lease_seconds")
    revision = _text(
        endpoint_config_revision,
        name="endpoint_config_revision",
        max_length=128,
    )
    endpoint = _text(endpoint_key, name="endpoint_key", max_length=64)
    terminalize_expired_outboxes(db, endpoint_key=endpoint, now=now)
    observed_at = _utc_naive(now)
    candidates = (
        db.query(OutboundDeliveryOutbox)
        .filter(
            OutboundDeliveryOutbox.endpoint_key == endpoint,
            OutboundDeliveryOutbox.status.in_(tuple(_DUE_OUTBOX_STATUSES)),
            OutboundDeliveryOutbox.request_started_count
            < OutboundDeliveryOutbox.max_attempts,
            OutboundDeliveryOutbox.retry_deadline_at > observed_at,
        )
        .filter(
            or_(
                OutboundDeliveryOutbox.status.in_(("pending", "blocked")),
                (
                    (OutboundDeliveryOutbox.status == "retry_wait")
                    & (OutboundDeliveryOutbox.next_attempt_at <= observed_at)
                ),
            )
        )
        .order_by(
            OutboundDeliveryOutbox.next_attempt_at.asc(),
            OutboundDeliveryOutbox.id.asc(),
        )
        .all()
    )
    for candidate in candidates:
        candidate_id = int(candidate.id)
        run_probe = db.get(OutboundRun, int(candidate.run_id))
        if run_probe is None:
            continue
        current = _locked_current(
            db,
            source_type=str(run_probe.source_type),
            now=now,
        )
        lease_expires_at = current + timedelta(seconds=seconds)
        candidate = db.get(OutboundDeliveryOutbox, candidate_id)
        if candidate is None:
            continue
        run = db.get(OutboundRun, int(candidate.run_id))
        if (
            run is None
            or run.active_outbox_id != candidate.id
            or candidate.endpoint_key != endpoint
            or not _worker_control_allows(db, run=run, outbox=candidate)
            or candidate.status not in _DUE_OUTBOX_STATUSES
            or int(candidate.request_started_count) >= int(candidate.max_attempts)
            or candidate.retry_deadline_at <= current
            or (
                candidate.status == "retry_wait"
                and (
                    candidate.next_attempt_at is None
                    or candidate.next_attempt_at > current
                )
            )
        ):
            continue
        open_circuit = _open_circuit_row(
            db,
            _outbox_circuit_facts(
                candidate,
                actual_config_revision=revision,
            ),
        )
        previous_status = str(candidate.status)
        if open_circuit is not None:
            if previous_status != "blocked":
                (
                    db.query(OutboundDeliveryOutbox)
                    .filter(
                        OutboundDeliveryOutbox.id == candidate.id,
                        OutboundDeliveryOutbox.status == previous_status,
                    )
                    .update(
                        {
                            OutboundDeliveryOutbox.status: "blocked",
                            OutboundDeliveryOutbox.next_attempt_at: None,
                            OutboundDeliveryOutbox.last_error_type: "circuit_open",
                            OutboundDeliveryOutbox.last_error_summary: (
                                f"{open_circuit.scope_type} circuit 已打开"
                            ),
                            OutboundDeliveryOutbox.updated_at: current,
                        },
                        synchronize_session=False,
                    )
                )
                (
                    db.query(OutboundRun)
                    .filter(
                        OutboundRun.id == run.id,
                        OutboundRun.active_outbox_id == candidate.id,
                    )
                    .update(
                        {
                            OutboundRun.status: "blocked",
                            OutboundRun.failure_type: "circuit_open",
                            OutboundRun.failure_summary: (
                                f"{open_circuit.scope_type} circuit 已打开"
                            ),
                            OutboundRun.updated_at: current,
                        },
                        synchronize_session=False,
                    )
                )
                _project_outbound_source(
                    db,
                    run=run,
                    status="blocked",
                    current=current,
                    error_summary=f"{open_circuit.scope_type} circuit 已打开",
                )
            continue

        attempt_no = int(candidate.allocated_attempt_count) + 1
        lease_token = secrets.token_hex(32)
        updated = (
            db.query(OutboundDeliveryOutbox)
            .filter(
                OutboundDeliveryOutbox.id == int(candidate.id),
                OutboundDeliveryOutbox.status == previous_status,
                OutboundDeliveryOutbox.request_started_count
                < OutboundDeliveryOutbox.max_attempts,
                OutboundDeliveryOutbox.retry_deadline_at > current,
                or_(
                    OutboundDeliveryOutbox.status.in_(("pending", "blocked")),
                    (
                        (OutboundDeliveryOutbox.status == "retry_wait")
                        & (OutboundDeliveryOutbox.next_attempt_at <= current)
                    ),
                ),
            )
            .update(
                {
                    OutboundDeliveryOutbox.status: "leased",
                    OutboundDeliveryOutbox.lease_owner: owner,
                    OutboundDeliveryOutbox.lease_token: lease_token,
                    OutboundDeliveryOutbox.lease_expires_at: lease_expires_at,
                    OutboundDeliveryOutbox.next_attempt_at: None,
                    OutboundDeliveryOutbox.allocated_attempt_count: attempt_no,
                    OutboundDeliveryOutbox.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            db.expire_all()
            continue
        attempt = OutboundDeliveryAttempt(
            outbox_id=int(candidate.id),
            attempt_no=attempt_no,
            worker_owner=owner,
            lease_token=lease_token,
            status="started",
            transport_phase="allocated",
            request_started=False,
            endpoint_config_revision=revision,
            http_status=None,
            result_category="",
            error_type="",
            safe_summary="",
            duration_ms=None,
            settlement_retry_at=None,
            settlement_circuit_scope_type=None,
            settlement_request_sha256="",
            started_at=current,
            request_started_at=None,
            completed_at=None,
            created_at=current,
        )
        db.add(attempt)
        run_updated = (
            db.query(OutboundRun)
            .filter(
                OutboundRun.id == int(run.id),
                OutboundRun.active_outbox_id == int(candidate.id),
                OutboundRun.status.in_(("queued", "delivering", "blocked")),
            )
            .update(
                {
                    OutboundRun.status: "delivering",
                    OutboundRun.failure_type: "",
                    OutboundRun.failure_summary: "",
                    OutboundRun.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if run_updated != 1:
            raise OutboundFencingError("active outbox leaf 已变化")
        _project_outbound_source(
            db,
            run=run,
            status="delivering",
            current=current,
        )
        db.flush()
        result = DeliveryClaimHandle(
            outbox_id=int(candidate.id),
            run_id=int(run.id),
            attempt_id=int(attempt.id),
            attempt_no=attempt_no,
            worker_owner=owner,
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
            endpoint_key=str(candidate.endpoint_key),
            target_type=str(candidate.target_type),
            endpoint_config_revision=revision,
            destination_snapshot_json=str(candidate.destination_snapshot_json),
            payload_json=str(candidate.payload_json),
            payload_sha256=str(candidate.payload_sha256),
            payload_contract_fingerprint=str(
                candidate.payload_contract_fingerprint
            ),
        )
        db.expire_all()
        return result
    db.flush()
    return None


def _require_live_delivery(
    db: Session,
    *,
    outbox_id: int,
    attempt_id: int,
    worker_owner: str,
    lease_token: str,
    current: datetime,
) -> tuple[OutboundDeliveryOutbox, OutboundDeliveryAttempt, OutboundRun]:
    outbox = db.get(OutboundDeliveryOutbox, int(outbox_id))
    attempt = db.get(OutboundDeliveryAttempt, int(attempt_id))
    if outbox is None or attempt is None:
        raise OutboundFencingError("delivery attempt 不存在")
    run = db.get(OutboundRun, int(outbox.run_id))
    if (
        run is None
        or run.active_outbox_id != outbox.id
        or outbox.status != "leased"
        or outbox.lease_owner != worker_owner
        or outbox.lease_token != lease_token
        or outbox.lease_expires_at is None
        or outbox.lease_expires_at <= current
        or int(attempt.outbox_id) != int(outbox.id)
        or attempt.worker_owner != worker_owner
        or attempt.lease_token != lease_token
        or attempt.status != "started"
        or int(attempt.attempt_no) != int(outbox.allocated_attempt_count)
        or not _live_delivery_control_allows(
            db,
            run=run,
            outbox=outbox,
            current=current,
        )
    ):
        raise OutboundFencingError("delivery lease 或 active leaf 已失效")
    return outbox, attempt, run


def mark_delivery_request_started(
    db: Session,
    *,
    outbox_id: int,
    attempt_id: int,
    worker_owner: str,
    lease_token: str,
    now: datetime | None = None,
) -> RequestStartResult:
    owner = _text(worker_owner, name="worker_owner", max_length=128)
    token = _text(lease_token, name="lease_token", max_length=64)
    outbox_probe = db.get(OutboundDeliveryOutbox, int(outbox_id))
    run_probe = (
        db.get(OutboundRun, int(outbox_probe.run_id))
        if outbox_probe is not None
        else None
    )
    if outbox_probe is None or run_probe is None:
        raise OutboundFencingError("delivery attempt 不存在")
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    outbox = db.get(OutboundDeliveryOutbox, int(outbox_id))
    attempt = db.get(OutboundDeliveryAttempt, int(attempt_id))
    if (
        outbox is not None
        and attempt is not None
        and outbox.status == "leased"
        and outbox.lease_owner == owner
        and outbox.lease_token == token
        and outbox.lease_expires_at is not None
        and outbox.lease_expires_at > current
        and attempt.status == "started"
        and attempt.worker_owner == owner
        and attempt.lease_token == token
        and bool(attempt.request_started)
    ):
        return RequestStartResult(
            applied=False,
            outbox_id=int(outbox.id),
            attempt_id=int(attempt.id),
            request_started_count=int(outbox.request_started_count),
        )
    outbox, attempt, _run = _require_live_delivery(
        db,
        outbox_id=outbox_id,
        attempt_id=attempt_id,
        worker_owner=owner,
        lease_token=token,
        current=current,
    )
    open_circuit = _open_circuit_row(
        db,
        _outbox_circuit_facts(
            outbox,
            actual_config_revision=str(attempt.endpoint_config_revision),
        ),
    )
    if open_circuit is not None:
        raise OutboundSafetyError(
            f"{open_circuit.scope_type} circuit 已打开，禁止越过 request boundary"
        )
    if (
        bool(attempt.request_started)
        or attempt.transport_phase != "allocated"
        or int(outbox.request_started_count) >= int(outbox.max_attempts)
        or outbox.retry_deadline_at <= current
    ):
        raise OutboundSafetyError("网络请求预算或 request boundary 状态无效")
    previous_count = int(outbox.request_started_count)
    attempt_updated = (
        db.query(OutboundDeliveryAttempt)
        .filter(
            OutboundDeliveryAttempt.id == int(attempt.id),
            OutboundDeliveryAttempt.status == "started",
            OutboundDeliveryAttempt.worker_owner == owner,
            OutboundDeliveryAttempt.lease_token == token,
            OutboundDeliveryAttempt.request_started.is_(False),
            OutboundDeliveryAttempt.transport_phase == "allocated",
        )
        .update(
            {
                OutboundDeliveryAttempt.request_started: True,
                OutboundDeliveryAttempt.transport_phase: "request_started",
                OutboundDeliveryAttempt.request_started_at: current,
            },
            synchronize_session=False,
        )
    )
    outbox_updated = (
        db.query(OutboundDeliveryOutbox)
        .filter(
            OutboundDeliveryOutbox.id == int(outbox.id),
            OutboundDeliveryOutbox.status == "leased",
            OutboundDeliveryOutbox.lease_owner == owner,
            OutboundDeliveryOutbox.lease_token == token,
            OutboundDeliveryOutbox.lease_expires_at > current,
            OutboundDeliveryOutbox.request_started_count == previous_count,
            OutboundDeliveryOutbox.request_started_count
            < OutboundDeliveryOutbox.max_attempts,
            OutboundDeliveryOutbox.retry_deadline_at > current,
        )
        .update(
            {
                OutboundDeliveryOutbox.request_started_count: previous_count + 1,
                OutboundDeliveryOutbox.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if attempt_updated != 1 or outbox_updated != 1:
        raise OutboundFencingError("request boundary CAS 失败")
    db.flush()
    result = RequestStartResult(
        applied=True,
        outbox_id=int(outbox.id),
        attempt_id=int(attempt.id),
        request_started_count=previous_count + 1,
    )
    db.expire_all()
    return result


def cancel_delivery_before_send(
    db: Session,
    *,
    outbox_id: int,
    attempt_id: int,
    worker_owner: str,
    lease_token: str,
    reason_type: str,
    safe_summary: Any,
    now: datetime | None = None,
    _project_source: bool = True,
) -> DeliverySettlementResult:
    owner = _text(worker_owner, name="worker_owner", max_length=128)
    token = _text(lease_token, name="lease_token", max_length=64)
    reason = _text(reason_type, name="reason_type", max_length=64)
    summary = _summary(safe_summary)
    outbox_probe = db.get(OutboundDeliveryOutbox, int(outbox_id))
    run_probe = (
        db.get(OutboundRun, int(outbox_probe.run_id))
        if outbox_probe is not None
        else None
    )
    if outbox_probe is None or run_probe is None:
        raise OutboundFencingError("delivery attempt 不存在")
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    outbox, attempt, run = _require_live_delivery(
        db,
        outbox_id=outbox_id,
        attempt_id=attempt_id,
        worker_owner=owner,
        lease_token=token,
        current=current,
    )
    if bool(attempt.request_started) or attempt.transport_phase != "allocated":
        raise OutboundSafetyError("越过 request boundary 后不能安全取消")

    attempt_updated = (
        db.query(OutboundDeliveryAttempt)
        .filter(
            OutboundDeliveryAttempt.id == int(attempt.id),
            OutboundDeliveryAttempt.outbox_id == int(outbox.id),
            OutboundDeliveryAttempt.attempt_no
            == int(outbox.allocated_attempt_count),
            OutboundDeliveryAttempt.status == "started",
            OutboundDeliveryAttempt.worker_owner == owner,
            OutboundDeliveryAttempt.lease_token == token,
            OutboundDeliveryAttempt.request_started.is_(False),
            OutboundDeliveryAttempt.transport_phase == "allocated",
        )
        .update(
            {
                OutboundDeliveryAttempt.status: "cancelled_before_send",
                OutboundDeliveryAttempt.result_category: "before_send",
                OutboundDeliveryAttempt.error_type: reason,
                OutboundDeliveryAttempt.safe_summary: summary,
                OutboundDeliveryAttempt.completed_at: current,
            },
            synchronize_session=False,
        )
    )
    outbox_updated = (
        db.query(OutboundDeliveryOutbox)
        .filter(
            OutboundDeliveryOutbox.id == int(outbox.id),
            OutboundDeliveryOutbox.status == "leased",
            OutboundDeliveryOutbox.lease_owner == owner,
            OutboundDeliveryOutbox.lease_token == token,
            OutboundDeliveryOutbox.lease_expires_at > current,
            OutboundDeliveryOutbox.request_started_count
            == int(outbox.request_started_count),
        )
        .update(
            {
                OutboundDeliveryOutbox.status: "cancelled",
                OutboundDeliveryOutbox.lease_owner: None,
                OutboundDeliveryOutbox.lease_token: None,
                OutboundDeliveryOutbox.lease_expires_at: None,
                OutboundDeliveryOutbox.next_attempt_at: None,
                OutboundDeliveryOutbox.last_error_type: reason,
                OutboundDeliveryOutbox.last_error_summary: summary,
                OutboundDeliveryOutbox.cancelled_at: current,
                OutboundDeliveryOutbox.cancel_reason_type: reason,
                OutboundDeliveryOutbox.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    run_updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run.id),
            OutboundRun.active_outbox_id == int(outbox.id),
            OutboundRun.status == "delivering",
        )
        .update(
            {
                OutboundRun.status: "failed",
                OutboundRun.failure_type: reason,
                OutboundRun.failure_summary: summary,
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if attempt_updated != 1 or outbox_updated != 1 or run_updated != 1:
        raise OutboundFencingError("发送前取消 CAS 失败")
    if _project_source:
        _project_outbound_source(
            db,
            run=run,
            status="cancelled",
            current=current,
            error_summary=summary,
        )
    db.flush()
    result = DeliverySettlementResult(
        applied=True,
        outbox_id=int(outbox.id),
        attempt_id=int(attempt.id),
        outbox_status="cancelled",
        run_status="failed",
    )
    db.expire_all()
    return result


def cancel_invalid_delivery_before_send(
    db: Session,
    *,
    outbox_id: int,
    attempt_id: int,
    worker_owner: str,
    lease_token: str,
    now: datetime | None = None,
) -> DeliverySettlementResult | None:
    """由当前 leaf owner 在 request boundary 前执行来源与清除点复检。"""

    owner = _text(worker_owner, name="worker_owner", max_length=128)
    token = _text(lease_token, name="lease_token", max_length=64)
    outbox_probe = db.get(OutboundDeliveryOutbox, int(outbox_id))
    run_probe = (
        db.get(OutboundRun, int(outbox_probe.run_id))
        if outbox_probe is not None
        else None
    )
    if outbox_probe is None or run_probe is None:
        raise OutboundFencingError("delivery attempt 不存在")
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    _outbox, _attempt, run = _require_live_delivery(
        db,
        outbox_id=outbox_id,
        attempt_id=attempt_id,
        worker_owner=owner,
        lease_token=token,
        current=current,
    )
    if str(run.source_type) != "proactive_outreach":
        return None

    project_source = True
    try:
        row, snapshot = _validated_proactive_source(db, run)
    except OutboundFencingError:
        reason_type = "source_fenced"
        safe_summary = "主动外呼来源 revision 已变化"
        project_source = False
    else:
        if row.outbound_run_id != int(run.id):
            reason_type = "source_fenced"
            safe_summary = "主动外呼来源 run 已变化"
            project_source = False
        else:
            clear_at = (
                db.query(User.history_clear_at)
                .filter(User.id == str(snapshot["user_id"]))
                .scalar()
            )
            clear_at_utc = (
                _local_naive_to_utc_naive(clear_at)
                if clear_at is not None
                else None
            )
            run_created_at = run.created_at
            if (
                clear_at_utc is not None
                and (
                    run_created_at is None
                    or run_created_at <= clear_at_utc
                )
            ):
                reason_type = "history_cleared"
                safe_summary = "用户历史已在投递前清除"
            elif str(row.status or "") in {
                "cancelled",
                "legacy_ambiguous_hold",
            }:
                reason_type = "source_cancelled"
                safe_summary = "主动外呼来源已取消"
            else:
                return None
    return cancel_delivery_before_send(
        db,
        outbox_id=outbox_id,
        attempt_id=attempt_id,
        worker_owner=owner,
        lease_token=token,
        reason_type=reason_type,
        safe_summary=safe_summary,
        now=current,
        _project_source=project_source,
    )


def cancel_safe_outbox(
    db: Session,
    *,
    outbox_id: int,
    expected_status: str,
    expected_updated_at: datetime,
    reason_type: str,
    safe_summary: Any,
    now: datetime | None = None,
) -> OutboxCancellationResult:
    """按活动 leaf 的显式版本安全取消尚未发送的投递。"""

    expected = _text(
        expected_status,
        name="expected_status",
        max_length=24,
    )
    reason = _text(reason_type, name="reason_type", max_length=64)
    summary = _summary(safe_summary)
    expected_updated = _utc_naive(expected_updated_at)
    outbox_probe = db.get(OutboundDeliveryOutbox, int(outbox_id))
    run_probe = (
        db.get(OutboundRun, int(outbox_probe.run_id))
        if outbox_probe is not None
        else None
    )
    if outbox_probe is None or run_probe is None:
        raise OutboundFencingError("待取消 outbox 或 run 不存在")
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    outbox = db.get(OutboundDeliveryOutbox, int(outbox_id))
    run = db.get(OutboundRun, int(run_probe.id))
    if outbox is None or run is None:
        raise OutboundFencingError("待取消 outbox 或 run 不存在")
    if outbox.status != expected or outbox.updated_at != expected_updated:
        raise OutboundFencingError("outbox 状态或 updated_at CAS 已失效")
    if expected not in {"pending", "retry_wait", "blocked"}:
        raise OutboundSafetyError("只有未租用的安全 leaf 可以取消")
    if (
        outbox.lease_owner is not None
        or outbox.lease_token is not None
        or outbox.lease_expires_at is not None
    ):
        raise OutboundSafetyError("已租用 leaf 不能由管理员直接取消")
    if run.active_outbox_id != outbox.id:
        raise OutboundFencingError("outbox 已不是 run 的活动 leaf")
    if run.status not in {"queued", "blocked"}:
        raise OutboundFencingError("run 状态与可取消 leaf 不一致")

    outbox_updated = (
        db.query(OutboundDeliveryOutbox)
        .filter(
            OutboundDeliveryOutbox.id == int(outbox.id),
            OutboundDeliveryOutbox.run_id == int(run.id),
            OutboundDeliveryOutbox.status == expected,
            OutboundDeliveryOutbox.updated_at == expected_updated,
            OutboundDeliveryOutbox.lease_owner.is_(None),
            OutboundDeliveryOutbox.lease_token.is_(None),
            OutboundDeliveryOutbox.lease_expires_at.is_(None),
        )
        .update(
            {
                OutboundDeliveryOutbox.status: "cancelled",
                OutboundDeliveryOutbox.next_attempt_at: None,
                OutboundDeliveryOutbox.last_error_type: reason,
                OutboundDeliveryOutbox.last_error_summary: summary,
                OutboundDeliveryOutbox.cancelled_at: current,
                OutboundDeliveryOutbox.cancel_reason_type: reason,
                OutboundDeliveryOutbox.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    run_updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run.id),
            OutboundRun.active_outbox_id == int(outbox.id),
            OutboundRun.status == str(run.status),
        )
        .update(
            {
                OutboundRun.status: "failed",
                OutboundRun.failure_type: reason,
                OutboundRun.failure_summary: summary,
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if outbox_updated != 1 or run_updated != 1:
        raise OutboundFencingError("安全取消 CAS 失败")
    _project_outbound_source(
        db,
        run=run,
        status="cancelled",
        current=current,
        error_summary=summary,
    )
    db.flush()
    result = OutboxCancellationResult(
        applied=True,
        outbox_id=int(outbox.id),
        run_id=int(run.id),
        status="cancelled",
    )
    db.expire_all()
    return result


def resolve_legacy_ambiguous_outreach(
    db: Session,
    *,
    outreach_log_id: int,
    expected_created_at: datetime,
    expected_source_revision: str,
    resolution: str,
    reason: str,
    now: datetime | None = None,
) -> LegacyOutreachResolutionResult:
    """显式取消无法证明投递结果的旧外呼记录，绝不创建成功事实。"""

    if resolution != "cancel_without_replay":
        raise OutboundSafetyError("legacy ambiguous hold 只能取消且不重放")
    normalized_reason = _text(reason, name="reason", max_length=1000)
    del normalized_reason
    expected_created = _utc_naive(expected_created_at)
    expected_revision = _text(
        expected_source_revision,
        name="expected_source_revision",
        max_length=128,
    )
    _utc_naive(now)
    row = db.get(ProactiveOutreachLog, int(outreach_log_id))
    if row is None:
        raise OutboundFencingError("legacy ambiguous outreach 不存在")
    if row.created_at != expected_created:
        raise OutboundFencingError("legacy outreach created_at CAS 已失效")
    if proactive_outreach_source_revision(row) != expected_revision:
        raise OutboundFencingError("legacy outreach source revision 已失效")
    if row.outbound_run_id is not None:
        raise OutboundSafetyError("已有出站 run 的记录不能按 legacy hold 解析")
    if row.status == "cancelled":
        return LegacyOutreachResolutionResult(
            applied=False,
            outreach_log_id=int(row.id),
            status="cancelled",
        )
    if row.status != "legacy_ambiguous_hold":
        raise OutboundSafetyError("只有 legacy ambiguous hold 可以显式解析")

    query = db.query(ProactiveOutreachLog).filter(
        ProactiveOutreachLog.id == int(row.id),
        ProactiveOutreachLog.status == "legacy_ambiguous_hold",
        ProactiveOutreachLog.outbound_run_id.is_(None),
        ProactiveOutreachLog.user_id == row.user_id,
        ProactiveOutreachLog.idempotency_key == row.idempotency_key,
        ProactiveOutreachLog.grounding_json == row.grounding_json,
        ProactiveOutreachLog.judge_reason == row.judge_reason,
        ProactiveOutreachLog.next_check_at == row.next_check_at,
        ProactiveOutreachLog.next_intent == row.next_intent,
        ProactiveOutreachLog.message == row.message,
        ProactiveOutreachLog.forced.is_(bool(row.forced)),
        ProactiveOutreachLog.created_at == expected_created,
    )
    if row.judge_should is None:
        query = query.filter(ProactiveOutreachLog.judge_should.is_(None))
    else:
        query = query.filter(
            ProactiveOutreachLog.judge_should.is_(bool(row.judge_should))
        )
    updated = query.update(
        {ProactiveOutreachLog.status: "cancelled"},
        synchronize_session=False,
    )
    if updated != 1:
        raise OutboundFencingError("legacy ambiguous outreach CAS 失败")
    db.flush()
    result = LegacyOutreachResolutionResult(
        applied=True,
        outreach_log_id=int(row.id),
        status="cancelled",
    )
    db.expire_all()
    return result


def cancel_safe_deliveries_for_source(
    db: Session,
    *,
    source_type: str,
    source_id: str,
    expected_source_revision: str,
    reason_type: str,
    safe_summary: Any,
    now: datetime | None = None,
) -> SourceCancellationSummary:
    """取消尚未产生外部副作用的来源 leaf；投递中或不确定记录只报告风险。"""

    source = _text(source_type, name="source_type", max_length=32)
    source_identity = _text(source_id, name="source_id", max_length=255)
    revision = _text(
        expected_source_revision,
        name="expected_source_revision",
        max_length=128,
    )
    reason = _text(reason_type, name="reason_type", max_length=64)
    summary = _summary(safe_summary)
    current = _locked_current(db, source_type=source, now=now)
    cancelled = 0
    generation_runs = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.source_type == source,
            OutboundRun.source_id == source_identity,
            OutboundRun.source_revision == revision,
            OutboundRun.active_outbox_id.is_(None),
            OutboundRun.status.in_(("claimed", "generating")),
            ~exists().where(
                OutboundDeliveryOutbox.run_id == OutboundRun.id
            ),
        )
        .order_by(OutboundRun.id.asc())
        .all()
    )
    for run in generation_runs:
        previous_status = str(run.status)
        claim_owner = str(run.claim_owner or "")
        claim_token = str(run.claim_token or "")
        run_updated = (
            db.query(OutboundRun)
            .filter(
                OutboundRun.id == int(run.id),
                OutboundRun.source_type == source,
                OutboundRun.source_id == source_identity,
                OutboundRun.source_revision == revision,
                OutboundRun.status == previous_status,
                OutboundRun.claim_owner == claim_owner,
                OutboundRun.claim_token == claim_token,
                OutboundRun.active_outbox_id.is_(None),
                ~exists().where(
                    OutboundDeliveryOutbox.run_id == OutboundRun.id
                ),
            )
            .update(
                {
                    OutboundRun.status: "failed",
                    OutboundRun.claim_owner: None,
                    OutboundRun.claim_token: None,
                    OutboundRun.claim_expires_at: None,
                    OutboundRun.failure_type: reason,
                    OutboundRun.failure_summary: summary,
                    OutboundRun.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if run_updated != 1:
            continue
        attempt_updated = (
            db.query(OutboundGenerationAttempt)
            .filter(
                OutboundGenerationAttempt.run_id == int(run.id),
                OutboundGenerationAttempt.status == "started",
                OutboundGenerationAttempt.owner == claim_owner,
                OutboundGenerationAttempt.fencing_token == claim_token,
            )
            .update(
                {
                    OutboundGenerationAttempt.status: "abandoned",
                    OutboundGenerationAttempt.completed_at: current,
                    OutboundGenerationAttempt.error_type: reason,
                    OutboundGenerationAttempt.error_summary: summary,
                },
                synchronize_session=False,
            )
        )
        expected_attempts = 1 if previous_status == "generating" else 0
        if attempt_updated != expected_attempts:
            raise OutboundFencingError(
                "来源安全取消的 generation attempt CAS 失败"
            )
        _project_outbound_source(
            db,
            run=run,
            status="cancelled",
            current=current,
            error_summary=summary,
        )
        cancelled += 1
    unsafe = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.source_type == source,
            OutboundRun.source_id == source_identity,
            OutboundRun.source_revision == revision,
            OutboundRun.status.in_(("claimed", "generating")),
        )
        .count()
    )
    blocked_runs = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.source_type == source,
            OutboundRun.source_id == source_identity,
            OutboundRun.source_revision == revision,
            OutboundRun.status == "blocked",
            OutboundRun.active_outbox_id.is_(None),
            ~exists().where(
                OutboundDeliveryOutbox.run_id == OutboundRun.id
            ),
        )
        .order_by(OutboundRun.id.asc())
        .all()
    )
    for run in blocked_runs:
        updated = (
            db.query(OutboundRun)
            .filter(
                OutboundRun.id == int(run.id),
                OutboundRun.status == "blocked",
                OutboundRun.active_outbox_id.is_(None),
                ~exists().where(
                    OutboundDeliveryOutbox.run_id == OutboundRun.id
                ),
            )
            .update(
                {
                    OutboundRun.status: "failed",
                    OutboundRun.failure_type: reason,
                    OutboundRun.failure_summary: summary,
                    OutboundRun.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            unsafe += 1
            continue
        _project_outbound_source(
            db,
            run=run,
            status="cancelled",
            current=current,
            error_summary=summary,
        )
        cancelled += 1
    rows = (
        db.query(OutboundRun, OutboundDeliveryOutbox)
        .join(
            OutboundDeliveryOutbox,
            OutboundDeliveryOutbox.id == OutboundRun.active_outbox_id,
        )
        .filter(
            OutboundRun.source_type == source,
            OutboundRun.source_id == source_identity,
            OutboundRun.source_revision == revision,
        )
        .order_by(OutboundDeliveryOutbox.id.asc())
        .all()
    )
    for run, outbox in rows:
        previous_status = str(outbox.status)
        if previous_status in {"leased", "ambiguous"}:
            unsafe += 1
            continue
        if previous_status not in {"pending", "retry_wait", "blocked"}:
            continue
        outbox_updated = (
            db.query(OutboundDeliveryOutbox)
            .filter(
                OutboundDeliveryOutbox.id == int(outbox.id),
                OutboundDeliveryOutbox.status == previous_status,
                OutboundDeliveryOutbox.lease_owner.is_(None),
                OutboundDeliveryOutbox.lease_token.is_(None),
                OutboundDeliveryOutbox.lease_expires_at.is_(None),
            )
            .update(
                {
                    OutboundDeliveryOutbox.status: "cancelled",
                    OutboundDeliveryOutbox.next_attempt_at: None,
                    OutboundDeliveryOutbox.last_error_type: reason,
                    OutboundDeliveryOutbox.last_error_summary: summary,
                    OutboundDeliveryOutbox.cancelled_at: current,
                    OutboundDeliveryOutbox.cancel_reason_type: reason,
                    OutboundDeliveryOutbox.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if outbox_updated != 1:
            unsafe += 1
            continue
        run_updated = (
            db.query(OutboundRun)
            .filter(
                OutboundRun.id == int(run.id),
                OutboundRun.active_outbox_id == int(outbox.id),
                OutboundRun.status.in_(("queued", "blocked")),
            )
            .update(
                {
                    OutboundRun.status: "failed",
                    OutboundRun.failure_type: reason,
                    OutboundRun.failure_summary: summary,
                    OutboundRun.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if run_updated != 1:
            raise OutboundFencingError("来源安全取消的 run CAS 失败")
        _project_outbound_source(
            db,
            run=run,
            status="cancelled",
            current=current,
            error_summary=summary,
        )
        cancelled += 1
    db.flush()
    result = SourceCancellationSummary(cancelled=cancelled, unsafe=unsafe)
    db.expire_all()
    return result


def _scope_fingerprint(
    outbox: OutboundDeliveryOutbox,
    scope_type: str,
) -> str:
    if scope_type == "endpoint":
        return endpoint_circuit_fingerprint(str(outbox.endpoint_key))
    if scope_type == "destination":
        return destination_circuit_fingerprint(
            str(outbox.endpoint_key),
            str(outbox.destination_fingerprint),
        )
    if scope_type == "payload_contract":
        return payload_contract_circuit_fingerprint(
            str(outbox.endpoint_key),
            str(outbox.payload_contract_fingerprint),
        )
    raise ValueError("circuit_scope_type 非法")


def _classified_circuit_scope(
    *,
    outcome: str,
    http_status: int | None,
    result_category: str,
    error_type: str,
) -> str | None:
    if outcome != "permanent_failure":
        return None

    category = result_category.strip().lower()
    error = error_type.strip().lower()
    if http_status in {401, 403, 405, 501, 505}:
        return "endpoint"

    destination_signal = (
        category in {
            "destination",
            "destination_missing",
            "destination_rejected",
            "destination_deleted",
        }
        or error in {
            "destination_missing",
            "destination_rejected",
            "destination_deleted",
            "target_missing",
            "target_rejected",
            "target_deleted",
        }
    )
    if http_status in {404, 410}:
        return "destination" if destination_signal else "endpoint"
    if http_status == 415:
        return "payload_contract" if category == "payload_contract" else "endpoint"
    if http_status in {400, 422}:
        return "payload_contract" if category == "payload_contract" else None
    if http_status == 413 or category == "payload":
        return None
    if destination_signal:
        return "destination"
    if category == "payload_contract":
        return "payload_contract"
    if category == "endpoint":
        return "endpoint"
    return None


def _validate_settlement_classification(
    *,
    outcome: str,
    http_status: int | None,
    result_category: str,
) -> None:
    category = result_category.strip().lower()
    expected_outcome = _RESULT_CATEGORY_OUTCOMES.get(category)
    if expected_outcome is not None and outcome != expected_outcome:
        raise ValueError("result_category 与 outcome 分类不一致")

    if http_status is None:
        return
    expected_http_outcome = None
    if 400 <= http_status <= 499:
        expected_http_outcome = (
            "transient_failure"
            if http_status in _TRANSIENT_CLIENT_HTTP_STATUSES
            else "permanent_failure"
        )
    elif 500 <= http_status <= 599:
        expected_http_outcome = (
            "permanent_failure"
            if http_status in _STABLE_SERVER_HTTP_STATUSES
            else "transient_failure"
        )
    if expected_http_outcome is not None and outcome != expected_http_outcome:
        raise ValueError("HTTP 状态与 outcome 分类不一致")
    if 200 <= http_status <= 299:
        if outcome == "transient_failure":
            raise ValueError("2xx HTTP 状态不能按 transient_failure 结算")
        if (
            outcome == "permanent_failure"
            and category not in _SEMANTIC_2XX_FAILURE_CATEGORIES
        ):
            raise ValueError("2xx HTTP 永久失败缺少可验证的语义分类")


def _settlement_request_sha256(
    *,
    outcome: str,
    transport_phase: str,
    http_status: int | None,
    result_category: str,
    error_type: str,
    safe_summary: str,
    duration_ms: int | None,
    retry_at: datetime | None,
    circuit_scope_type: str | None,
) -> str:
    return _audit_request_sha256(
        "delivery_settlement",
        {
            "outcome": outcome,
            "transport_phase": transport_phase,
            "http_status": http_status,
            "result_category": result_category,
            "error_type": error_type,
            "safe_summary": safe_summary,
            "duration_ms": duration_ms,
            "retry_at": _audit_datetime(retry_at),
            "circuit_scope_type": circuit_scope_type,
        },
    )


def _open_delivery_circuit(
    db: Session,
    *,
    outbox: OutboundDeliveryOutbox,
    attempt: OutboundDeliveryAttempt,
    scope_type: str,
    reason_type: str,
    current: datetime,
) -> None:
    scope = _text(scope_type, name="circuit_scope_type", max_length=32)
    if scope not in _CIRCUIT_SCOPE_TYPES:
        raise ValueError("circuit_scope_type 非法")
    fingerprint = _scope_fingerprint(outbox, scope)
    db.execute(
        sqlite_insert(OutboundDeliveryCircuit)
        .values(
            scope_type=scope,
            scope_fingerprint=fingerprint,
            config_revision=str(attempt.endpoint_config_revision),
            status="open",
            reason_type=reason_type,
            opened_at=current,
            opened_by_attempt_id=int(attempt.id),
            created_at=current,
            updated_at=current,
        )
        .on_conflict_do_update(
            index_elements=[
                "scope_type",
                "scope_fingerprint",
                "config_revision",
            ],
            set_={
                "status": "open",
                "reason_type": reason_type,
                "opened_at": current,
                "opened_by_attempt_id": int(attempt.id),
                "updated_at": current,
            },
        )
    )


def settle_delivery_attempt(
    db: Session,
    *,
    outbox_id: int,
    attempt_id: int,
    worker_owner: str,
    lease_token: str,
    outcome: str,
    transport_phase: str,
    http_status: int | None,
    result_category: str,
    error_type: str,
    safe_summary: Any,
    duration_ms: int | None,
    retry_at: datetime | None = None,
    circuit_scope_type: str | None = None,
    now: datetime | None = None,
) -> DeliverySettlementResult:
    owner = _text(worker_owner, name="worker_owner", max_length=128)
    token = _text(lease_token, name="lease_token", max_length=64)
    outbox_probe = db.get(OutboundDeliveryOutbox, int(outbox_id))
    run_probe = (
        db.get(OutboundRun, int(outbox_probe.run_id))
        if outbox_probe is not None
        else None
    )
    if outbox_probe is None or run_probe is None:
        raise OutboundFencingError("delivery attempt 不存在")
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    normalized_outcome = _text(outcome, name="outcome", max_length=32)
    if normalized_outcome not in {
        "succeeded",
        "transient_failure",
        "permanent_failure",
        "ambiguous",
    }:
        raise ValueError("outcome 非法")
    requested_scope = None
    if circuit_scope_type is not None:
        requested_scope = _text(
            circuit_scope_type,
            name="circuit_scope_type",
            max_length=32,
        )
        if requested_scope not in _CIRCUIT_SCOPE_TYPES:
            raise ValueError("circuit_scope_type 非法")
        if normalized_outcome != "permanent_failure":
            raise ValueError("只有 permanent_failure 可以打开 circuit")
    phase = _text(transport_phase, name="transport_phase", max_length=32)
    normalized_result_category = str(result_category or "")[:64]
    normalized_error_type = str(error_type or "")[:64]
    normalized_safe_summary = _summary(safe_summary)
    if http_status is not None and (
        type(http_status) is not int or not 100 <= http_status <= 599
    ):
        raise ValueError("http_status 必须是 100-599")
    _validate_settlement_classification(
        outcome=normalized_outcome,
        http_status=http_status,
        result_category=normalized_result_category,
    )
    if normalized_outcome == "succeeded" and (
        http_status is None or not 200 <= http_status <= 299
    ):
        raise ValueError("成功结算必须具有 2xx 状态码")
    if duration_ms is not None and (
        type(duration_ms) is not int or duration_ms < 0
    ):
        raise ValueError("duration_ms 必须是非负整数或 null")
    classified_scope = _classified_circuit_scope(
        outcome=normalized_outcome,
        http_status=http_status,
        result_category=normalized_result_category,
        error_type=normalized_error_type,
    )
    if requested_scope is not None and requested_scope != classified_scope:
        raise ValueError("circuit scope 与结构化结果分类不一致")
    circuit_scope_type = classified_scope
    normalized_retry_at = (
        _utc_naive(retry_at) if retry_at is not None else None
    )
    settlement_request_sha256 = _settlement_request_sha256(
        outcome=normalized_outcome,
        transport_phase=phase,
        http_status=http_status,
        result_category=normalized_result_category,
        error_type=normalized_error_type,
        safe_summary=normalized_safe_summary,
        duration_ms=duration_ms,
        retry_at=normalized_retry_at,
        circuit_scope_type=circuit_scope_type,
    )
    existing_outbox = db.get(OutboundDeliveryOutbox, int(outbox_id))
    existing_attempt = db.get(OutboundDeliveryAttempt, int(attempt_id))
    if existing_outbox is not None and existing_attempt is not None:
        existing_run = db.get(OutboundRun, int(existing_outbox.run_id))
        if existing_attempt.status != "started":
            identity_matches = (
                existing_run is not None
                and int(existing_attempt.outbox_id) == int(existing_outbox.id)
                and existing_attempt.worker_owner == owner
                and existing_attempt.lease_token == token
            )
            if not identity_matches:
                raise OutboundFencingError("delivery attempt 已结算或身份已变化")
            audit_fields_match = (
                existing_attempt.status == normalized_outcome
                and existing_attempt.transport_phase == phase
                and existing_attempt.http_status == http_status
                and existing_attempt.result_category == normalized_result_category
                and existing_attempt.error_type == normalized_error_type
                and existing_attempt.safe_summary == normalized_safe_summary
                and existing_attempt.duration_ms == duration_ms
                and existing_attempt.settlement_retry_at == normalized_retry_at
                and existing_attempt.settlement_circuit_scope_type
                == circuit_scope_type
            )
            fingerprint_matches = (
                existing_attempt.settlement_request_sha256
                == settlement_request_sha256
            )
            legacy_terminal_matches = (
                not existing_attempt.settlement_request_sha256
                and audit_fields_match
            )
            if audit_fields_match and (
                fingerprint_matches or legacy_terminal_matches
            ):
                return DeliverySettlementResult(
                    applied=False,
                    outbox_id=int(existing_outbox.id),
                    attempt_id=int(existing_attempt.id),
                    outbox_status=str(existing_outbox.status),
                    run_status=str(existing_run.status),
                )
            raise OutboundConflictError("重复结算的不可变审计事实不一致")
    if (
        normalized_outcome == "transient_failure"
        and normalized_retry_at is not None
        and normalized_retry_at <= current
    ):
        raise ValueError("retry_at 必须严格晚于 now")
    outbox, attempt, run = _require_live_delivery(
        db,
        outbox_id=outbox_id,
        attempt_id=attempt_id,
        worker_owner=owner,
        lease_token=token,
        current=current,
    )
    request_started = bool(attempt.request_started)
    if normalized_outcome in {"succeeded", "ambiguous"} and not request_started:
        raise OutboundSafetyError("成功或不确定结算必须已越过 request boundary")
    next_attempt_at = None
    delivered_at = None
    failure_type = ""
    failure_summary = ""
    if normalized_outcome == "succeeded":
        outbox_status = "delivered"
        run_status = (
            "succeeded_after_ambiguous_replay"
            if bool(run.has_ambiguous_ancestor) or int(outbox.replay_sequence) > 0
            else "succeeded"
        )
        delivered_at = current
    elif normalized_outcome == "ambiguous":
        outbox_status = "ambiguous"
        run_status = "ambiguous"
        failure_type = str(error_type or "ambiguous")[:64]
        failure_summary = _summary(safe_summary)
    elif normalized_outcome == "permanent_failure":
        outbox_status = "failed"
        run_status = "blocked" if circuit_scope_type else "failed"
        failure_type = str(error_type or "permanent_failure")[:64]
        failure_summary = _summary(safe_summary)
    else:
        failure_type = str(error_type or "transient_failure")[:64]
        failure_summary = _summary(safe_summary)
        exhausted = (
            int(outbox.request_started_count) >= int(outbox.max_attempts)
            or current >= outbox.retry_deadline_at
            or normalized_retry_at is None
            or normalized_retry_at >= outbox.retry_deadline_at
        )
        if exhausted:
            outbox_status = "failed"
            run_status = "failed"
            failure_type = "retry_exhausted"
        else:
            outbox_status = "retry_wait"
            run_status = "queued"
            next_attempt_at = normalized_retry_at

    attempt_updated = (
        db.query(OutboundDeliveryAttempt)
        .filter(
            OutboundDeliveryAttempt.id == int(attempt.id),
            OutboundDeliveryAttempt.outbox_id == int(outbox.id),
            OutboundDeliveryAttempt.attempt_no
            == int(outbox.allocated_attempt_count),
            OutboundDeliveryAttempt.status == "started",
            OutboundDeliveryAttempt.worker_owner == owner,
            OutboundDeliveryAttempt.lease_token == token,
            OutboundDeliveryAttempt.request_started.is_(request_started),
        )
        .update(
            {
                OutboundDeliveryAttempt.status: normalized_outcome,
                OutboundDeliveryAttempt.transport_phase: phase,
                OutboundDeliveryAttempt.http_status: http_status,
                OutboundDeliveryAttempt.result_category: (
                    normalized_result_category
                ),
                OutboundDeliveryAttempt.error_type: normalized_error_type,
                OutboundDeliveryAttempt.safe_summary: normalized_safe_summary,
                OutboundDeliveryAttempt.duration_ms: duration_ms,
                OutboundDeliveryAttempt.settlement_retry_at: normalized_retry_at,
                OutboundDeliveryAttempt.settlement_circuit_scope_type: (
                    circuit_scope_type
                ),
                OutboundDeliveryAttempt.settlement_request_sha256: (
                    settlement_request_sha256
                ),
                OutboundDeliveryAttempt.completed_at: current,
            },
            synchronize_session=False,
        )
    )
    outbox_updated = (
        db.query(OutboundDeliveryOutbox)
        .filter(
            OutboundDeliveryOutbox.id == int(outbox.id),
            OutboundDeliveryOutbox.status == "leased",
            OutboundDeliveryOutbox.lease_owner == owner,
            OutboundDeliveryOutbox.lease_token == token,
            OutboundDeliveryOutbox.lease_expires_at > current,
            OutboundDeliveryOutbox.allocated_attempt_count
            == int(attempt.attempt_no),
        )
        .update(
            {
                OutboundDeliveryOutbox.status: outbox_status,
                OutboundDeliveryOutbox.lease_owner: None,
                OutboundDeliveryOutbox.lease_token: None,
                OutboundDeliveryOutbox.lease_expires_at: None,
                OutboundDeliveryOutbox.next_attempt_at: next_attempt_at,
                OutboundDeliveryOutbox.last_error_type: failure_type,
                OutboundDeliveryOutbox.last_error_summary: failure_summary,
                OutboundDeliveryOutbox.delivered_at: delivered_at,
                OutboundDeliveryOutbox.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    run_values: dict[Any, Any] = {
        OutboundRun.status: run_status,
        OutboundRun.failure_type: failure_type,
        OutboundRun.failure_summary: failure_summary,
        OutboundRun.succeeded_at: delivered_at,
        OutboundRun.updated_at: current,
    }
    if normalized_outcome == "ambiguous":
        run_values[OutboundRun.has_ambiguous_ancestor] = True
    run_updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run.id),
            OutboundRun.active_outbox_id == int(outbox.id),
            OutboundRun.status == "delivering",
            OutboundRun.cutover_epoch == int(outbox.cutover_epoch),
        )
        .update(run_values, synchronize_session=False)
    )
    if attempt_updated != 1 or outbox_updated != 1 or run_updated != 1:
        raise OutboundFencingError("delivery settlement CAS 失败")
    if circuit_scope_type is not None:
        _open_delivery_circuit(
            db,
            outbox=outbox,
            attempt=attempt,
            scope_type=circuit_scope_type,
            reason_type=failure_type,
            current=current,
        )
    projection_status = {
        "delivered": "delivered",
        "retry_wait": "retry_wait",
        "failed": "blocked" if circuit_scope_type else "failed",
        "ambiguous": "ambiguous",
    }[outbox_status]
    _project_outbound_source(
        db,
        run=run,
        status=projection_status,
        current=current,
        error_summary=failure_summary,
        succeeded=outbox_status == "delivered",
    )
    if outbox_status == "delivered" and delivered_at is not None:
        _append_delivered_outbound_context(
            db,
            run=run,
            outbox=outbox,
            delivered_at=delivered_at,
        )
    db.flush()
    return DeliverySettlementResult(
        applied=True,
        outbox_id=int(outbox.id),
        attempt_id=int(attempt.id),
        outbox_status=outbox_status,
        run_status=run_status,
    )


def expire_stale_delivery_leases(
    db: Session,
    *,
    endpoint_key: str = "qq_push",
    now: datetime | None = None,
) -> LeaseExpirySummary:
    endpoint = _text(endpoint_key, name="endpoint_key", max_length=64)
    observed_at = _utc_naive(now)
    candidates = (
        db.query(OutboundDeliveryOutbox)
        .filter(
            OutboundDeliveryOutbox.endpoint_key == endpoint,
            OutboundDeliveryOutbox.status == "leased",
            OutboundDeliveryOutbox.lease_expires_at.is_not(None),
            OutboundDeliveryOutbox.lease_expires_at <= observed_at,
        )
        .order_by(OutboundDeliveryOutbox.id.asc())
        .all()
    )
    abandoned = 0
    ambiguous = 0
    for outbox in candidates:
        outbox_id = int(outbox.id)
        run_probe = db.get(OutboundRun, int(outbox.run_id))
        if run_probe is None:
            current = _utc_naive(now)
        else:
            current = _locked_current(
                db,
                source_type=str(run_probe.source_type),
                now=now,
            )
        outbox = db.get(OutboundDeliveryOutbox, outbox_id)
        if (
            outbox is None
            or outbox.endpoint_key != endpoint
            or outbox.status != "leased"
            or outbox.lease_expires_at is None
            or outbox.lease_expires_at > current
        ):
            continue
        attempt = (
            db.query(OutboundDeliveryAttempt)
            .filter(
                OutboundDeliveryAttempt.outbox_id == int(outbox.id),
                OutboundDeliveryAttempt.attempt_no
                == int(outbox.allocated_attempt_count),
            )
            .first()
        )
        run = db.get(OutboundRun, int(outbox.run_id))
        attempt_consistent = bool(
            attempt is not None
            and attempt.status == "started"
            and attempt.worker_owner == outbox.lease_owner
            and attempt.lease_token == outbox.lease_token
        )
        active_leaf_consistent = bool(
            run is not None
            and run.active_outbox_id == outbox.id
            and run.status == "delivering"
        )
        request_started = bool(attempt.request_started) if attempt_consistent else True
        is_ambiguous = (
            request_started
            or not attempt_consistent
            or not active_leaf_consistent
        )
        deadline_exhausted = (
            not is_ambiguous and current >= outbox.retry_deadline_at
        )
        recovery_status = "failed" if deadline_exhausted else "pending"
        recovery_error_type = (
            "retry_exhausted" if deadline_exhausted else "lease_expired"
        )
        recovery_summary = (
            "发送前租约过期且投递重试期限已到期"
            if deadline_exhausted
            else "发送前租约过期，已安全放回队列"
        )
        previous_owner = str(outbox.lease_owner)
        previous_token = str(outbox.lease_token)
        previous_expiry = outbox.lease_expires_at
        updated = (
            db.query(OutboundDeliveryOutbox)
            .filter(
                OutboundDeliveryOutbox.id == int(outbox.id),
                OutboundDeliveryOutbox.endpoint_key == endpoint,
                OutboundDeliveryOutbox.status == "leased",
                OutboundDeliveryOutbox.lease_owner == previous_owner,
                OutboundDeliveryOutbox.lease_token == previous_token,
                OutboundDeliveryOutbox.lease_expires_at == previous_expiry,
                OutboundDeliveryOutbox.lease_expires_at <= current,
                OutboundDeliveryOutbox.request_started_count
                == int(outbox.request_started_count),
            )
            .update(
                {
                    OutboundDeliveryOutbox.status: (
                        "ambiguous" if is_ambiguous else recovery_status
                    ),
                    OutboundDeliveryOutbox.lease_owner: None,
                    OutboundDeliveryOutbox.lease_token: None,
                    OutboundDeliveryOutbox.lease_expires_at: None,
                    OutboundDeliveryOutbox.next_attempt_at: None,
                    OutboundDeliveryOutbox.last_error_type: (
                        "lease_expired" if is_ambiguous else recovery_error_type
                    ),
                    OutboundDeliveryOutbox.last_error_summary: (
                        "请求开始后租约过期，投递结果不确定"
                        if is_ambiguous
                        else recovery_summary
                    ),
                    OutboundDeliveryOutbox.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            continue
        if is_ambiguous:
            if attempt_consistent and attempt is not None:
                ambiguous_summary = "请求开始后租约过期，投递结果不确定"
                settlement_request_sha256 = _settlement_request_sha256(
                    outcome="ambiguous",
                    transport_phase=str(attempt.transport_phase),
                    http_status=attempt.http_status,
                    result_category="ambiguous",
                    error_type="lease_expired",
                    safe_summary=ambiguous_summary,
                    duration_ms=attempt.duration_ms,
                    retry_at=None,
                    circuit_scope_type=None,
                )
                attempt_updated = (
                    db.query(OutboundDeliveryAttempt)
                    .filter(
                        OutboundDeliveryAttempt.id == int(attempt.id),
                        OutboundDeliveryAttempt.outbox_id == int(outbox.id),
                        OutboundDeliveryAttempt.attempt_no
                        == int(outbox.allocated_attempt_count),
                        OutboundDeliveryAttempt.status == "started",
                        OutboundDeliveryAttempt.worker_owner == previous_owner,
                        OutboundDeliveryAttempt.lease_token == previous_token,
                        OutboundDeliveryAttempt.request_started.is_(
                            request_started
                        ),
                    )
                    .update(
                        {
                            OutboundDeliveryAttempt.status: "ambiguous",
                            OutboundDeliveryAttempt.result_category: "ambiguous",
                            OutboundDeliveryAttempt.error_type: "lease_expired",
                            OutboundDeliveryAttempt.safe_summary: ambiguous_summary,
                            OutboundDeliveryAttempt.settlement_retry_at: None,
                            OutboundDeliveryAttempt.settlement_circuit_scope_type: (
                                None
                            ),
                            OutboundDeliveryAttempt.settlement_request_sha256: (
                                settlement_request_sha256
                            ),
                            OutboundDeliveryAttempt.completed_at: current,
                        },
                        synchronize_session=False,
                    )
                )
                if attempt_updated != 1:
                    raise OutboundFencingError("过期 attempt ambiguous CAS 失败")
            if active_leaf_consistent and run is not None:
                run_updated = (
                    db.query(OutboundRun)
                    .filter(
                        OutboundRun.id == int(run.id),
                        OutboundRun.active_outbox_id == int(outbox.id),
                        OutboundRun.status == "delivering",
                    )
                    .update(
                        {
                            OutboundRun.status: "ambiguous",
                            OutboundRun.has_ambiguous_ancestor: True,
                            OutboundRun.failure_type: "lease_expired",
                            OutboundRun.failure_summary: (
                                "请求开始后租约过期，投递结果不确定"
                            ),
                            OutboundRun.updated_at: current,
                        },
                        synchronize_session=False,
                    )
                )
                if run_updated != 1:
                    raise OutboundFencingError("过期 run ambiguous CAS 失败")
                _project_outbound_source(
                    db,
                    run=run,
                    status="ambiguous",
                    current=current,
                    error_summary="请求开始后租约过期，投递结果不确定",
                )
            ambiguous += 1
        else:
            assert attempt is not None
            assert run is not None
            attempt_updated = (
                db.query(OutboundDeliveryAttempt)
                .filter(
                    OutboundDeliveryAttempt.id == int(attempt.id),
                    OutboundDeliveryAttempt.outbox_id == int(outbox.id),
                    OutboundDeliveryAttempt.attempt_no
                    == int(outbox.allocated_attempt_count),
                    OutboundDeliveryAttempt.status == "started",
                    OutboundDeliveryAttempt.worker_owner == previous_owner,
                    OutboundDeliveryAttempt.lease_token == previous_token,
                    OutboundDeliveryAttempt.request_started.is_(False),
                    OutboundDeliveryAttempt.transport_phase == "allocated",
                )
                .update(
                    {
                        OutboundDeliveryAttempt.status: "abandoned_before_send",
                        OutboundDeliveryAttempt.transport_phase: "allocated",
                        OutboundDeliveryAttempt.result_category: "before_send",
                        OutboundDeliveryAttempt.error_type: "lease_expired",
                        OutboundDeliveryAttempt.safe_summary: (
                            "发送前租约过期，未消耗网络预算"
                        ),
                        OutboundDeliveryAttempt.completed_at: current,
                    },
                    synchronize_session=False,
                )
            )
            run_updated = (
                db.query(OutboundRun)
                .filter(
                    OutboundRun.id == int(run.id),
                    OutboundRun.active_outbox_id == int(outbox.id),
                    OutboundRun.status == "delivering",
                )
                .update(
                    {
                        OutboundRun.status: (
                            "failed" if deadline_exhausted else "queued"
                        ),
                        OutboundRun.failure_type: (
                            "retry_exhausted" if deadline_exhausted else ""
                        ),
                        OutboundRun.failure_summary: (
                            recovery_summary if deadline_exhausted else ""
                        ),
                        OutboundRun.updated_at: current,
                    },
                    synchronize_session=False,
                )
            )
            if attempt_updated != 1 or run_updated != 1:
                raise OutboundFencingError("发送前租约过期 CAS 失败")
            _project_outbound_source(
                db,
                run=run,
                status="failed" if deadline_exhausted else "queued",
                current=current,
                error_summary=recovery_summary if deadline_exhausted else "",
            )
            abandoned += 1
    db.flush()
    result = LeaseExpirySummary(
        abandoned_before_send=abandoned,
        ambiguous=ambiguous,
    )
    db.expire_all()
    return result


def _replay_request_sha256(
    *,
    parent: OutboundDeliveryOutbox,
    manual_request_key: str,
    reason: str,
    max_attempts: int,
    retry_deadline_at: datetime,
    endpoint_config_revision: str,
) -> str:
    return _audit_request_sha256(
        "manual_replay",
        {
            "parent_outbox_id": int(parent.id),
            "parent_replay_sequence": int(parent.replay_sequence),
            "manual_request_key": manual_request_key,
            "reason": reason,
            "max_attempts": max_attempts,
            "retry_deadline_at": _audit_datetime(retry_deadline_at),
            "endpoint_config_revision": endpoint_config_revision,
            "destination_snapshot_json": str(parent.destination_snapshot_json),
            "destination_fingerprint": str(parent.destination_fingerprint),
            "target_type": str(parent.target_type),
            "endpoint_key": str(parent.endpoint_key),
            "payload_json": str(parent.payload_json),
            "payload_sha256": str(parent.payload_sha256),
            "payload_contract_fingerprint": str(
                parent.payload_contract_fingerprint
            ),
            "cutover_epoch": int(parent.cutover_epoch),
        },
    )


def create_delivery_replay(
    db: Session,
    *,
    parent_outbox_id: int,
    manual_request_key: str,
    confirm_duplicate_risk: bool,
    reason: str,
    max_attempts: int,
    retry_deadline_at: datetime,
    endpoint_config_revision: str,
    now: datetime | None = None,
) -> ReplayResult:
    if confirm_duplicate_risk is not True:
        raise OutboundSafetyError("ambiguous replay 必须显式确认重复投递风险")
    request_key = _text(
        manual_request_key,
        name="manual_request_key",
        max_length=255,
    )
    normalized_reason = _text(reason, name="reason", max_length=1000)
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts 必须是正整数")
    parent_probe = db.get(OutboundDeliveryOutbox, int(parent_outbox_id))
    run_probe = (
        db.get(OutboundRun, int(parent_probe.run_id))
        if parent_probe is not None
        else None
    )
    if parent_probe is None or run_probe is None:
        raise OutboundSafetyError("replay parent 缺少 outbox 或 run")
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    deadline = _utc_naive(retry_deadline_at)
    revision = _text(
        endpoint_config_revision,
        name="endpoint_config_revision",
        max_length=128,
    )
    parent = db.get(OutboundDeliveryOutbox, int(parent_outbox_id))
    if parent is None or parent.status != "ambiguous":
        raise OutboundSafetyError("只有 active ambiguous leaf 可以 replay")
    run = db.get(OutboundRun, int(parent.run_id))
    if run is None:
        raise OutboundSafetyError("replay parent 缺少 run")
    replay_key = _fingerprint(
        "manual_replay",
        str(parent.id),
        request_key,
    )
    replay_request_sha256 = _replay_request_sha256(
        parent=parent,
        manual_request_key=request_key,
        reason=normalized_reason,
        max_attempts=max_attempts,
        retry_deadline_at=deadline,
        endpoint_config_revision=revision,
    )
    existing = (
        db.query(OutboundDeliveryOutbox)
        .filter(OutboundDeliveryOutbox.idempotency_key == replay_key)
        .first()
    )
    if existing is not None:
        expected_sequence = int(parent.replay_sequence) + 1
        immutable_facts_match = (
            existing.replay_of_outbox_id == parent.id
            and int(existing.run_id) == int(run.id)
            and int(existing.replay_sequence) == expected_sequence
            and existing.destination_snapshot_json == parent.destination_snapshot_json
            and existing.destination_fingerprint == parent.destination_fingerprint
            and existing.target_type == parent.target_type
            and existing.endpoint_key == parent.endpoint_key
            and existing.payload_json == parent.payload_json
            and existing.payload_sha256 == parent.payload_sha256
            and int(existing.max_attempts) == int(max_attempts)
            and existing.retry_deadline_at == deadline
            and existing.endpoint_config_revision == revision
            and existing.payload_contract_fingerprint
            == parent.payload_contract_fingerprint
            and existing.replay_request_sha256 == replay_request_sha256
        )
        if not immutable_facts_match:
            raise OutboundConflictError("同一 manual replay key 的不可变事实不一致")
        return ReplayResult(
            outbox_id=int(existing.id),
            run_id=int(existing.run_id),
            replay_sequence=int(existing.replay_sequence),
            created=False,
        )
    if deadline <= current:
        raise ValueError("retry_deadline_at 必须晚于 now")
    if run.active_outbox_id != parent.id:
        raise OutboundConflictError("parent 已不是 active replay leaf")
    control = _control(db, str(run.source_type))
    if (
        control.mode != "outbox_active"
        or int(control.cutover_epoch) != int(run.cutover_epoch)
        or int(parent.cutover_epoch) != int(run.cutover_epoch)
    ):
        raise OutboundSafetyError("当前 cutover control 禁止 replay")
    if _open_circuit_row(
        db,
        _outbox_circuit_facts(parent, actual_config_revision=revision),
    ) is not None:
        raise OutboundSafetyError("适用 circuit 打开时不能 replay")

    sequence = int(parent.replay_sequence) + 1
    insert_result = db.execute(
        sqlite_insert(OutboundDeliveryOutbox)
        .values(
            run_id=int(run.id),
            idempotency_key=replay_key,
            destination_snapshot_json=str(parent.destination_snapshot_json),
            destination_fingerprint=str(parent.destination_fingerprint),
            target_type=str(parent.target_type),
            endpoint_key=str(parent.endpoint_key),
            payload_json=str(parent.payload_json),
            payload_sha256=str(parent.payload_sha256),
            status="pending",
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            next_attempt_at=None,
            allocated_attempt_count=0,
            request_started_count=0,
            max_attempts=max_attempts,
            retry_deadline_at=deadline,
            last_error_type="manual_replay",
            last_error_summary=normalized_reason,
            delivered_at=None,
            cancelled_at=None,
            cancel_reason_type=None,
            replay_of_outbox_id=int(parent.id),
            replay_sequence=sequence,
            replay_request_sha256=replay_request_sha256,
            cutover_epoch=int(parent.cutover_epoch),
            endpoint_config_revision=revision,
            payload_contract_fingerprint=str(
                parent.payload_contract_fingerprint
            ),
            created_at=current,
            updated_at=current,
        )
        .on_conflict_do_nothing()
    )
    child = (
        db.query(OutboundDeliveryOutbox)
        .filter(
            OutboundDeliveryOutbox.run_id == int(run.id),
            OutboundDeliveryOutbox.destination_fingerprint
            == parent.destination_fingerprint,
            OutboundDeliveryOutbox.replay_sequence == sequence,
        )
        .first()
    )
    if child is None:
        raise RuntimeError("原子创建 replay 后未找到 child")
    if child.idempotency_key != replay_key:
        raise OutboundConflictError("active leaf 已由其他 replay 请求推进")
    created = insert_result.rowcount == 1
    if created:
        updated = (
            db.query(OutboundRun)
            .filter(
                OutboundRun.id == int(run.id),
                OutboundRun.active_outbox_id == int(parent.id),
                OutboundRun.status == "ambiguous",
                OutboundRun.cutover_epoch == int(parent.cutover_epoch),
            )
            .update(
                {
                    OutboundRun.active_outbox_id: int(child.id),
                    OutboundRun.status: "queued",
                    OutboundRun.has_ambiguous_ancestor: True,
                    OutboundRun.succeeded_at: None,
                    OutboundRun.failure_type: "",
                    OutboundRun.failure_summary: "",
                    OutboundRun.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            raise OutboundConflictError("active replay leaf CAS 失败")
    db.flush()
    result = ReplayResult(
        outbox_id=int(child.id),
        run_id=int(run.id),
        replay_sequence=sequence,
        created=created,
    )
    db.expire_all()
    return result


def reset_delivery_circuit(
    db: Session,
    *,
    scope_type: str,
    scope_fingerprint: str,
    config_revision: str,
    expected_updated_at: datetime,
    now: datetime | None = None,
) -> CircuitResetResult:
    scope = _text(scope_type, name="scope_type", max_length=32)
    if scope not in _CIRCUIT_SCOPE_TYPES:
        raise ValueError("scope_type 非法")
    fingerprint = _text(
        scope_fingerprint,
        name="scope_fingerprint",
        max_length=64,
    )
    revision = _text(config_revision, name="config_revision", max_length=128)
    expected = _utc_naive(expected_updated_at)
    current = _utc_naive(now)
    row = (
        db.query(OutboundDeliveryCircuit)
        .filter(
            OutboundDeliveryCircuit.scope_type == scope,
            OutboundDeliveryCircuit.scope_fingerprint == fingerprint,
            OutboundDeliveryCircuit.config_revision == revision,
        )
        .first()
    )
    if row is None:
        return CircuitResetResult(applied=False, circuit_id=None, status="missing")
    updated = (
        db.query(OutboundDeliveryCircuit)
        .filter(
            OutboundDeliveryCircuit.id == int(row.id),
            OutboundDeliveryCircuit.status == "open",
            OutboundDeliveryCircuit.updated_at == expected,
        )
        .update(
            {
                OutboundDeliveryCircuit.status: "closed",
                OutboundDeliveryCircuit.reason_type: "",
                OutboundDeliveryCircuit.opened_at: None,
                OutboundDeliveryCircuit.opened_by_attempt_id: None,
                OutboundDeliveryCircuit.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    db.flush()
    result = CircuitResetResult(
        applied=updated == 1,
        circuit_id=int(row.id),
        status="closed" if updated == 1 else str(row.status),
    )
    db.expire_all()
    return result


def _has_unsafe_legacy_run(db: Session, *, source_type: str) -> bool:
    unsafe_run = (
        db.query(OutboundRun.id)
        .filter(
            OutboundRun.source_type == source_type,
            OutboundRun.delivery_mode == "legacy_direct",
            OutboundRun.status.in_((
                "claimed",
                "generating",
                "queued",
                "delivering",
                "blocked",
                "ambiguous",
            )),
        )
        .first()
    )
    if unsafe_run is not None:
        return True
    unsafe_leaf = (
        db.query(OutboundDeliveryOutbox.id)
        .join(OutboundRun, OutboundRun.id == OutboundDeliveryOutbox.run_id)
        .filter(
            OutboundRun.source_type == source_type,
            OutboundRun.delivery_mode == "legacy_direct",
            OutboundRun.active_outbox_id == OutboundDeliveryOutbox.id,
            OutboundDeliveryOutbox.status.in_((
                "pending",
                "retry_wait",
                "leased",
                "blocked",
                "ambiguous",
            )),
        )
        .first()
    )
    if unsafe_leaf is not None:
        return True
    return (
        db.query(OutboundDeliveryAttempt.id)
        .join(
            OutboundDeliveryOutbox,
            OutboundDeliveryOutbox.id == OutboundDeliveryAttempt.outbox_id,
        )
        .join(OutboundRun, OutboundRun.id == OutboundDeliveryOutbox.run_id)
        .filter(
            OutboundRun.source_type == source_type,
            OutboundRun.delivery_mode == "legacy_direct",
            OutboundDeliveryAttempt.status == "started",
        )
        .first()
        is not None
    )


def _has_unsafe_unmaterialized_outbox_run(
    db: Session,
    *,
    source_type: str,
) -> bool:
    return (
        db.query(OutboundRun.id)
        .filter(
            OutboundRun.source_type == source_type,
            OutboundRun.delivery_mode == "outbox",
            OutboundRun.active_outbox_id.is_(None),
            OutboundRun.status.in_((
                "claimed",
                "generating",
                "delivering",
                "blocked",
                "ambiguous",
            )),
        )
        .first()
        is not None
    )


def _unsafe_for_control_transition(
    db: Session,
    *,
    source_type: str,
    epoch: int,
    include_legacy_runs: bool,
) -> bool:
    unsafe_generation = (
        db.query(OutboundRun.id)
        .filter(
            OutboundRun.source_type == source_type,
            OutboundRun.cutover_epoch == int(epoch),
            OutboundRun.active_outbox_id.is_(None),
            OutboundRun.status.in_((
                "claimed",
                "generating",
                "delivering",
                "blocked",
                "ambiguous",
            )),
        )
        .first()
    )
    if unsafe_generation is not None:
        return True
    if include_legacy_runs and _has_unsafe_legacy_run(
        db,
        source_type=source_type,
    ):
        return True
    unsafe_outbox = (
        db.query(OutboundDeliveryOutbox.id)
        .join(OutboundRun, OutboundRun.id == OutboundDeliveryOutbox.run_id)
        .filter(
            OutboundRun.source_type == source_type,
            OutboundDeliveryOutbox.cutover_epoch == int(epoch),
            or_(
                OutboundDeliveryOutbox.status.in_((
                    "pending",
                    "retry_wait",
                    "leased",
                    "blocked",
                )),
                (
                    (OutboundDeliveryOutbox.status == "ambiguous")
                    & (
                        OutboundRun.active_outbox_id
                        == OutboundDeliveryOutbox.id
                    )
                ),
            ),
        )
        .first()
    )
    if unsafe_outbox is not None:
        return True
    return (
        db.query(OutboundDeliveryAttempt.id)
        .join(
            OutboundDeliveryOutbox,
            OutboundDeliveryOutbox.id == OutboundDeliveryAttempt.outbox_id,
        )
        .join(OutboundRun, OutboundRun.id == OutboundDeliveryOutbox.run_id)
        .filter(
            OutboundRun.source_type == source_type,
            OutboundDeliveryOutbox.cutover_epoch == int(epoch),
            OutboundDeliveryAttempt.status == "started",
        )
        .first()
        is not None
    )


def transition_delivery_control(
    db: Session,
    *,
    source_type: str,
    expected_mode: str,
    new_mode: str,
    expected_writer_version: int,
    actor_owner: str,
    actor_token: str,
    protocol_version: int,
    effective_from: datetime,
    writer_lease_seconds: int | float,
    now: datetime | None = None,
) -> ControlTransitionResult:
    source = _text(source_type, name="source_type", max_length=32)
    old_mode = _text(expected_mode, name="expected_mode", max_length=24)
    target_mode = _text(new_mode, name="new_mode", max_length=24)
    if (old_mode, target_mode) not in _CONTROL_TRANSITIONS:
        raise InvalidOutboundTransitionError(
            f"不允许的 control 转换: {old_mode} -> {target_mode}"
        )
    if type(expected_writer_version) is not int or expected_writer_version < 0:
        raise ValueError("expected_writer_version 必须是非负整数")
    if type(protocol_version) is not int or protocol_version < OUTBOUND_PROTOCOL_VERSION:
        raise OutboundSafetyError("cutover 需要当前 outbox writer 协议")
    owner = _text(actor_owner, name="actor_owner", max_length=128)
    token = _text(actor_token, name="actor_token", max_length=64)
    seconds = _positive_seconds(
        writer_lease_seconds,
        name="writer_lease_seconds",
    )
    current = _locked_current(db, source_type=source, now=now)
    requested_effective = _utc_naive(effective_from)
    control = _require_writer(
        db,
        source_type=source,
        owner=owner,
        token=token,
        protocol_version=protocol_version,
        current=current,
    )
    if (
        control.mode != old_mode
        or int(control.writer_version) != expected_writer_version
    ):
        raise OutboundFencingError("control mode 或 writer_version CAS 已失效")

    if (old_mode, target_mode) in {
        ("legacy_direct", "outbox_hold"),
        ("outbox_active", "outbox_draining"),
    }:
        if requested_effective <= current:
            raise OutboundSafetyError("cutover effective_from 必须严格晚于 now")
        next_effective = requested_effective
    else:
        if current < control.effective_from:
            raise OutboundSafetyError("尚未到达 control effective boundary")
        if requested_effective != control.effective_from:
            raise OutboundSafetyError("确认转换不得改变既有 effective boundary")
        next_effective = control.effective_from

    if old_mode == "legacy_direct" and _unsafe_for_control_transition(
        db,
        source_type=source,
        epoch=int(control.cutover_epoch),
        include_legacy_runs=True,
    ):
        raise OutboundSafetyError("仍有未安全结算的 legacy writer/run")
    if old_mode == "outbox_hold" and _has_unsafe_legacy_run(
        db,
        source_type=source,
    ):
        raise OutboundSafetyError("hold boundary 仍有未安全结算的 legacy run")
    if old_mode == "outbox_active" and (
        _has_unsafe_legacy_run(db, source_type=source)
        or _has_unsafe_unmaterialized_outbox_run(
            db,
            source_type=source,
        )
    ):
        raise OutboundSafetyError("仍有未安全结算的 legacy 或生成中 run")
    if old_mode == "outbox_draining" and _unsafe_for_control_transition(
        db,
        source_type=source,
        epoch=int(control.cutover_epoch),
        include_legacy_runs=True,
    ):
        raise OutboundSafetyError("draining epoch 仍有不安全队列或 attempt")

    next_epoch = int(control.cutover_epoch)
    if (old_mode, target_mode) in {
        ("legacy_direct", "outbox_hold"),
        ("outbox_draining", "legacy_direct"),
    }:
        next_epoch += 1
    next_writer_version = int(control.writer_version) + 1
    lease_expires_at = current + timedelta(seconds=seconds)
    updated = (
        db.query(OutboundDeliveryControl)
        .filter(
            OutboundDeliveryControl.source_type == source,
            OutboundDeliveryControl.mode == old_mode,
            OutboundDeliveryControl.writer_version == expected_writer_version,
            OutboundDeliveryControl.writer_owner == owner,
            OutboundDeliveryControl.writer_token == token,
            OutboundDeliveryControl.writer_lease_expires_at > current,
            OutboundDeliveryControl.protocol_version == protocol_version,
        )
        .update(
            {
                OutboundDeliveryControl.mode: target_mode,
                OutboundDeliveryControl.cutover_epoch: next_epoch,
                OutboundDeliveryControl.effective_from: next_effective,
                OutboundDeliveryControl.writer_version: next_writer_version,
                OutboundDeliveryControl.writer_lease_expires_at: lease_expires_at,
                OutboundDeliveryControl.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        raise OutboundFencingError("control transition CAS 失败")
    db.flush()
    result = ControlTransitionResult(
        applied=True,
        source_type=source,
        mode=target_mode,
        cutover_epoch=next_epoch,
        writer_version=next_writer_version,
        effective_from=next_effective,
    )
    db.expire_all()
    return result
