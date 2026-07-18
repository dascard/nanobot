"""定时任务 producer：幂等领取、冻结生成输入并持久化投递 leaf。"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
import os
import secrets
import socket
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import exists, or_
from sqlalchemy.orm import Session

from core.database import OutboundDeliveryOutbox, OutboundRun, ScheduledTask
from core.message_envelope import build_chat_response_envelope
from core.outbound_delivery import (
    OUTBOUND_PROTOCOL_VERSION,
    OutboundConflictError,
    SourceCancellationSummary,
    cancel_safe_deliveries_for_source,
    claim_outbound_run,
    commit_generated_outbox,
    fail_outbound_generation,
    lock_outbound_source_control,
    quarantine_expired_generation_run,
    start_generation_attempt,
)
from core.outbound_delivery_service import (
    CustomTransportConfig,
    LegacyWriterLeaseSnapshot,
    LegacyWriterTakeover,
    OutboundDeliveryWorkResult,
    OutboundTransport,
    OutboundTransportRequest,
    OutboundWorkerConfig,
    deliver_legacy_outbound_once,
    snapshot_live_legacy_writer,
)


SOURCE_TYPE = "scheduled_task"
ENDPOINT_KEY = "qq_push"
PAYLOAD_CONTRACT = "qq-envelope-v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")
DEFAULT_CLAIM_LEASE_SECONDS = 900.0
DEFAULT_WRITER_LEASE_SECONDS = 900.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_DEADLINE_SECONDS = 86400.0
CRON_OCCURRENCE_LATE_CLAIM_GRACE_SECONDS = 120.0
_PROCESS_OWNER = (
    f"scheduled-task:{socket.gethostname().strip() or 'host'}:{uuid4().hex}"
)[:128]
_PROCESS_WRITER_TOKEN = secrets.token_hex(32)
logger = logging.getLogger("nanobot.scheduled_task_outbound")


class ScheduledTaskOutboundError(RuntimeError):
    """定时任务 producer 的可诊断错误。"""


class ScheduledTaskNotFoundError(ScheduledTaskOutboundError):
    """目标定时任务不存在。"""


@dataclass(frozen=True, slots=True)
class ScheduledOccurrence:
    occurrence_key: str
    scheduled_for: datetime


@dataclass(frozen=True, slots=True)
class ScheduledTaskSnapshot:
    schema_version: int
    task_id: int
    name: str
    cron_expr: str
    target_type: str
    target_id: str
    prompt_template: str
    enabled: bool

    @property
    def id(self) -> int:
        """兼容现有定时任务生成器读取 ORM 风格主键。"""
        return self.task_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "name": self.name,
            "cron_expr": self.cron_expr,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "prompt_template": self.prompt_template,
            "enabled": self.enabled,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ScheduledTaskSnapshot":
        if int(value.get("schema_version") or 0) != 1:
            raise ScheduledTaskOutboundError("定时任务冻结快照版本不受支持")
        task_id = value.get("task_id")
        if type(task_id) is not int or task_id <= 0:
            raise ScheduledTaskOutboundError("定时任务冻结快照缺少 task_id")
        target_type = str(value.get("target_type") or "").strip()
        target_id = str(value.get("target_id") or "").strip()
        if target_type not in {"private", "group"} or not target_id:
            raise ScheduledTaskOutboundError("定时任务冻结目标无效")
        return cls(
            schema_version=1,
            task_id=task_id,
            name=str(value.get("name") or "").strip(),
            cron_expr=str(value.get("cron_expr") or "").strip(),
            target_type=target_type,
            target_id=target_id,
            prompt_template=str(value.get("prompt_template") or ""),
            enabled=bool(value.get("enabled")),
        )


@dataclass(frozen=True, slots=True)
class ScheduledTaskProducerConfig:
    endpoint_config_revision: str
    producer_owner: str = _PROCESS_OWNER
    writer_token: str = _PROCESS_WRITER_TOKEN
    claim_lease_seconds: float = DEFAULT_CLAIM_LEASE_SECONDS
    writer_lease_seconds: float = DEFAULT_WRITER_LEASE_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    retry_deadline_seconds: float = DEFAULT_RETRY_DEADLINE_SECONDS

    def __post_init__(self) -> None:
        revision = str(self.endpoint_config_revision or "").strip()
        owner = str(self.producer_owner or "").strip()
        token = str(self.writer_token or "").strip()
        if not revision or len(revision) > 128:
            raise ValueError("NANOBOT_QQ_PUSH_CONFIG_REVISION 必须是非空短文本")
        if not owner or len(owner) > 128:
            raise ValueError("producer_owner 必须是 1-128 字符")
        if not token or len(token) > 64:
            raise ValueError("writer_token 必须是 1-64 字符")
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError("NANOBOT_OUTBOUND_MAX_ATTEMPTS 必须是正整数")
        for name, value in (
            ("claim_lease_seconds", self.claim_lease_seconds),
            ("writer_lease_seconds", self.writer_lease_seconds),
            ("retry_deadline_seconds", self.retry_deadline_seconds),
        ):
            if type(value) not in {int, float} or not math.isfinite(float(value)):
                raise ValueError(f"{name} 必须是有限正数")
            if float(value) <= 0:
                raise ValueError(f"{name} 必须是有限正数")
        object.__setattr__(self, "endpoint_config_revision", revision)
        object.__setattr__(self, "producer_owner", owner)
        object.__setattr__(self, "writer_token", token)

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "ScheduledTaskProducerConfig":
        source = os.environ if environ is None else environ
        return cls(
            endpoint_config_revision=source.get(
                "NANOBOT_QQ_PUSH_CONFIG_REVISION",
                "1",
            ),
            claim_lease_seconds=_env_float(
                source,
                "NANOBOT_SCHEDULED_TASK_CLAIM_LEASE_SECONDS",
                DEFAULT_CLAIM_LEASE_SECONDS,
            ),
            writer_lease_seconds=_env_float(
                source,
                "NANOBOT_SCHEDULED_TASK_WRITER_LEASE_SECONDS",
                DEFAULT_WRITER_LEASE_SECONDS,
            ),
            max_attempts=_env_int(
                source,
                "NANOBOT_OUTBOUND_MAX_ATTEMPTS",
                DEFAULT_MAX_ATTEMPTS,
            ),
            retry_deadline_seconds=_env_float(
                source,
                "NANOBOT_OUTBOUND_RETRY_DEADLINE_SECONDS",
                DEFAULT_RETRY_DEADLINE_SECONDS,
            ),
        )

    @classmethod
    def for_tests(
        cls,
        *,
        endpoint_config_revision: str = "test-revision",
    ) -> "ScheduledTaskProducerConfig":
        return cls(
            endpoint_config_revision=endpoint_config_revision,
            producer_owner="scheduled-task-test-producer",
            writer_token="scheduled-task-test-writer-token",
        )


@dataclass(frozen=True, slots=True)
class ScheduledTaskEnqueueResult:
    run_id: int
    outbox_id: int | None
    status: str
    delivery_mode: str
    deduplicated: bool
    generation_attempted: bool


TaskGenerator = Callable[
    [ScheduledTaskSnapshot],
    str | None | Awaitable[str | None],
]


def _env_float(
    environ: Mapping[str, str],
    name: str,
    default: float,
) -> float:
    try:
        return float(environ.get(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是有限正数") from exc


def _env_int(
    environ: Mapping[str, str],
    name: str,
    default: int,
) -> int:
    try:
        return int(environ.get(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是正整数") from exc


def _utc_naive(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current
    return current.astimezone(timezone.utc).replace(tzinfo=None)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _cron_field_matches(value: int, expression: str) -> bool:
    if expression == "*":
        return True
    for part in expression.split(","):
        normalized = part.strip()
        if "-" in normalized:
            lower, upper = normalized.split("-", 1)
            if int(lower) <= value <= int(upper):
                return True
        elif normalized.startswith("*/"):
            step = int(normalized[2:])
            if value % step == 0:
                return True
        elif normalized.isdigit() and int(normalized) == value:
            return True
    return False


def scheduled_cron_matches(cron_expr: str, local_time: datetime) -> bool:
    """按调度器现有的五段 cron 子集判断上海本地时间。"""

    try:
        localized = (
            local_time.replace(tzinfo=SHANGHAI)
            if local_time.tzinfo is None
            else local_time.astimezone(SHANGHAI)
        )
        parts = str(cron_expr or "").strip().split()
        if len(parts) != 5:
            return False
        minute, hour, day, month, day_of_week = parts
        return all((
            _cron_field_matches(localized.minute, minute),
            _cron_field_matches(localized.hour, hour),
            _cron_field_matches(localized.day, day),
            _cron_field_matches(localized.month, month),
            _cron_field_matches(localized.isoweekday(), day_of_week),
        ))
    except (TypeError, ValueError, ZeroDivisionError):
        return False


def scheduled_cron_occurrence(
    *,
    task_id: int,
    local_time: datetime,
) -> ScheduledOccurrence:
    if type(task_id) is not int or task_id <= 0:
        raise ValueError("task_id 必须是正整数")
    if not isinstance(local_time, datetime):
        raise TypeError("local_time 必须是 datetime")
    localized = (
        local_time.replace(tzinfo=SHANGHAI)
        if local_time.tzinfo is None
        else local_time.astimezone(SHANGHAI)
    )
    slot = localized.replace(second=0, microsecond=0)
    scheduled_for = slot.astimezone(timezone.utc).replace(tzinfo=None)
    return ScheduledOccurrence(
        occurrence_key=(
            f"scheduled-task:{task_id}:cron:"
            f"{scheduled_for.strftime('%Y%m%dT%H%M%SZ')}"
        ),
        scheduled_for=scheduled_for,
    )


def scheduled_manual_occurrence(
    *,
    task_id: int,
    idempotency_key: str,
    now: datetime | None = None,
) -> ScheduledOccurrence:
    raw_key = str(idempotency_key or "").strip()
    if not raw_key or len(raw_key) > 512:
        raise ValueError("手动执行必须提供 1-512 字符的幂等键")
    return ScheduledOccurrence(
        occurrence_key=(
            f"scheduled-task:{task_id}:manual:{_sha256(raw_key)}"
        ),
        scheduled_for=_utc_naive(now),
    )


def scheduled_task_source_revision(snapshot: ScheduledTaskSnapshot) -> str:
    return _sha256(_canonical_json(snapshot.to_dict()))


def scheduled_task_destination_fingerprint(snapshot: ScheduledTaskSnapshot) -> str:
    return _sha256(
        f"{ENDPOINT_KEY}\0{snapshot.target_type}\0{snapshot.target_id}"
    )


def _snapshot_task(task: ScheduledTask) -> ScheduledTaskSnapshot:
    if task.id is None:
        raise ScheduledTaskOutboundError("定时任务尚未持久化")
    return ScheduledTaskSnapshot.from_mapping({
        "schema_version": 1,
        "task_id": int(task.id),
        "name": task.name,
        "cron_expr": task.cron_expr,
        "target_type": task.target_type,
        "target_id": task.target_id,
        "prompt_template": task.prompt_template,
        "enabled": bool(task.enabled),
    })


def snapshot_scheduled_task(task: ScheduledTask) -> ScheduledTaskSnapshot:
    """返回不含运行时投影字段的规范任务快照。"""
    return _snapshot_task(task)


def cancel_scheduled_task_deliveries(
    db: Session,
    *,
    task: ScheduledTask,
    reason_type: str,
    safe_summary: str,
    now: datetime | None = None,
) -> SourceCancellationSummary:
    """按任务当前 revision 取消安全 leaf，并报告不可撤销中的记录。"""
    snapshot = snapshot_scheduled_task(task)
    return cancel_safe_deliveries_for_source(
        db,
        source_type=SOURCE_TYPE,
        source_id=str(snapshot.task_id),
        expected_source_revision=scheduled_task_source_revision(snapshot),
        reason_type=reason_type,
        safe_summary=safe_summary,
        now=now,
    )


def _load_claim_snapshot(
    raw: str,
    *,
    expected_sha256: str | None = None,
) -> ScheduledTaskSnapshot:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ScheduledTaskOutboundError("定时任务冻结快照无法解析") from exc
    if not isinstance(value, Mapping):
        raise ScheduledTaskOutboundError("定时任务冻结快照必须是对象")
    if _canonical_json(value) != raw:
        raise ScheduledTaskOutboundError("定时任务冻结快照不是 canonical JSON")
    if expected_sha256 is not None and _sha256(raw) != expected_sha256:
        raise ScheduledTaskOutboundError("定时任务冻结快照完整性校验失败")
    return ScheduledTaskSnapshot.from_mapping(value)


def _load_recovery_contract(
    run: OutboundRun,
    snapshot: ScheduledTaskSnapshot,
) -> tuple[dict[str, str], str]:
    raw = str(run.delivery_contract_json or "")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ScheduledTaskOutboundError("定时任务冻结投递合同无法解析") from exc
    if not isinstance(value, Mapping) or _canonical_json(value) != raw:
        raise ScheduledTaskOutboundError("定时任务冻结投递合同不是 canonical JSON")
    if _sha256(raw) != str(run.delivery_contract_sha256 or ""):
        raise ScheduledTaskOutboundError("定时任务冻结投递合同完整性校验失败")
    destination = value.get("destination_snapshot")
    if not isinstance(destination, Mapping):
        raise ScheduledTaskOutboundError("定时任务冻结投递目标无效")
    target_type = str(destination.get("target_type") or "").strip()
    target_id = str(destination.get("target_id") or "").strip()
    destination_fingerprint = str(
        value.get("destination_fingerprint") or ""
    ).strip()
    if (
        value.get("endpoint_key") != ENDPOINT_KEY
        or value.get("payload_contract_fingerprint") != PAYLOAD_CONTRACT
        or value.get("target_type") != target_type
        or target_type != snapshot.target_type
        or target_id != snapshot.target_id
        or destination_fingerprint
        != scheduled_task_destination_fingerprint(snapshot)
    ):
        raise ScheduledTaskOutboundError("定时任务冻结投递合同字段不一致")
    return {
        "target_type": target_type,
        "target_id": target_id,
    }, destination_fingerprint


def _occurrence(
    *,
    task_id: int,
    trigger_type: str,
    scheduled_for: datetime | None,
    manual_idempotency_key: str | None,
    now: datetime,
) -> ScheduledOccurrence:
    if trigger_type == "cron":
        if scheduled_for is None:
            raise ValueError("cron 执行必须提供 scheduled_for")
        slot = _utc_naive(scheduled_for).replace(second=0, microsecond=0)
        return ScheduledOccurrence(
            occurrence_key=(
                f"scheduled-task:{task_id}:cron:"
                f"{slot.strftime('%Y%m%dT%H%M%SZ')}"
            ),
            scheduled_for=slot,
        )
    if trigger_type == "manual":
        return scheduled_manual_occurrence(
            task_id=task_id,
            idempotency_key=str(manual_idempotency_key or ""),
            now=now,
        )
    raise ValueError("trigger_type 只支持 cron/manual")


def _existing_result(
    db: Session,
    run_id: int,
    *,
    deduplicated: bool = True,
    generation_attempted: bool = False,
) -> ScheduledTaskEnqueueResult:
    from core.database import OutboundDeliveryOutbox

    run = db.get(OutboundRun, int(run_id))
    if run is None:
        raise ScheduledTaskOutboundError("已登记的定时任务 run 不存在")
    outbox_id = int(run.active_outbox_id) if run.active_outbox_id else None
    status = str(run.status)
    if outbox_id is not None:
        outbox = db.get(OutboundDeliveryOutbox, outbox_id)
        if outbox is not None:
            status = "queued" if outbox.status == "pending" else str(outbox.status)
    return ScheduledTaskEnqueueResult(
        run_id=int(run.id),
        outbox_id=outbox_id,
        status=status,
        delivery_mode=str(run.delivery_mode),
        deduplicated=deduplicated,
        generation_attempted=generation_attempted,
    )


def _finish_read_transaction(db: Session) -> None:
    if db.in_transaction():
        db.rollback()


def _default_session_factory() -> Callable[[], Session]:
    from core import database

    return database.SessionLocal


async def _deliver_legacy_leaf(
    *,
    result: ScheduledTaskEnqueueResult,
    producer_config: ScheduledTaskProducerConfig,
    session_factory: Callable[[], Session] | None,
    transport: OutboundTransport | None,
    worker_config: OutboundWorkerConfig | None,
    fixed_now: datetime | None,
    clock: Callable[[], datetime] | None,
) -> None:
    if result.delivery_mode != "legacy_direct" or result.outbox_id is None:
        return
    factory = session_factory or _default_session_factory()

    async def deliver(
        resolved_transport: OutboundTransport,
        execution_config,
    ) -> None:
        await deliver_legacy_outbound_once(
            session_factory=factory,
            transport=resolved_transport,
            config=execution_config,
            outbox_id=result.outbox_id,
            worker_owner=producer_config.producer_owner,
            writer_owner=producer_config.producer_owner,
            writer_token=producer_config.writer_token,
            writer_protocol_version=OUTBOUND_PROTOCOL_VERSION,
            writer_lease_seconds=producer_config.writer_lease_seconds,
            now=fixed_now,
            clock=clock,
        )

    if transport is not None:
        execution_config = worker_config or CustomTransportConfig(
            endpoint_config_revision=(
                producer_config.endpoint_config_revision
            ),
        )
        await deliver(transport, execution_config)
        return

    resolved_worker_config = worker_config or OutboundWorkerConfig.from_env()

    import aiohttp

    from core.outbound_transport import deliver_qq_push_with_session

    async with aiohttp.ClientSession() as http_session:
        async def qq_transport(
            request: OutboundTransportRequest,
        ):
            return await deliver_qq_push_with_session(
                http_session,
                push_url=request.push_url,
                push_token=resolved_worker_config.push_token,
                target_type=request.target_type,
                target_id=request.target_id,
                message=request.message,
                timeout_seconds=request.timeout_seconds,
            )

        await deliver(qq_transport, resolved_worker_config)


async def _maybe_deliver_legacy(
    db: Session,
    *,
    result: ScheduledTaskEnqueueResult,
    producer_config: ScheduledTaskProducerConfig,
    session_factory: Callable[[], Session] | None,
    transport: OutboundTransport | None,
    worker_config: OutboundWorkerConfig | None,
    fixed_now: datetime | None,
    clock: Callable[[], datetime] | None,
) -> ScheduledTaskEnqueueResult:
    _finish_read_transaction(db)
    if transport is None:
        return result
    await _deliver_legacy_leaf(
        result=result,
        producer_config=producer_config,
        session_factory=session_factory,
        transport=transport,
        worker_config=worker_config,
        fixed_now=fixed_now,
        clock=clock,
    )
    refreshed = _existing_result(
        db,
        result.run_id,
        deduplicated=result.deduplicated,
        generation_attempted=result.generation_attempted,
    )
    _finish_read_transaction(db)
    return refreshed


async def drain_due_legacy_scheduled_task_outboxes(
    *,
    session_factory: Callable[[], Session] | None = None,
    producer_config: ScheduledTaskProducerConfig | None = None,
    worker_config: OutboundWorkerConfig | None = None,
    transport: OutboundTransport | None = None,
    now: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
    jitter: Callable[[float], float] | None = None,
    worker_owner: str | None = None,
    stop_event: Any | None = None,
    takeover_writer: LegacyWriterTakeover | None = None,
    limit: int | None = None,
) -> list[OutboundDeliveryWorkResult]:
    """自动投递安全到期的 legacy leaf，普通 worker 仍不领取它们。"""

    if now is not None and clock is not None:
        raise ValueError("now 与 clock 不能同时提供")
    factory = session_factory or _default_session_factory()
    resolved_worker = worker_config or OutboundWorkerConfig.from_env()
    batch_limit = resolved_worker.batch_size if limit is None else limit
    if type(batch_limit) is not int or batch_limit < 1 or batch_limit > 1000:
        raise ValueError("limit 必须是 1-1000 的整数")
    clock_source = clock or (lambda: datetime.now(timezone.utc))
    current = _utc_naive(now if now is not None else clock_source())
    discovery = factory()
    writer_snapshot: LegacyWriterLeaseSnapshot | None = None
    try:
        if producer_config is None:
            writer_snapshot = snapshot_live_legacy_writer(
                discovery,
                source_type=SOURCE_TYPE,
                now=current,
            )
        outbox_ids = [
            int(row[0])
            for row in (
                discovery.query(OutboundDeliveryOutbox.id)
                .join(
                    OutboundRun,
                    OutboundRun.id == OutboundDeliveryOutbox.run_id,
                )
                .filter(
                    OutboundRun.source_type == SOURCE_TYPE,
                    OutboundRun.delivery_mode == "legacy_direct",
                    OutboundRun.active_outbox_id == OutboundDeliveryOutbox.id,
                    OutboundDeliveryOutbox.endpoint_key == ENDPOINT_KEY,
                    OutboundDeliveryOutbox.status.in_((
                        "pending",
                        "retry_wait",
                        "blocked",
                    )),
                    OutboundDeliveryOutbox.request_started_count
                    < OutboundDeliveryOutbox.max_attempts,
                    OutboundDeliveryOutbox.retry_deadline_at > current,
                    or_(
                        OutboundDeliveryOutbox.status.in_((
                            "pending",
                            "blocked",
                        )),
                        (
                            (OutboundDeliveryOutbox.status == "retry_wait")
                            & (
                                OutboundDeliveryOutbox.next_attempt_at
                                <= current
                            )
                        ),
                    ),
                )
                .order_by(
                    OutboundDeliveryOutbox.next_attempt_at.asc(),
                    OutboundDeliveryOutbox.id.asc(),
                )
                .limit(batch_limit)
                .all()
            )
        ]
    finally:
        discovery.close()

    if producer_config is not None:
        writer_owner = producer_config.producer_owner
        writer_token = producer_config.writer_token
        writer_protocol_version = OUTBOUND_PROTOCOL_VERSION
        writer_lease_seconds = producer_config.writer_lease_seconds
        expected_writer_version = None
    elif writer_snapshot is not None:
        writer_owner = writer_snapshot.writer_owner
        writer_token = writer_snapshot.writer_token
        writer_protocol_version = writer_snapshot.protocol_version
        writer_lease_seconds = DEFAULT_WRITER_LEASE_SECONDS
        expected_writer_version = writer_snapshot.writer_version
    elif takeover_writer is not None:
        writer_owner = takeover_writer.writer_owner
        writer_token = takeover_writer.writer_token
        writer_protocol_version = OUTBOUND_PROTOCOL_VERSION
        writer_lease_seconds = DEFAULT_WRITER_LEASE_SECONDS
        expected_writer_version = None
    else:
        return []
    delivery_owner = str(worker_owner or writer_owner).strip()

    async def deliver_batch(
        resolved_transport: OutboundTransport,
    ) -> list[OutboundDeliveryWorkResult]:
        results: list[OutboundDeliveryWorkResult] = []
        for outbox_id in outbox_ids:
            if stop_event is not None and stop_event.is_set():
                break
            try:
                result = await deliver_legacy_outbound_once(
                    session_factory=factory,
                    transport=resolved_transport,
                    config=resolved_worker,
                    outbox_id=outbox_id,
                    worker_owner=delivery_owner,
                    writer_owner=writer_owner,
                    writer_token=writer_token,
                    writer_protocol_version=writer_protocol_version,
                    writer_lease_seconds=writer_lease_seconds,
                    expected_writer_version=expected_writer_version,
                    now=now,
                    clock=clock,
                    jitter=jitter,
                )
            except Exception as exc:
                logger.error(
                    "定时任务 legacy leaf 投递失败 outbox_id=%s "
                    "error_type=%s",
                    outbox_id,
                    type(exc).__name__,
                )
                continue
            if result is not None:
                results.append(result)
        return results

    if transport is not None:
        return await deliver_batch(transport)
    if not outbox_ids:
        return []

    import aiohttp

    from core.outbound_transport import deliver_qq_push_with_session

    async with aiohttp.ClientSession() as http_session:
        async def qq_transport(request: OutboundTransportRequest):
            return await deliver_qq_push_with_session(
                http_session,
                push_url=request.push_url,
                push_token=resolved_worker.push_token,
                target_type=request.target_type,
                target_id=request.target_id,
                message=request.message,
                timeout_seconds=request.timeout_seconds,
            )

        return await deliver_batch(qq_transport)


async def _generate(
    generator: TaskGenerator | None,
    snapshot: ScheduledTaskSnapshot,
) -> str | None:
    resolved = generator
    if resolved is None:
        from core.daily_digest import _generate_task_message

        resolved = _generate_task_message
    result = resolved(snapshot)
    if inspect.isawaitable(result):
        result = await result
    if result is None:
        return None
    normalized = str(result)
    return normalized if normalized.strip() else None


async def enqueue_scheduled_task_occurrence(
    db: Session,
    *,
    task_id: int,
    trigger_type: str,
    scheduled_for: datetime | None = None,
    manual_idempotency_key: str | None = None,
    config: ScheduledTaskProducerConfig | None = None,
    generator: TaskGenerator | None = None,
    session_factory: Callable[[], Session] | None = None,
    legacy_transport: OutboundTransport | None = None,
    legacy_worker_config: OutboundWorkerConfig | None = None,
    now: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
    _recovery_run_id: int | None = None,
) -> ScheduledTaskEnqueueResult:
    """领取一次定时任务 occurrence，生成正文并提交不可变 outbox。"""

    if now is not None and clock is not None:
        raise ValueError("now 与 clock 不能同时提供")
    resolved_config = config or ScheduledTaskProducerConfig.from_env()
    clock_source = clock or (
        (lambda: now) if now is not None else lambda: datetime.now(timezone.utc)
    )

    def current_time() -> datetime:
        return _utc_naive(clock_source())

    current = current_time()
    if _recovery_run_id is None:
        lock_outbound_source_control(
            db,
            source_type=SOURCE_TYPE,
            now=current,
        )
        task = db.get(ScheduledTask, int(task_id))
        if task is None:
            raise ScheduledTaskNotFoundError("定时任务不存在")
        snapshot = _snapshot_task(task)
        if trigger_type == "cron" and not snapshot.enabled:
            raise ScheduledTaskOutboundError("已禁用任务不能由 cron 执行")
        occurrence = _occurrence(
            task_id=snapshot.task_id,
            trigger_type=trigger_type,
            scheduled_for=scheduled_for,
            manual_idempotency_key=manual_idempotency_key,
            now=current,
        )
        if trigger_type == "cron":
            local_slot = occurrence.scheduled_for.replace(
                tzinfo=timezone.utc,
            ).astimezone(SHANGHAI)
            if not scheduled_cron_matches(snapshot.cron_expr, local_slot):
                raise ScheduledTaskOutboundError(
                    "定时任务当前槽已不再匹配最新 cron"
                )
            slot_age_seconds = (
                current - occurrence.scheduled_for
            ).total_seconds()
            if (
                slot_age_seconds < 0
                or slot_age_seconds > CRON_OCCURRENCE_LATE_CLAIM_GRACE_SECONDS
            ):
                raise ScheduledTaskOutboundError(
                    "cron occurrence 只能在到点后的补领窗口内创建"
                )
        destination_snapshot = {
            "target_type": snapshot.target_type,
            "target_id": snapshot.target_id,
        }
        destination_fingerprint = scheduled_task_destination_fingerprint(snapshot)
        source_revision = scheduled_task_source_revision(snapshot)
    else:
        recovery_run = db.get(OutboundRun, int(_recovery_run_id))
        if (
            recovery_run is None
            or recovery_run.source_type != SOURCE_TYPE
            or recovery_run.active_outbox_id is not None
            or recovery_run.status not in {"claimed", "generating"}
            or recovery_run.claim_expires_at is None
            or recovery_run.claim_expires_at > current
            or recovery_run.scheduled_for is None
        ):
            raise ScheduledTaskOutboundError("定时任务 run 当前不可恢复")
        snapshot = _load_claim_snapshot(
            str(recovery_run.source_snapshot_json),
            expected_sha256=str(recovery_run.source_snapshot_sha256),
        )
        if str(snapshot.task_id) != str(recovery_run.source_id):
            raise ScheduledTaskOutboundError("定时任务冻结来源不一致")
        trigger_type = str(recovery_run.trigger_type)
        if trigger_type not in {"cron", "manual"}:
            raise ScheduledTaskOutboundError("定时任务恢复触发类型无效")
        occurrence = ScheduledOccurrence(
            occurrence_key=str(recovery_run.occurrence_key),
            scheduled_for=_utc_naive(recovery_run.scheduled_for),
        )
        destination_snapshot, destination_fingerprint = _load_recovery_contract(
            recovery_run,
            snapshot,
        )
        source_revision = str(recovery_run.source_revision)
    try:
        claim = claim_outbound_run(
            db,
            source_type=SOURCE_TYPE,
            source_id=str(snapshot.task_id),
            occurrence_key=occurrence.occurrence_key,
            source_revision=source_revision,
            source_snapshot=snapshot.to_dict(),
            destination_snapshot=destination_snapshot,
            target_type=snapshot.target_type,
            task_kind="scheduled_task",
            scheduled_for=occurrence.scheduled_for,
            trigger_type=trigger_type,
            owner=resolved_config.producer_owner,
            claim_lease_seconds=resolved_config.claim_lease_seconds,
            writer_owner=resolved_config.producer_owner,
            writer_token=resolved_config.writer_token,
            writer_protocol_version=OUTBOUND_PROTOCOL_VERSION,
            writer_lease_seconds=resolved_config.writer_lease_seconds,
            endpoint_key=ENDPOINT_KEY,
            destination_fingerprint=destination_fingerprint,
            endpoint_config_revision=resolved_config.endpoint_config_revision,
            payload_contract_fingerprint=PAYLOAD_CONTRACT,
            now=current,
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise
    if not claim.acquired:
        existing = _existing_result(db, claim.run_id)
        return await _maybe_deliver_legacy(
            db,
            result=existing,
            producer_config=resolved_config,
            session_factory=session_factory,
            transport=legacy_transport,
            worker_config=legacy_worker_config,
            fixed_now=current if now is not None else None,
            clock=clock,
        )

    frozen = _load_claim_snapshot(
        claim.source_snapshot_json,
        expected_sha256=claim.source_snapshot_sha256,
    )
    generation_started_at = current_time()
    try:
        generation = start_generation_attempt(
            db,
            run_id=claim.run_id,
            owner=claim.owner,
            claim_token=claim.claim_token,
            writer_owner=resolved_config.producer_owner,
            writer_token=resolved_config.writer_token,
            writer_protocol_version=OUTBOUND_PROTOCOL_VERSION,
            endpoint_key=ENDPOINT_KEY,
            destination_fingerprint=destination_fingerprint,
            endpoint_config_revision=resolved_config.endpoint_config_revision,
            payload_contract_fingerprint=PAYLOAD_CONTRACT,
            now=generation_started_at,
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise
    if generation.status != "started" or generation.attempt_id is None:
        return ScheduledTaskEnqueueResult(
            run_id=claim.run_id,
            outbox_id=None,
            status=generation.status,
            delivery_mode=claim.delivery_mode,
            deduplicated=False,
            generation_attempted=False,
        )

    try:
        content = await _generate(generator, frozen)
    except Exception as exc:
        completed_at = current_time()
        failure_type = (
            "generation_timeout"
            if isinstance(exc, TimeoutError)
            else "generation_error"
        )
        try:
            fail_outbound_generation(
                db,
                run_id=claim.run_id,
                generation_attempt_id=generation.attempt_id,
                owner=claim.owner,
                claim_token=claim.claim_token,
                error_type=failure_type,
                error_summary=f"正文生成失败: {type(exc).__name__}",
                now=completed_at,
            )
            db.commit()
        except BaseException:
            db.rollback()
            raise
        return ScheduledTaskEnqueueResult(
            run_id=claim.run_id,
            outbox_id=None,
            status="failed",
            delivery_mode=claim.delivery_mode,
            deduplicated=False,
            generation_attempted=True,
        )
    if content is None:
        completed_at = current_time()
        try:
            fail_outbound_generation(
                db,
                run_id=claim.run_id,
                generation_attempt_id=generation.attempt_id,
                owner=claim.owner,
                claim_token=claim.claim_token,
                error_type="empty_generation",
                error_summary="模型没有生成可投递内容",
                now=completed_at,
            )
            db.commit()
        except BaseException:
            db.rollback()
            raise
        return ScheduledTaskEnqueueResult(
            run_id=claim.run_id,
            outbox_id=None,
            status="failed",
            delivery_mode=claim.delivery_mode,
            deduplicated=False,
            generation_attempted=True,
        )

    envelope = build_chat_response_envelope(
        status="ok",
        answer=content,
        meta={
            "platform": "qq",
            "chat_type": "scheduled_task",
            "task_id": frozen.task_id,
            "task_name": frozen.name,
            "target_type": frozen.target_type,
            "target_id": frozen.target_id,
        },
    )
    outbox_key = (
        f"scheduled-task:{frozen.task_id}:delivery:"
        f"{_sha256(occurrence.occurrence_key)}"
    )
    completed_at = current_time()
    try:
        queued = commit_generated_outbox(
            db,
            run_id=claim.run_id,
            generation_attempt_id=generation.attempt_id,
            owner=claim.owner,
            claim_token=claim.claim_token,
            idempotency_key=outbox_key,
            destination_snapshot=destination_snapshot,
            destination_fingerprint=destination_fingerprint,
            target_type=frozen.target_type,
            endpoint_key=ENDPOINT_KEY,
            payload=envelope,
            max_attempts=resolved_config.max_attempts,
            retry_deadline_at=(
                completed_at
                + timedelta(seconds=resolved_config.retry_deadline_seconds)
            ),
            endpoint_config_revision=resolved_config.endpoint_config_revision,
            payload_contract_fingerprint=PAYLOAD_CONTRACT,
            now=completed_at,
        )
        db.commit()
    except OutboundConflictError:
        db.rollback()
        raise
    except BaseException:
        db.rollback()
        raise
    result = ScheduledTaskEnqueueResult(
        run_id=queued.run_id,
        outbox_id=queued.outbox_id,
        status="queued" if queued.status == "pending" else queued.status,
        delivery_mode=claim.delivery_mode,
        deduplicated=not queued.created,
        generation_attempted=True,
    )
    return await _maybe_deliver_legacy(
        db,
        result=result,
        producer_config=resolved_config,
        session_factory=session_factory,
        transport=legacy_transport,
        worker_config=legacy_worker_config,
        fixed_now=completed_at if now is not None else None,
        clock=clock,
    )


async def recover_expired_scheduled_task_occurrences(
    *,
    session_factory: Callable[[], Session] | None = None,
    config: ScheduledTaskProducerConfig | None = None,
    generator: TaskGenerator | None = None,
    legacy_transport: OutboundTransport | None = None,
    legacy_worker_config: OutboundWorkerConfig | None = None,
    now: datetime | None = None,
    limit: int = 100,
) -> list[ScheduledTaskEnqueueResult]:
    """恢复生成租约过期且尚未形成 outbox 的定时任务 run。"""
    if type(limit) is not int or limit < 1 or limit > 1000:
        raise ValueError("limit 必须是 1-1000 的整数")
    factory = session_factory or _default_session_factory()
    current = _utc_naive(now)
    discovery = factory()
    try:
        candidates = (
            discovery.query(OutboundRun.id, OutboundRun.source_id)
            .filter(
                OutboundRun.source_type == SOURCE_TYPE,
                OutboundRun.status.in_(("claimed", "generating")),
                OutboundRun.active_outbox_id.is_(None),
                OutboundRun.claim_expires_at.is_not(None),
                OutboundRun.claim_expires_at <= current,
                ~exists().where(
                    OutboundDeliveryOutbox.run_id == OutboundRun.id
                ),
            )
            .order_by(
                OutboundRun.claim_expires_at.asc(),
                OutboundRun.id.asc(),
            )
            .limit(limit)
            .all()
        )
    finally:
        discovery.close()

    results: list[ScheduledTaskEnqueueResult] = []
    for run_id, source_id in candidates:
        db = factory()
        try:
            run = db.get(OutboundRun, int(run_id))
            try:
                if (
                    run is None
                    or run.task_kind != "scheduled_task"
                    or run.scheduled_for is None
                    or run.trigger_type not in {"cron", "manual"}
                ):
                    raise ScheduledTaskOutboundError(
                        "定时任务恢复类型无效"
                    )
                snapshot = _load_claim_snapshot(
                    str(run.source_snapshot_json),
                    expected_sha256=str(run.source_snapshot_sha256),
                )
                if str(snapshot.task_id) != str(run.source_id):
                    raise ScheduledTaskOutboundError(
                        "定时任务冻结来源不一致"
                    )
                _load_recovery_contract(run, snapshot)
            except ScheduledTaskOutboundError:
                quarantined = quarantine_expired_generation_run(
                    db,
                    run_id=int(run_id),
                    expected_source_type=SOURCE_TYPE,
                    target_status="failed",
                    reason_type="recovery_state_invalid",
                    safe_summary="定时任务冻结恢复事实无效",
                    now=current,
                )
                if quarantined:
                    db.commit()
                else:
                    db.rollback()
                continue
            result = await enqueue_scheduled_task_occurrence(
                db,
                task_id=int(source_id),
                trigger_type="manual",
                config=config,
                generator=generator,
                session_factory=factory,
                legacy_transport=legacy_transport,
                legacy_worker_config=legacy_worker_config,
                now=now,
                _recovery_run_id=int(run_id),
            )
            results.append(result)
        except ScheduledTaskOutboundError as exc:
            db.rollback()
            quarantined = quarantine_expired_generation_run(
                db,
                run_id=int(run_id),
                expected_source_type=SOURCE_TYPE,
                target_status="failed",
                reason_type="recovery_state_invalid",
                safe_summary="定时任务冻结恢复事实无效",
                now=current,
            )
            if quarantined:
                db.commit()
            else:
                db.rollback()
            logger.warning(
                "Scheduled task recovery rejected run_id=%s error_type=%s",
                run_id,
                type(exc).__name__,
            )
            continue
        except Exception as exc:
            db.rollback()
            logger.error(
                "Scheduled task recovery failed run_id=%s error_type=%s",
                run_id,
                type(exc).__name__,
            )
        finally:
            db.close()
    return results
