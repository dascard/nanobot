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

from croniter import croniter
from sqlalchemy import exists, or_
from sqlalchemy.orm import Session

from core.db.models.outbound import OutboundDeliveryOutbox, OutboundRun
from core.db.models.scheduling import ScheduledTask
from core.schedule_spec import (
    KIND_CRON,
    KIND_INTERVAL,
    KIND_ONCE,
    grace_seconds,
    once_run_at_utc,
    spec_from_fields,
)
from core.message_envelope import is_html_reply
from core.message_transport_adapters import render_chat_json
from core.outbound.contracts import (
    OUTBOUND_PROTOCOL_VERSION,
    OutboundConflictError,
    SourceCancellationSummary,
)
from core.outbound.control import lock_outbound_source_control
from core.outbound.delivery_claims import cancel_safe_deliveries_for_source
from core.outbound.generation import (
    commit_generated_outbox,
    fail_outbound_generation,
)
from core.outbound.run_claims import (
    claim_outbound_run,
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
from foundation.identity import RecipientIdentity
from foundation.message_contract import (
    MessageAction,
    OutboundMessageContract,
    TextContent,
    TextFormat,
)


SOURCE_TYPE = "scheduled_task"
ENDPOINT_KEY = "qq_push"
PAYLOAD_CONTRACT = "qq-envelope-v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")
DEFAULT_CLAIM_LEASE_SECONDS = 900.0
DEFAULT_WRITER_LEASE_SECONDS = 900.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_DEADLINE_SECONDS = 86400.0
_PROCESS_OWNER = (
    f"scheduled-task:{socket.gethostname().strip() or 'host'}:{uuid4().hex}"
)[:128]
_PROCESS_WRITER_TOKEN = secrets.token_hex(32)
logger = logging.getLogger("nanobot.scheduled_task_outbound")


class ScheduledTaskOutboundError(RuntimeError):
    """定时任务 producer 的可诊断错误。"""


class ScheduledTaskNotFoundError(ScheduledTaskOutboundError):
    """目标定时任务不存在。"""


class ScheduledTaskGenerationError(ScheduledTaskOutboundError):
    """保留失败类型与已分配 Agent Trace，不暴露异常正文。"""

    def __init__(self, cause: Exception, *, model_trace_id: str = ""):
        super().__init__("定时任务正文生成失败")
        self.cause = cause
        self.model_trace_id = str(model_trace_id or "")[:128]


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
    schedule_kind: str
    schedule_spec: str
    timezone: str
    target_type: str
    target_id: str
    prompt_template: str
    program: dict[str, Any]
    program_sha256: str
    owner_chat_stream_id: str
    owner_platform: str
    owner_chat_type: str
    owner_session_id: str
    created_by_actor_id: str
    definition_version: int
    enabled: bool

    @property
    def id(self) -> int:
        """兼容现有定时任务生成器读取 ORM 风格主键。"""
        return self.task_id

    def to_dict(self) -> dict[str, Any]:
        if self.schema_version == 1:
            return {
                "schema_version": 1,
                "task_id": self.task_id,
                "name": self.name,
                "cron_expr": self.cron_expr,
                "target_type": self.target_type,
                "target_id": self.target_id,
                "prompt_template": self.prompt_template,
                "enabled": self.enabled,
            }
        base = {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "name": self.name,
            "cron_expr": self.cron_expr,
            "schedule_kind": self.schedule_kind,
            "schedule_spec": self.schedule_spec,
            "timezone": self.timezone,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "prompt_template": self.prompt_template,
            "owner_chat_stream_id": self.owner_chat_stream_id,
            "owner_platform": self.owner_platform,
            "owner_chat_type": self.owner_chat_type,
            "owner_session_id": self.owner_session_id,
            "created_by_actor_id": self.created_by_actor_id,
            "definition_version": self.definition_version,
            "enabled": self.enabled,
        }
        if self.schema_version >= 3:
            base["program"] = self.program
            base["program_sha256"] = self.program_sha256
        return base

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ScheduledTaskSnapshot":
        schema_version = int(value.get("schema_version") or 0)
        if schema_version not in {1, 2, 3}:
            raise ScheduledTaskOutboundError("定时任务冻结快照版本不受支持")
        task_id = value.get("task_id")
        if type(task_id) is not int or task_id <= 0:
            raise ScheduledTaskOutboundError("定时任务冻结快照缺少 task_id")
        target_type = str(value.get("target_type") or "").strip()
        target_id = str(value.get("target_id") or "").strip()
        if target_type not in {"private", "group"} or not target_id:
            raise ScheduledTaskOutboundError("定时任务冻结目标无效")
        from core.scheduled_task_contract import (
            SCHEDULED_TASK_TIMEZONE,
            ScheduledTaskContractError,
            ensure_task_target_matches_owner,
            normalize_scheduled_task_definition,
            scheduled_task_owner_from_persisted,
            scheduled_task_owner_from_target,
        )

        try:
            (
                name,
                prompt_template,
                program,
                _program_json,
                program_sha256,
            ) = normalize_scheduled_task_definition(
                name=value.get("name"),
                prompt_template=value.get("prompt_template"),
                program=(
                    value.get("program")
                    if schema_version >= 3
                    else None
                ),
            )
            if schema_version >= 3:
                expected_program_sha256 = str(
                    value.get("program_sha256") or ""
                ).strip().lower()
                if expected_program_sha256 != program_sha256:
                    raise ScheduledTaskContractError(
                        "定时任务 program 完整性校验失败"
                    )
            if schema_version == 1:
                owner = scheduled_task_owner_from_target(
                    target_type=target_type,
                    target_id=target_id,
                    platform="qq",
                    created_by_actor_id=(
                        target_id if target_type == "private" else ""
                    ),
                )
                schedule_kind = KIND_CRON
                schedule_spec = ""
                timezone_name = SCHEDULED_TASK_TIMEZONE
                definition_version = 1
            else:
                owner = scheduled_task_owner_from_persisted(
                    chat_stream_id=value.get("owner_chat_stream_id"),
                    platform=value.get("owner_platform"),
                    chat_type=value.get("owner_chat_type"),
                    session_id=value.get("owner_session_id"),
                    created_by_actor_id=value.get(
                        "created_by_actor_id"
                    ),
                )
                ensure_task_target_matches_owner(
                    owner,
                    target_type=target_type,
                    target_id=target_id,
                )
                schedule_kind = str(
                    value.get("schedule_kind") or ""
                ).strip()
                schedule_spec = str(
                    value.get("schedule_spec") or ""
                )
                timezone_name = str(
                    value.get("timezone") or ""
                ).strip()
                if timezone_name != SCHEDULED_TASK_TIMEZONE:
                    raise ScheduledTaskContractError(
                        "定时任务冻结时区无效"
                    )
                if spec_from_fields(
                    schedule_kind,
                    schedule_spec,
                    value.get("cron_expr"),
                ) is None:
                    raise ScheduledTaskContractError(
                        "定时任务冻结 trigger 无法解析"
                    )
                definition_version = int(
                    value.get("definition_version") or 0
                )
                if definition_version < 1:
                    raise ScheduledTaskContractError(
                        "定时任务 definition_version 无效"
                    )
        except (ScheduledTaskContractError, TypeError, ValueError) as exc:
            raise ScheduledTaskOutboundError(
                f"定时任务冻结定义无效: {exc}"
            ) from exc
        return cls(
            schema_version=schema_version,
            task_id=task_id,
            name=name,
            cron_expr=str(value.get("cron_expr") or "").strip(),
            schedule_kind=schedule_kind,
            schedule_spec=schedule_spec,
            timezone=timezone_name,
            target_type=target_type,
            target_id=target_id,
            prompt_template=prompt_template,
            program=program,
            program_sha256=program_sha256,
            owner_chat_stream_id=owner.chat_stream_id,
            owner_platform=owner.platform,
            owner_chat_type=owner.chat_type,
            owner_session_id=owner.session_id,
            created_by_actor_id=owner.created_by_actor_id,
            definition_version=definition_version,
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


@dataclass(frozen=True, slots=True)
class ScheduledTaskGeneratedContent:
    """任务生成正文及其 Agent Trace 关联。"""

    content: str | None
    model_trace_id: str = ""


TaskGenerator = Callable[
    [ScheduledTaskSnapshot],
    (
        str
        | None
        | ScheduledTaskGeneratedContent
        | Awaitable[str | None | ScheduledTaskGeneratedContent]
    ),
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


def scheduled_cron_matches(cron_expr: str, local_time: datetime) -> bool:
    """按标准五段 cron 语义(croniter,0=周日)判断上海本地时间。"""

    try:
        localized = (
            local_time.replace(tzinfo=SHANGHAI)
            if local_time.tzinfo is None
            else local_time.astimezone(SHANGHAI)
        )
        parts = str(cron_expr or "").strip().split()
        if len(parts) != 5:
            return False
        return bool(croniter.match(str(cron_expr).strip(), localized))
    except (TypeError, ValueError):
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
    if bool(getattr(task, "owner_migration_required", 1)):
        raise ScheduledTaskOutboundError("定时任务 owner 尚未安全迁移")
    raw_program_text = str(getattr(task, "program_json", "") or "")
    raw_program_sha256 = str(
        getattr(task, "program_sha256", "") or ""
    )
    if not raw_program_text:
        # 只兼容尚未经过迁移的有效 legacy 对象（主要是滚动发布窗口和
        # in-memory 测试）；生产迁移会持久化同一份 canonical program。
        from core.scheduled_task_contract import (
            ScheduledTaskContractError,
            normalize_scheduled_task_definition,
        )

        try:
            (
                _name,
                _prompt,
                raw_program,
                _program_json,
                raw_program_sha256,
            ) = normalize_scheduled_task_definition(
                name=task.name,
                prompt_template=task.prompt_template,
            )
        except ScheduledTaskContractError as exc:
            raise ScheduledTaskOutboundError(
                "定时任务 program 无法安全迁移"
            ) from exc
    else:
        try:
            raw_program = json.loads(raw_program_text)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ScheduledTaskOutboundError(
                "定时任务 program 无法解析"
            ) from exc
    return ScheduledTaskSnapshot.from_mapping({
        "schema_version": 3,
        "task_id": int(task.id),
        "name": task.name,
        "cron_expr": task.cron_expr,
        "schedule_kind": task.schedule_kind,
        "schedule_spec": task.schedule_spec,
        "timezone": "Asia/Shanghai",
        "target_type": task.target_type,
        "target_id": task.target_id,
        "prompt_template": task.prompt_template,
        "program": raw_program,
        "program_sha256": raw_program_sha256,
        "owner_chat_stream_id": task.owner_chat_stream_id,
        "owner_platform": task.owner_platform,
        "owner_chat_type": task.owner_chat_type,
        "owner_session_id": task.owner_session_id,
        "created_by_actor_id": task.created_by_actor_id,
        "definition_version": task.definition_version,
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
    schedule_kind: str = KIND_CRON,
) -> ScheduledOccurrence:
    if trigger_type == "cron":
        if scheduled_for is None:
            raise ValueError("cron 执行必须提供 scheduled_for")
        slot = _utc_naive(scheduled_for).replace(second=0, microsecond=0)
        key_kind = (
            schedule_kind
            if schedule_kind in {KIND_ONCE, KIND_INTERVAL}
            else KIND_CRON
        )
        return ScheduledOccurrence(
            occurrence_key=(
                f"scheduled-task:{task_id}:{key_kind}:"
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


def _validate_schedule_slot(
    schedule: dict,
    *,
    slot: datetime,
    now: datetime,
) -> None:
    """校验 schedule 驱动的 occurrence:定义仍匹配 + 在补领窗口内。

    宽限窗口自适应(周期一半,钳制 [2min, 2h]),高频任务保持
    与旧 120s 常量一致的下限。interval 槽由调度器状态推进,无法
    静态复验,只做窗口校验。
    """

    kind = schedule.get("kind")
    if kind == KIND_CRON:
        local_slot = slot.replace(
            tzinfo=timezone.utc,
        ).astimezone(SHANGHAI)
        if not scheduled_cron_matches(
            str(schedule.get("expr") or ""),
            local_slot,
        ):
            raise ScheduledTaskOutboundError(
                "定时任务当前槽已不再匹配最新 cron"
            )
    elif kind == KIND_ONCE:
        run_at = once_run_at_utc(schedule)
        expected = (
            run_at.replace(second=0, microsecond=0)
            if run_at is not None
            else None
        )
        if expected is None or expected != slot:
            raise ScheduledTaskOutboundError(
                "once 任务槽与计划触发时刻不符"
            )
    slot_age_seconds = (now - slot).total_seconds()
    if (
        slot_age_seconds < 0
        or slot_age_seconds > grace_seconds(schedule, base_utc=slot)
    ):
        raise ScheduledTaskOutboundError(
            "cron occurrence 只能在到点后的补领窗口内创建"
        )


def _existing_result(
    db: Session,
    run_id: int,
    *,
    deduplicated: bool = True,
    generation_attempted: bool = False,
) -> ScheduledTaskEnqueueResult:
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
) -> ScheduledTaskGeneratedContent:
    resolved = generator
    generated_trace_id = ""
    if resolved is None:
        from core.daily_digest import _generate_task_message
        from core.tracing import new_trace_id

        resolved = _generate_task_message
        generated_trace_id = new_trace_id()
    try:
        parameters = inspect.signature(resolved).parameters
        if generated_trace_id and "trace_id" in parameters:
            result = resolved(snapshot, trace_id=generated_trace_id)
        else:
            result = resolved(snapshot)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:
        raise ScheduledTaskGenerationError(
            exc,
            model_trace_id=generated_trace_id,
        ) from exc
    if isinstance(result, ScheduledTaskGeneratedContent):
        normalized = (
            str(result.content)
            if result.content is not None
            else ""
        )
        return ScheduledTaskGeneratedContent(
            content=normalized if normalized.strip() else None,
            model_trace_id=str(
                result.model_trace_id or generated_trace_id
            )[:128],
        )
    normalized = str(result) if result is not None else ""
    return ScheduledTaskGeneratedContent(
        content=normalized if normalized.strip() else None,
        model_trace_id=generated_trace_id[:128],
    )


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
    expected_owner_chat_stream_id: str | None = None,
    _frozen_snapshot: ScheduledTaskSnapshot | None = None,
    _frozen_occurrence: ScheduledOccurrence | None = None,
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
    if _recovery_run_id is not None and (
        _frozen_snapshot is not None or _frozen_occurrence is not None
    ):
        raise ValueError("恢复 run 与冻结 execution 输入不能同时提供")
    if (_frozen_snapshot is None) != (_frozen_occurrence is None):
        raise ValueError("冻结 snapshot 与 occurrence 必须同时提供")

    if _recovery_run_id is None:
        lock_outbound_source_control(
            db,
            source_type=SOURCE_TYPE,
            now=current,
        )
        if _frozen_snapshot is not None:
            # 重新解析一遍不可变 wire 快照，不能信任调用方传入的可变 dict。
            snapshot = ScheduledTaskSnapshot.from_mapping(
                _frozen_snapshot.to_dict()
            )
            occurrence = ScheduledOccurrence(
                occurrence_key=str(_frozen_occurrence.occurrence_key),
                scheduled_for=_utc_naive(_frozen_occurrence.scheduled_for),
            )
            if snapshot.task_id != int(task_id):
                raise ScheduledTaskOutboundError(
                    "冻结 execution 的 task_id 不一致"
                )
            if trigger_type not in {"cron", "manual"}:
                raise ScheduledTaskOutboundError(
                    "冻结 execution 的触发类型无效"
                )
        else:
            task = db.get(ScheduledTask, int(task_id))
            if task is None:
                raise ScheduledTaskNotFoundError("定时任务不存在")
            expected_owner = str(
                expected_owner_chat_stream_id or ""
            ).strip()
            if (
                expected_owner
                and str(task.owner_chat_stream_id or "").strip()
                != expected_owner
            ):
                # 对普通 Agent 隐藏跨 owner 任务是否存在。
                raise ScheduledTaskNotFoundError("定时任务不存在")
            snapshot = _snapshot_task(task)
            if trigger_type == "cron" and not snapshot.enabled:
                raise ScheduledTaskOutboundError(
                    "已禁用任务不能由 cron 执行"
                )
            schedule = spec_from_fields(
                getattr(task, "schedule_kind", None),
                getattr(task, "schedule_spec", None),
                task.cron_expr,
            )
            occurrence = _occurrence(
                task_id=snapshot.task_id,
                trigger_type=trigger_type,
                scheduled_for=scheduled_for,
                manual_idempotency_key=manual_idempotency_key,
                now=current,
                schedule_kind=(schedule or {}).get("kind", KIND_CRON),
            )
            if trigger_type == "cron":
                if schedule is None:
                    raise ScheduledTaskOutboundError(
                        "定时任务 schedule 无法解析"
                    )
                _validate_schedule_slot(
                    schedule,
                    slot=occurrence.scheduled_for,
                    now=current,
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
        generated = await _generate(generator, frozen)
    except Exception as exc:
        failure_cause = (
            exc.cause
            if isinstance(exc, ScheduledTaskGenerationError)
            else exc
        )
        model_trace_id = (
            exc.model_trace_id
            if isinstance(exc, ScheduledTaskGenerationError)
            else ""
        )
        completed_at = current_time()
        failure_type = (
            "generation_timeout"
            if isinstance(failure_cause, TimeoutError)
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
                error_summary=(
                    "正文生成失败: "
                    f"{type(failure_cause).__name__}"
                ),
                model_trace_id=model_trace_id,
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
    content = generated.content
    model_trace_id = generated.model_trace_id
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
                model_trace_id=model_trace_id,
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

    outbound_message = OutboundMessageContract(
        action=MessageAction.REPLY,
        recipient=RecipientIdentity(
            platform="qq",
            recipient_type=(
                "group" if frozen.target_type == "group" else "user"
            ),
            recipient_id=frozen.target_id,
        ),
        parts=(
            TextContent(
                content,
                format=(
                    TextFormat.HTML
                    if is_html_reply(content)
                    else TextFormat.PLAIN
                ),
            ),
        ),
    )
    envelope = render_chat_json(
        outbound_message,
        status="ok",
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
            model_trace_id=model_trace_id,
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


async def enqueue_frozen_scheduled_task_content(
    db: Session,
    *,
    snapshot: ScheduledTaskSnapshot,
    occurrence: ScheduledOccurrence,
    content: str,
    model_trace_id: str = "",
    config: ScheduledTaskProducerConfig | None = None,
    session_factory: Callable[[], Session] | None = None,
    legacy_transport: OutboundTransport | None = None,
    legacy_worker_config: OutboundWorkerConfig | None = None,
    now: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
    trigger_type: str = "cron",
) -> ScheduledTaskEnqueueResult:
    """把 workflow 已生成的内容按冻结任务事实提交到现有 outbox。"""

    normalized_content = str(content or "")
    if not normalized_content.strip():
        raise ValueError("冻结任务投递内容不能为空")
    generated = ScheduledTaskGeneratedContent(
        content=normalized_content,
        model_trace_id=str(model_trace_id or "")[:128],
    )
    return await enqueue_scheduled_task_occurrence(
        db,
        task_id=snapshot.task_id,
        trigger_type=trigger_type,
        scheduled_for=occurrence.scheduled_for,
        manual_idempotency_key=(
            occurrence.occurrence_key
            if trigger_type == "manual"
            else None
        ),
        config=config,
        generator=lambda _snapshot: generated,
        session_factory=session_factory,
        legacy_transport=legacy_transport,
        legacy_worker_config=legacy_worker_config,
        now=now,
        clock=clock,
        _frozen_snapshot=snapshot,
        _frozen_occurrence=occurrence,
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
