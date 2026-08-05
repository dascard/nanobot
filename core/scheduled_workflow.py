"""统一定时任务程序的持久执行器。

调度器只冻结 occurrence 并创建 ``ScheduledTaskExecution``。本模块的 worker
使用租约和 fencing token 逐步执行 program；每个外部步骤先持久化 started
尝试，成功后再原子写入 checkpoint。无法判断副作用是否发生时进入
``ambiguous``，不会从头重跑。
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import math
import secrets
import socket
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.db.models.scheduling import (
    ScheduledTask,
    ScheduledTaskExecution,
    ScheduledTaskOwnerLease,
    ScheduledTaskStepAttempt,
)
from core.schedule_spec import KIND_CRON, spec_from_fields
from core.scheduled_task_contract import (
    MAX_SCHEDULED_TASK_PROMPT_CHARS,
    MAX_SCHEDULED_TASK_WAIT_SECONDS,
    normalize_scheduled_task_program,
)
from core.scheduled_task_outbound import (
    ScheduledOccurrence,
    ScheduledTaskNotFoundError,
    ScheduledTaskOutboundError,
    ScheduledTaskSnapshot,
    scheduled_manual_occurrence,
    snapshot_scheduled_task,
)
from core.agent_runtime.contracts import RuntimeOwnerType, RuntimePrincipal
from core.trigger_runtime import (
    TriggerContractError,
    TriggerEnvelope,
    TriggerKind,
    build_trigger_envelope,
)


DEFAULT_WORKFLOW_LEASE_SECONDS = 900.0
DEFAULT_WORKFLOW_CONCURRENCY = 4
MAX_WORKFLOW_STATE_BYTES = 256 * 1024
MAX_WORKFLOW_OUTPUT_BYTES = 128 * 1024
_TERMINAL_STATUSES = frozenset(
    {"succeeded", "failed", "blocked", "ambiguous"}
)
_SAFE_RECOVERY_OPERATIONS = frozenset(
    {"set", "branch", "loop", "wait", "emit"}
)
_PROCESS_OWNER = (
    f"scheduled-workflow:{socket.gethostname().strip() or 'host'}:"
    f"{uuid4().hex}"
)[:128]
logger = logging.getLogger("nanobot.scheduled_workflow")


class ScheduledWorkflowError(RuntimeError):
    """统一任务执行器的可诊断错误。"""


class ScheduledWorkflowFencingError(ScheduledWorkflowError):
    """worker 的执行租约或 fencing token 已失效。"""


class ScheduledWorkflowStateError(ScheduledWorkflowError):
    """持久状态、快照或 cursor 不满足恢复合同。"""


class ScheduledWorkflowLoopLimitError(ScheduledWorkflowStateError):
    """条件循环到达显式上限后仍未结束。"""


@dataclass(frozen=True, slots=True)
class ScheduledExecutionEnqueueResult:
    execution_id: int
    status: str
    deduplicated: bool
    occurrence_key: str


@dataclass(frozen=True, slots=True)
class ScheduledExecutionClaim:
    execution_id: int
    owner_chat_stream_id: str
    owner: str
    lease_token: str
    lease_expires_at: datetime
    generation: int = 1
    attempt_no: int = 1


@dataclass(frozen=True, slots=True)
class ScheduledWorkflowContext:
    execution_id: int
    task_snapshot: ScheduledTaskSnapshot
    occurrence: ScheduledOccurrence
    trigger_type: str
    runtime_step_id: str
    static_step_id: str
    trigger_envelope: TriggerEnvelope | None = None
    model_tool_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScheduledWorkflowStepOutcome:
    output: Any = None
    tool_call_id: str = ""
    model_trace_id: str = ""
    agent_run_id: str = ""
    outbound_run_id: int | None = None
    error_code: str = ""
    error_summary: str = ""
    retryable: bool = False
    ambiguous: bool = False
    blocked: bool = False
    stop: bool = False

    @property
    def success(self) -> bool:
        return not self.error_code

    @classmethod
    def failed(
        cls,
        code: str,
        summary: str,
        *,
        retryable: bool = False,
        ambiguous: bool = False,
        blocked: bool = False,
        stop: bool = False,
        tool_call_id: str = "",
        model_trace_id: str = "",
        agent_run_id: str = "",
    ) -> "ScheduledWorkflowStepOutcome":
        return cls(
            tool_call_id=str(tool_call_id or "")[:128],
            model_trace_id=str(model_trace_id or "")[:128],
            agent_run_id=str(agent_run_id or "")[:128],
            error_code=str(code or "step_error")[:128],
            error_summary=str(summary or "任务步骤执行失败")[:1000],
            retryable=bool(retryable),
            ambiguous=bool(ambiguous),
            blocked=bool(blocked),
            stop=bool(stop),
        )


class ScheduledWorkflowCallbacks(Protocol):
    async def execute_tool(
        self,
        context: ScheduledWorkflowContext,
        *,
        tool_name: str,
        args: dict[str, Any],
        idempotency_key: str,
    ) -> ScheduledWorkflowStepOutcome: ...

    async def execute_model(
        self,
        context: ScheduledWorkflowContext,
        *,
        prompt: str,
        idempotency_key: str,
    ) -> ScheduledWorkflowStepOutcome: ...

    async def emit(
        self,
        context: ScheduledWorkflowContext,
        *,
        content: str,
        idempotency_key: str,
        model_trace_id: str,
    ) -> ScheduledWorkflowStepOutcome: ...


@dataclass(frozen=True, slots=True)
class ScheduledWorkflowWorkerResult:
    claimed: int
    succeeded: int
    waiting: int
    failed: int
    blocked: int
    ambiguous: int


def _utc_naive(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current
    return current.astimezone(timezone.utc).replace(tzinfo=None)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ScheduledWorkflowStateError(
            "任务运行值必须是可序列化的有限 JSON"
        ) from exc


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _bounded_json(value: Any, *, max_bytes: int, label: str) -> str:
    raw = _canonical_json(value)
    if len(raw.encode("utf-8")) > max_bytes:
        raise ScheduledWorkflowStateError(
            f"{label} 超过 {max_bytes} 字节上限"
        )
    return raw


def _load_canonical_mapping(
    raw: object,
    *,
    label: str,
    expected_sha256: str = "",
) -> dict[str, Any]:
    text_value = str(raw or "")
    try:
        parsed = json.loads(text_value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ScheduledWorkflowStateError(f"{label} 无法解析") from exc
    if not isinstance(parsed, dict) or _canonical_json(parsed) != text_value:
        raise ScheduledWorkflowStateError(f"{label} 不是 canonical JSON 对象")
    digest = str(expected_sha256 or "").strip().lower()
    if digest and hashlib.sha256(text_value.encode("utf-8")).hexdigest() != digest:
        raise ScheduledWorkflowStateError(f"{label} 完整性校验失败")
    return parsed


def _task_occurrence(
    task: ScheduledTaskSnapshot,
    *,
    trigger_type: str,
    scheduled_for: datetime | None,
    manual_idempotency_key: str | None,
    now: datetime,
) -> ScheduledOccurrence:
    if trigger_type == "manual":
        return scheduled_manual_occurrence(
            task_id=task.task_id,
            idempotency_key=str(manual_idempotency_key or ""),
            now=now,
        )
    if trigger_type != "scheduled":
        raise ValueError("trigger_type 只支持 scheduled/manual")
    if scheduled_for is None:
        raise ValueError("scheduled 触发必须提供 scheduled_for")
    slot = _utc_naive(scheduled_for).replace(microsecond=0)
    kind = str(task.schedule_kind or KIND_CRON)
    return ScheduledOccurrence(
        occurrence_key=(
            f"scheduled-task:{task.task_id}:{kind}:"
            f"{slot.strftime('%Y%m%dT%H%M%SZ')}"
        ),
        scheduled_for=slot,
    )


def _step_ids(steps: Sequence[Mapping[str, Any]]) -> list[str]:
    return [str(step["id"]) for step in steps]


def _initial_state(program: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "variables": {},
        "steps": {},
        "frames": [
            {
                "kind": "steps",
                "step_ids": _step_ids(program["steps"]),
                "index": 0,
            }
        ],
        "waits": {},
        "step_count": 0,
        "loop_iterations": 0,
    }


def _owner_snapshot(snapshot: ScheduledTaskSnapshot) -> dict[str, Any]:
    return {
        "chat_stream_id": snapshot.owner_chat_stream_id,
        "platform": snapshot.owner_platform,
        "chat_type": snapshot.owner_chat_type,
        "session_id": snapshot.owner_session_id,
        "created_by_actor_id": snapshot.created_by_actor_id,
        "target_type": snapshot.target_type,
        "target_id": snapshot.target_id,
    }


def _trigger_snapshot(
    snapshot: ScheduledTaskSnapshot,
    *,
    trigger_type: str,
    scheduled_for: datetime,
    occurrence_key: str,
    program: Mapping[str, Any],
    model_tool_names: Sequence[str],
) -> dict[str, Any]:
    spec = spec_from_fields(
        snapshot.schedule_kind,
        snapshot.schedule_spec,
        snapshot.cron_expr,
    )
    if spec is None:
        raise ScheduledWorkflowStateError("任务 trigger 无法解析")
    envelope = _scheduled_trigger_envelope(
        snapshot,
        trigger_type=trigger_type,
        scheduled_for=scheduled_for,
        occurrence_key=occurrence_key,
        program=program,
        model_tool_names=model_tool_names,
    )
    return {
        "trigger_type": trigger_type,
        "kind": snapshot.schedule_kind,
        "spec": spec,
        "timezone": snapshot.timezone,
        "scheduled_for": scheduled_for.isoformat(timespec="seconds"),
        "model_tool_names": list(model_tool_names),
        "envelope": envelope.to_dict(),
    }


def _program_trigger_capabilities(
    steps: Sequence[Mapping[str, Any]],
) -> tuple[tuple[str, ...], bool, bool]:
    tools: set[str] = set()
    has_emit = False
    has_model = False

    def visit(items: Sequence[Mapping[str, Any]]) -> None:
        nonlocal has_emit, has_model
        for step in items:
            operation = str(step.get("op") or "")
            if operation == "tool":
                tool_name = str(step.get("tool") or "").strip()
                if tool_name:
                    tools.add(tool_name)
            elif operation == "emit":
                has_emit = True
            elif operation == "model":
                has_model = True
            nested = step.get("steps")
            if isinstance(nested, list):
                visit([item for item in nested if isinstance(item, Mapping)])
            for branch_key in ("then", "else"):
                branch = step.get(branch_key)
                if isinstance(branch, list):
                    visit([item for item in branch if isinstance(item, Mapping)])

    visit(steps)
    return tuple(sorted(tools)), has_emit, has_model


def _scheduled_model_tool_names(
    db: Session,
    snapshot: ScheduledTaskSnapshot,
    program: Mapping[str, Any],
) -> tuple[str, ...]:
    """按 execution 入队时的现有 ToolPlan 冻结模型可用工具。"""

    _tools, _has_emit, has_model = _program_trigger_capabilities(
        program["steps"]
    )
    if not has_model:
        return ()
    from core.tool_plan import build_tool_plan

    is_group = snapshot.target_type == "group"
    plan = build_tool_plan(
        chat_type="group" if is_group else "private",
        group_id=snapshot.target_id if is_group else "",
        user_id=(
            snapshot.created_by_actor_id
            or ("" if is_group else snapshot.target_id)
        ),
        platform=snapshot.owner_platform,
        session_id=snapshot.owner_session_id,
        runtime_preset="full",
        db=db,
        extra_disabled={
            "schedule_task": "定时任务 workflow 禁止递归调度",
        },
    )
    return tuple(sorted(plan.executable_tool_names))


def _scheduled_trigger_envelope(
    snapshot: ScheduledTaskSnapshot,
    *,
    trigger_type: str,
    scheduled_for: datetime,
    occurrence_key: str,
    program: Mapping[str, Any],
    model_tool_names: Sequence[str] = (),
    ttl_seconds: int | None = None,
) -> TriggerEnvelope:
    kind = (
        TriggerKind.MANUAL
        if trigger_type == "manual"
        else TriggerKind.SCHEDULE
    )
    tools, has_emit, _has_model = _program_trigger_capabilities(
        program["steps"]
    )
    allowed_tools = tuple(sorted({*tools, *model_tool_names}))
    limits = program["limits"]
    duration = int(limits["max_duration_seconds"])
    effective_ttl = ttl_seconds or max(
        duration,
        3600 if kind is TriggerKind.MANUAL else 86_400,
    )
    occurred_at = scheduled_for
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    owner_type = (
        RuntimeOwnerType.GROUP
        if snapshot.target_type == "group"
        else RuntimeOwnerType.USER
    )
    principal_owner_id = (
        snapshot.target_id
        if owner_type is RuntimeOwnerType.GROUP
        else snapshot.created_by_actor_id or snapshot.target_id
    )
    return build_trigger_envelope(
        kind=kind,
        source_type="scheduled_task",
        source_ref=occurrence_key,
        idempotency_key=occurrence_key,
        principal=RuntimePrincipal(
            snapshot.owner_platform,
            owner_type,
            principal_owner_id,
        ),
        allowed_tools=allowed_tools,
        delivery_endpoints=("qq_push",) if has_emit else (),
        max_model_calls=int(limits["max_steps"]),
        max_steps=int(limits["max_steps"]),
        timeout_seconds=duration,
        occurred_at=occurred_at,
        ttl_seconds=min(effective_ttl, 7 * 24 * 60 * 60),
    )


def enqueue_scheduled_task_execution(
    db: Session,
    *,
    task_id: int,
    trigger_type: str,
    scheduled_for: datetime | None = None,
    manual_idempotency_key: str | None = None,
    expected_owner_chat_stream_id: str | None = None,
    now: datetime | None = None,
) -> ScheduledExecutionEnqueueResult:
    """幂等冻结一次 occurrence；不调用模型、不执行工具、不生成 outbox。"""

    current = _utc_naive(now)
    task = db.get(ScheduledTask, int(task_id))
    if task is None:
        raise ScheduledTaskNotFoundError("定时任务不存在")
    expected_owner = str(expected_owner_chat_stream_id or "").strip()
    if expected_owner and str(task.owner_chat_stream_id or "") != expected_owner:
        raise ScheduledTaskNotFoundError("定时任务不存在")
    if bool(getattr(task, "owner_migration_required", 1)):
        raise ScheduledWorkflowStateError("定时任务 owner 尚未安全迁移")
    if trigger_type == "scheduled" and not bool(task.enabled):
        raise ScheduledWorkflowStateError("已禁用任务不能由调度器执行")

    snapshot = snapshot_scheduled_task(task)
    occurrence = _task_occurrence(
        snapshot,
        trigger_type=trigger_type,
        scheduled_for=scheduled_for,
        manual_idempotency_key=manual_idempotency_key,
        now=current,
    )
    task_snapshot_json = _canonical_json(snapshot.to_dict())
    task_snapshot_sha256 = hashlib.sha256(
        task_snapshot_json.encode("utf-8")
    ).hexdigest()
    owner_snapshot_json = _canonical_json(_owner_snapshot(snapshot))
    normalized_program, program_json, program_sha256 = (
        normalize_scheduled_task_program(snapshot.program)
    )
    if program_sha256 != snapshot.program_sha256:
        raise ScheduledWorkflowStateError("任务 program 快照摘要不一致")
    model_tool_names = _scheduled_model_tool_names(
        db,
        snapshot,
        normalized_program,
    )
    trigger_snapshot_json = _canonical_json(
        _trigger_snapshot(
            snapshot,
            trigger_type=trigger_type,
            scheduled_for=occurrence.scheduled_for,
            occurrence_key=occurrence.occurrence_key,
            program=normalized_program,
            model_tool_names=model_tool_names,
        )
    )
    state_json = _bounded_json(
        _initial_state(normalized_program),
        max_bytes=MAX_WORKFLOW_STATE_BYTES,
        label="任务初始状态",
    )

    existing = (
        db.query(ScheduledTaskExecution)
        .filter(
            ScheduledTaskExecution.task_id == snapshot.task_id,
            ScheduledTaskExecution.occurrence_key
            == occurrence.occurrence_key,
        )
        .first()
    )
    if existing is not None:
        return ScheduledExecutionEnqueueResult(
            execution_id=int(existing.id),
            status=str(existing.status),
            deduplicated=True,
            occurrence_key=occurrence.occurrence_key,
        )

    execution = ScheduledTaskExecution(
        task_id=snapshot.task_id,
        task_version=snapshot.definition_version,
        owner_chat_stream_id=snapshot.owner_chat_stream_id,
        occurrence_key=occurrence.occurrence_key,
        trigger_type=trigger_type,
        scheduled_for=occurrence.scheduled_for,
        task_snapshot_json=task_snapshot_json,
        task_snapshot_sha256=task_snapshot_sha256,
        owner_snapshot_json=owner_snapshot_json,
        trigger_snapshot_json=trigger_snapshot_json,
        program_snapshot_json=program_json,
        program_snapshot_sha256=program_sha256,
        state_json=state_json,
        status="pending",
        created_at=current,
        updated_at=current,
    )
    try:
        with db.begin_nested():
            db.add(execution)
            db.flush()
    except IntegrityError:
        db.expire_all()
        existing = (
            db.query(ScheduledTaskExecution)
            .filter(
                ScheduledTaskExecution.task_id == snapshot.task_id,
                ScheduledTaskExecution.occurrence_key
                == occurrence.occurrence_key,
            )
            .first()
        )
        if existing is None:
            raise
        return ScheduledExecutionEnqueueResult(
            execution_id=int(existing.id),
            status=str(existing.status),
            deduplicated=True,
            occurrence_key=occurrence.occurrence_key,
        )
    return ScheduledExecutionEnqueueResult(
        execution_id=int(execution.id),
        status="pending",
        deduplicated=False,
        occurrence_key=occurrence.occurrence_key,
    )


def _try_acquire_owner_lease(
    db: Session,
    *,
    owner_chat_stream_id: str,
    execution_id: int,
    owner: str,
    token: str,
    expires_at: datetime,
    now: datetime,
) -> bool:
    """在同一数据库事务中抢占 owner 互斥租约。"""

    lease = db.get(ScheduledTaskOwnerLease, owner_chat_stream_id)
    if lease is None:
        try:
            with db.begin_nested():
                db.add(ScheduledTaskOwnerLease(
                    owner_chat_stream_id=owner_chat_stream_id,
                    execution_id=execution_id,
                    lease_owner=owner,
                    lease_token=token,
                    lease_expires_at=expires_at,
                    updated_at=now,
                ))
                db.flush()
            return True
        except IntegrityError:
            db.expire_all()
            lease = db.get(
                ScheduledTaskOwnerLease,
                owner_chat_stream_id,
            )
    if lease is None:
        return False
    lease_expiry = _utc_naive(lease.lease_expires_at)
    if lease_expiry > now and int(lease.execution_id) != execution_id:
        return False
    updated = (
        db.query(ScheduledTaskOwnerLease)
        .filter(
            ScheduledTaskOwnerLease.owner_chat_stream_id
            == owner_chat_stream_id,
            or_(
                ScheduledTaskOwnerLease.lease_expires_at <= now,
                ScheduledTaskOwnerLease.execution_id == execution_id,
            ),
        )
        .update(
            {
                ScheduledTaskOwnerLease.execution_id: execution_id,
                ScheduledTaskOwnerLease.lease_owner: owner,
                ScheduledTaskOwnerLease.lease_token: token,
                ScheduledTaskOwnerLease.lease_expires_at: expires_at,
                ScheduledTaskOwnerLease.updated_at: now,
            },
            synchronize_session=False,
        )
    )
    return updated == 1


def _release_owner_lease(
    db: Session,
    *,
    owner_chat_stream_id: str,
    execution_id: int,
    token: str,
    required: bool,
) -> None:
    removed = (
        db.query(ScheduledTaskOwnerLease)
        .filter(
            ScheduledTaskOwnerLease.owner_chat_stream_id
            == owner_chat_stream_id,
            ScheduledTaskOwnerLease.execution_id == execution_id,
            ScheduledTaskOwnerLease.lease_token == token,
        )
        .delete(synchronize_session=False)
    )
    if required and removed != 1:
        raise ScheduledWorkflowFencingError("任务 owner 互斥租约已失效")


def claim_scheduled_task_executions(
    db: Session,
    *,
    owner: str = _PROCESS_OWNER,
    limit: int = DEFAULT_WORKFLOW_CONCURRENCY,
    lease_seconds: float = DEFAULT_WORKFLOW_LEASE_SECONDS,
    now: datetime | None = None,
) -> list[ScheduledExecutionClaim]:
    """按 CAS 领取到期 execution；过期 running 租约可被新 worker 接管。"""

    normalized_owner = str(owner or "").strip()
    if not normalized_owner or len(normalized_owner) > 128:
        raise ValueError("worker owner 必须是 1-128 字符")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit 必须是 1-100")
    if (
        type(lease_seconds) not in {int, float}
        or not math.isfinite(float(lease_seconds))
        or float(lease_seconds) <= 0
    ):
        raise ValueError("lease_seconds 必须是有限正数")
    current = _utc_naive(now)
    eligible = or_(
        ScheduledTaskExecution.status == "pending",
        and_(
            ScheduledTaskExecution.status == "waiting",
            ScheduledTaskExecution.wake_at.is_not(None),
            ScheduledTaskExecution.wake_at <= current,
        ),
        and_(
            ScheduledTaskExecution.status == "running",
            ScheduledTaskExecution.lease_expires_at.is_not(None),
            ScheduledTaskExecution.lease_expires_at <= current,
        ),
    )
    candidates = (
        db.query(
            ScheduledTaskExecution.id,
            ScheduledTaskExecution.status,
            ScheduledTaskExecution.owner_chat_stream_id,
            ScheduledTaskExecution.lease_generation,
            ScheduledTaskExecution.attempt_count,
        )
        .filter(eligible)
        .order_by(
            ScheduledTaskExecution.scheduled_for.asc(),
            ScheduledTaskExecution.id.asc(),
        )
        .limit(limit)
        .all()
    )
    claims: list[ScheduledExecutionClaim] = []
    for (
        execution_id,
        observed_status,
        owner_chat_stream_id,
        observed_generation,
        observed_attempt_count,
    ) in candidates:
        token = secrets.token_hex(32)
        expires_at = current + timedelta(seconds=float(lease_seconds))
        normalized_stream_id = str(owner_chat_stream_id or "").strip()
        if not normalized_stream_id:
            continue
        if not _try_acquire_owner_lease(
            db,
            owner_chat_stream_id=normalized_stream_id,
            execution_id=int(execution_id),
            owner=normalized_owner,
            token=token,
            expires_at=expires_at,
            now=current,
        ):
            continue
        status_guard: Any
        if observed_status == "pending":
            status_guard = ScheduledTaskExecution.status == "pending"
        elif observed_status == "waiting":
            status_guard = and_(
                ScheduledTaskExecution.status == "waiting",
                ScheduledTaskExecution.wake_at.is_not(None),
                ScheduledTaskExecution.wake_at <= current,
            )
        else:
            status_guard = and_(
                ScheduledTaskExecution.status == "running",
                ScheduledTaskExecution.lease_expires_at.is_not(None),
                ScheduledTaskExecution.lease_expires_at <= current,
            )
        updated = (
            db.query(ScheduledTaskExecution)
            .filter(
                ScheduledTaskExecution.id == int(execution_id),
                ScheduledTaskExecution.lease_generation
                == int(observed_generation or 0),
                ScheduledTaskExecution.attempt_count
                == int(observed_attempt_count or 0),
                status_guard,
            )
            .update(
                {
                    ScheduledTaskExecution.status: "running",
                    ScheduledTaskExecution.lease_owner: normalized_owner,
                    ScheduledTaskExecution.lease_token: token,
                    ScheduledTaskExecution.lease_expires_at: expires_at,
                    ScheduledTaskExecution.lease_generation:
                        ScheduledTaskExecution.lease_generation + 1,
                    ScheduledTaskExecution.attempt_count:
                        ScheduledTaskExecution.attempt_count + 1,
                    ScheduledTaskExecution.wake_at: None,
                    ScheduledTaskExecution.started_at: func.coalesce(
                        ScheduledTaskExecution.started_at,
                        current,
                    ),
                    ScheduledTaskExecution.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if updated == 1:
            claims.append(
                ScheduledExecutionClaim(
                    execution_id=int(execution_id),
                    owner_chat_stream_id=normalized_stream_id,
                    owner=normalized_owner,
                    lease_token=token,
                    lease_expires_at=expires_at,
                    generation=int(observed_generation or 0) + 1,
                    attempt_no=int(observed_attempt_count or 0) + 1,
                )
            )
        else:
            _release_owner_lease(
                db,
                owner_chat_stream_id=normalized_stream_id,
                execution_id=int(execution_id),
                token=token,
                required=False,
            )
    db.commit()
    return claims


def renew_scheduled_task_execution_lease(
    db: Session,
    claim: ScheduledExecutionClaim,
    *,
    lease_seconds: float = DEFAULT_WORKFLOW_LEASE_SECONDS,
    now: datetime | None = None,
) -> datetime:
    current = _utc_naive(now)
    expires_at = current + timedelta(seconds=float(lease_seconds))
    updated = (
        db.query(ScheduledTaskExecution)
        .filter(
            ScheduledTaskExecution.id == claim.execution_id,
            ScheduledTaskExecution.status == "running",
            ScheduledTaskExecution.lease_owner == claim.owner,
            ScheduledTaskExecution.lease_token == claim.lease_token,
            ScheduledTaskExecution.lease_generation == claim.generation,
            ScheduledTaskExecution.attempt_count == claim.attempt_no,
            ScheduledTaskExecution.lease_expires_at > current,
        )
        .update(
            {
                ScheduledTaskExecution.lease_expires_at: expires_at,
                ScheduledTaskExecution.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        db.rollback()
        raise ScheduledWorkflowFencingError("任务执行租约续期 CAS 失败")
    owner_updated = (
        db.query(ScheduledTaskOwnerLease)
        .filter(
            ScheduledTaskOwnerLease.owner_chat_stream_id
            == claim.owner_chat_stream_id,
            ScheduledTaskOwnerLease.execution_id == claim.execution_id,
            ScheduledTaskOwnerLease.lease_owner == claim.owner,
            ScheduledTaskOwnerLease.lease_token == claim.lease_token,
            ScheduledTaskOwnerLease.lease_expires_at > current,
        )
        .update(
            {
                ScheduledTaskOwnerLease.lease_expires_at: expires_at,
                ScheduledTaskOwnerLease.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if owner_updated != 1:
        db.rollback()
        raise ScheduledWorkflowFencingError("任务 owner 互斥租约续期失败")
    db.commit()
    return expires_at


def _step_map(program: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}

    def visit(steps: Sequence[Mapping[str, Any]]) -> None:
        for raw_step in steps:
            step = dict(raw_step)
            result[str(step["id"])] = step
            if step["op"] == "branch":
                visit(step["then"])
                visit(step["else"])
            elif step["op"] == "loop":
                visit(step["steps"])

    visit(program["steps"])
    return result


def _load_state(raw: object, program: Mapping[str, Any]) -> dict[str, Any]:
    if len(str(raw or "").encode("utf-8")) > MAX_WORKFLOW_STATE_BYTES:
        raise ScheduledWorkflowStateError("任务执行状态超过大小上限")
    state = _load_canonical_mapping(raw, label="任务执行状态")
    if state.get("schema_version") != 1:
        raise ScheduledWorkflowStateError("任务执行状态版本不受支持")
    if not isinstance(state.get("variables"), dict):
        raise ScheduledWorkflowStateError("任务 variables 状态无效")
    if not isinstance(state.get("steps"), dict):
        raise ScheduledWorkflowStateError("任务 steps 状态无效")
    if not isinstance(state.get("frames"), list):
        raise ScheduledWorkflowStateError("任务 cursor 状态无效")
    if not isinstance(state.get("waits"), dict):
        raise ScheduledWorkflowStateError("任务 wait 状态无效")
    for key in ("step_count", "loop_iterations"):
        if type(state.get(key)) is not int or int(state[key]) < 0:
            raise ScheduledWorkflowStateError(f"任务 {key} 状态无效")
    steps_by_id = _step_map(program)
    known_ids = set(steps_by_id)
    allowed_step_sequences: set[tuple[str, ...]] = set()

    def collect_sequences(steps: Sequence[Mapping[str, Any]]) -> None:
        allowed_step_sequences.add(tuple(_step_ids(steps)))
        for step in steps:
            if step["op"] == "branch":
                collect_sequences(step["then"])
                if step["else"]:
                    collect_sequences(step["else"])
            elif step["op"] == "loop":
                collect_sequences(step["steps"])

    collect_sequences(program["steps"])
    if any(step_id not in known_ids for step_id in state["steps"]):
        raise ScheduledWorkflowStateError("任务 checkpoint 含未知步骤")
    for value in state["steps"].values():
        if (
            not isinstance(value, Mapping)
            or ("output" not in value and "outputs" not in value)
        ):
            raise ScheduledWorkflowStateError("任务步骤 checkpoint 无效")
        history = value.get("outputs", [])
        if not isinstance(history, list) or any(
            not isinstance(item, Mapping)
            or "runtime_step_id" not in item
            or "output" not in item
            for item in history
        ):
            raise ScheduledWorkflowStateError("任务步骤输出历史无效")
    for frame in state["frames"]:
        if not isinstance(frame, dict):
            raise ScheduledWorkflowStateError("任务 cursor frame 无效")
        kind = frame.get("kind")
        if kind == "steps":
            ids = frame.get("step_ids")
            index = frame.get("index")
            if (
                not isinstance(ids, list)
                or any(item not in known_ids for item in ids)
                or tuple(ids) not in allowed_step_sequences
                or type(index) is not int
                or not 0 <= index <= len(ids)
            ):
                raise ScheduledWorkflowStateError("任务 steps frame 无效")
        elif kind == "loop":
            loop_step = steps_by_id.get(str(frame.get("step_id") or ""))
            index = frame.get("index")
            body_ids = frame.get("body_ids")
            common_invalid = (
                loop_step is None
                or loop_step["op"] != "loop"
                or type(index) is not int
                or body_ids != _step_ids(loop_step["steps"])
            )
            mode = str(
                frame.get("mode")
                or ("items" if "items" in (loop_step or {}) else "condition")
            )
            if common_invalid:
                raise ScheduledWorkflowStateError("任务 loop frame 无效")
            if mode == "items":
                items = frame.get("items")
                previous = frame.get("previous_variables", {})
                missing = frame.get("missing_variables", [])
                if (
                    "items" not in loop_step
                    or not isinstance(items, list)
                    or len(items) > int(loop_step["max_iterations"])
                    or not 0 <= index < len(items)
                    or frame.get("item_name") != loop_step["item"]
                    or frame.get("index_name") != loop_step["index"]
                    or not isinstance(previous, Mapping)
                    or not isinstance(missing, list)
                    or any(not isinstance(item, str) for item in missing)
                ):
                    raise ScheduledWorkflowStateError(
                        "任务 foreach loop frame 无效"
                    )
            elif mode == "condition":
                if (
                    "condition" not in loop_step
                    or not 0 <= index < int(loop_step["max_iterations"])
                ):
                    raise ScheduledWorkflowStateError(
                        "任务 condition loop frame 无效"
                    )
            else:
                raise ScheduledWorkflowStateError("任务 loop mode 无效")
        else:
            raise ScheduledWorkflowStateError("任务 cursor frame 类型无效")
    for runtime_step_id, wake_raw in state["waits"].items():
        if not isinstance(runtime_step_id, str) or not isinstance(
            wake_raw,
            str,
        ):
            raise ScheduledWorkflowStateError("任务 wait checkpoint 无效")
        try:
            datetime.fromisoformat(wake_raw)
        except ValueError as exc:
            raise ScheduledWorkflowStateError(
                "任务 wait checkpoint 无法解析"
            ) from exc
    return state


def _reference_value(state: Mapping[str, Any], reference: str) -> Any:
    parts = reference.split(".")
    root: Any
    if parts[0] == "variables":
        root = state["variables"]
        parts = parts[1:]
    elif parts[0] == "steps":
        root = state["steps"]
        parts = parts[1:]
    else:
        # 兼容文档中的 ``legacy_model.output`` 简写。
        root = state["steps"]
    current = root
    for part in parts:
        if not isinstance(current, Mapping) or part not in current:
            raise ScheduledWorkflowStateError(
                f"任务表达式引用不存在: {reference}"
            )
        current = current[part]
    return current


def _evaluate(value: Any, state: Mapping[str, Any]) -> Any:
    if isinstance(value, list):
        return [_evaluate(item, state) for item in value]
    if not isinstance(value, Mapping):
        return value
    operators = [
        key for key in value if isinstance(key, str) and key.startswith("$")
    ]
    if not operators:
        return {
            str(key): _evaluate(item, state)
            for key, item in value.items()
        }
    operator = operators[0]
    operand = value[operator]
    if operator == "$ref":
        return _reference_value(state, str(operand))
    if operator == "$exists":
        try:
            _evaluate(operand, state)
        except ScheduledWorkflowStateError:
            return False
        return True
    if operator == "$not":
        return not bool(_evaluate(operand, state))
    if operator in {"$and", "$or"}:
        values = [bool(_evaluate(item, state)) for item in operand]
        return all(values) if operator == "$and" else any(values)
    if operator == "$concat":
        return "".join(str(_evaluate(item, state)) for item in operand)
    if operator == "$coalesce":
        for item in operand:
            try:
                resolved = _evaluate(item, state)
            except ScheduledWorkflowStateError:
                continue
            if resolved is not None:
                return resolved
        return None
    if operator == "$json_parse":
        raw = _evaluate(operand, state)
        if not isinstance(raw, str):
            raise ScheduledWorkflowStateError(
                "$json_parse 只能解析 JSON 字符串"
            )
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ScheduledWorkflowStateError(
                "$json_parse 收到无效 JSON"
            ) from exc
        _bounded_json(
            parsed,
            max_bytes=MAX_WORKFLOW_OUTPUT_BYTES,
            label="$json_parse 结果",
        )
        return parsed
    left = _evaluate(operand[0], state)
    right = _evaluate(operand[1], state)
    if operator == "$eq":
        return left == right
    if operator == "$ne":
        return left != right
    try:
        if operator == "$lt":
            return left < right
        if operator == "$lte":
            return left <= right
        if operator == "$gt":
            return left > right
        if operator == "$gte":
            return left >= right
    except TypeError as exc:
        raise ScheduledWorkflowStateError(
            f"{operator} 两侧值不可比较"
        ) from exc
    raise ScheduledWorkflowStateError(f"未知表达式运算符: {operator}")


def _loop_scope(state: Mapping[str, Any]) -> str:
    parts = [
        f"{frame['step_id']}:{frame['index']}"
        for frame in state["frames"]
        if frame.get("kind") == "loop"
    ]
    return "/".join(parts)


def _runtime_step_id(state: Mapping[str, Any], static_step_id: str) -> str:
    scope = _loop_scope(state)
    raw = f"{scope}/{static_step_id}" if scope else static_step_id
    if len(raw) <= 255:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{static_step_id[:64]}@{digest}"


def _set_loop_variables(state: dict[str, Any], frame: Mapping[str, Any]) -> None:
    index = int(frame["index"])
    items = frame["items"]
    state["variables"][str(frame["item_name"])] = items[index]
    state["variables"][str(frame["index_name"])] = index


def _capture_loop_variables(
    state: Mapping[str, Any],
    *,
    item_name: str,
    index_name: str,
) -> tuple[dict[str, Any], list[str]]:
    previous: dict[str, Any] = {}
    missing: list[str] = []
    for name in dict.fromkeys((item_name, index_name)):
        if name in state["variables"]:
            previous[name] = state["variables"][name]
        else:
            missing.append(name)
    return previous, missing


def _restore_loop_variables(
    state: dict[str, Any],
    frame: Mapping[str, Any],
) -> None:
    # 若循环变量覆盖了外层同名变量，离开当前循环时恢复外层值。原先不存在
    # 的变量保留最后一次迭代值，兼容既有 program 在循环后读取 index/item。
    for name, value in dict(frame.get("previous_variables") or {}).items():
        state["variables"][str(name)] = value


def _initialize_step_output_histories(
    state: dict[str, Any],
    steps: Sequence[Mapping[str, Any]],
) -> None:
    for step in steps:
        step_id = str(step["id"])
        existing = state["steps"].get(step_id)
        if existing is None:
            state["steps"][step_id] = {"outputs": []}
        elif "outputs" not in existing:
            existing["outputs"] = []
        if step["op"] == "branch":
            _initialize_step_output_histories(state, step["then"])
            _initialize_step_output_histories(state, step["else"])
        elif step["op"] == "loop":
            _initialize_step_output_histories(state, step["steps"])


def _normalize_frames(
    state: dict[str, Any],
    steps_by_id: Mapping[str, dict[str, Any]],
) -> None:
    """只做无副作用 cursor 推进，直到得到待执行静态步骤或终态。"""

    frames = state["frames"]
    while frames:
        top = frames[-1]
        if top["kind"] == "steps":
            if int(top["index"]) < len(top["step_ids"]):
                return
            frames.pop()
            continue
        # loop frame 只会在当前迭代 body frame 完成后成为栈顶。
        loop_step = steps_by_id.get(str(top.get("step_id") or ""))
        if loop_step is None or loop_step["op"] != "loop":
            raise ScheduledWorkflowStateError("任务 loop step 不存在")
        next_index = int(top["index"]) + 1
        mode = str(
            top.get("mode")
            or ("items" if "items" in loop_step else "condition")
        )
        if mode == "items":
            if next_index >= len(top["items"]):
                frames.pop()
                _restore_loop_variables(state, top)
                continue
        elif mode == "condition":
            should_continue = bool(
                _evaluate(loop_step["condition"], state)
            )
            if not should_continue:
                frames.pop()
                continue
            if next_index >= int(loop_step["max_iterations"]):
                raise ScheduledWorkflowLoopLimitError(
                    f"条件循环 {loop_step['id']} 达到 max_iterations "
                    "后条件仍为 true"
                )
        else:
            raise ScheduledWorkflowStateError("任务 loop mode 无效")
        top["index"] = next_index
        state["loop_iterations"] = int(state["loop_iterations"]) + 1
        if mode == "items":
            _set_loop_variables(state, top)
        frames.append(
            {
                "kind": "steps",
                "step_ids": list(top["body_ids"]),
                "index": 0,
            }
        )


def _next_step(
    state: dict[str, Any],
    steps_by_id: Mapping[str, dict[str, Any]],
) -> tuple[dict[str, Any], str] | None:
    _normalize_frames(state, steps_by_id)
    if not state["frames"]:
        return None
    frame = state["frames"][-1]
    if frame["kind"] != "steps":
        raise ScheduledWorkflowStateError("任务 cursor 未落在步骤 frame")
    static_id = str(frame["step_ids"][int(frame["index"])])
    step = steps_by_id.get(static_id)
    if step is None:
        raise ScheduledWorkflowStateError(f"任务步骤不存在: {static_id}")
    return step, _runtime_step_id(state, static_id)


def _advance_step(state: dict[str, Any]) -> None:
    if not state["frames"] or state["frames"][-1]["kind"] != "steps":
        raise ScheduledWorkflowStateError("任务 cursor 无法推进")
    state["frames"][-1]["index"] = (
        int(state["frames"][-1]["index"]) + 1
    )


def _step_max_attempts(step: Mapping[str, Any]) -> int:
    if step["op"] == "tool":
        return int(step.get("max_attempts") or 1)
    # model 步骤运行的是可能调用真实工具的完整 Agent，不是纯函数。模型路由
    # 自身可在 Agent 内重试，workflow 不得整体重放该步骤。
    if step["op"] == "model":
        return 1
    return 1


def _attempt_idempotency_key(
    execution_id: int,
    runtime_step_id: str,
) -> str:
    """生成同一运行时步骤跨重试稳定的外部幂等键。"""

    step_digest = hashlib.sha256(
        runtime_step_id.encode("utf-8")
    ).hexdigest()[:24]
    return f"scheduled-workflow:{execution_id}:{step_digest}"


def _require_claim(
    db: Session,
    claim: ScheduledExecutionClaim,
    *,
    now: datetime,
) -> ScheduledTaskExecution:
    execution = (
        db.query(ScheduledTaskExecution)
        .filter(
            ScheduledTaskExecution.id == claim.execution_id,
            ScheduledTaskExecution.status == "running",
            ScheduledTaskExecution.lease_owner == claim.owner,
            ScheduledTaskExecution.lease_token == claim.lease_token,
            ScheduledTaskExecution.lease_generation == claim.generation,
            ScheduledTaskExecution.attempt_count == claim.attempt_no,
            ScheduledTaskExecution.lease_expires_at > now,
        )
        .first()
    )
    if execution is None:
        raise ScheduledWorkflowFencingError("任务执行 fencing token 已失效")
    owner_lease = (
        db.query(ScheduledTaskOwnerLease.owner_chat_stream_id)
        .filter(
            ScheduledTaskOwnerLease.owner_chat_stream_id
            == claim.owner_chat_stream_id,
            ScheduledTaskOwnerLease.execution_id == claim.execution_id,
            ScheduledTaskOwnerLease.lease_owner == claim.owner,
            ScheduledTaskOwnerLease.lease_token == claim.lease_token,
            ScheduledTaskOwnerLease.lease_expires_at > now,
        )
        .first()
    )
    if owner_lease is None:
        raise ScheduledWorkflowFencingError("任务 owner 互斥租约已失效")
    return execution


def _clear_lease_values() -> dict[Any, Any]:
    return {
        ScheduledTaskExecution.lease_owner: None,
        ScheduledTaskExecution.lease_token: None,
        ScheduledTaskExecution.lease_expires_at: None,
    }


def _finish_execution(
    db: Session,
    claim: ScheduledExecutionClaim,
    *,
    status: str,
    state_json: str,
    current_step_id: str = "",
    error_code: str = "",
    error_summary: str = "",
    now: datetime,
) -> None:
    values: dict[Any, Any] = {
        ScheduledTaskExecution.status: status,
        ScheduledTaskExecution.state_json: state_json,
        ScheduledTaskExecution.current_step_id: current_step_id,
        ScheduledTaskExecution.last_error_code: str(error_code or "")[:128],
        ScheduledTaskExecution.last_error_summary: str(
            error_summary or ""
        )[:1000],
        ScheduledTaskExecution.updated_at: now,
        ScheduledTaskExecution.wake_at: None,
        **_clear_lease_values(),
    }
    if status in _TERMINAL_STATUSES:
        values[ScheduledTaskExecution.finished_at] = now
    updated = (
        db.query(ScheduledTaskExecution)
        .filter(
            ScheduledTaskExecution.id == claim.execution_id,
            ScheduledTaskExecution.status == "running",
            ScheduledTaskExecution.lease_owner == claim.owner,
            ScheduledTaskExecution.lease_token == claim.lease_token,
            ScheduledTaskExecution.lease_generation == claim.generation,
            ScheduledTaskExecution.attempt_count == claim.attempt_no,
            ScheduledTaskExecution.lease_expires_at > now,
        )
        .update(values, synchronize_session=False)
    )
    if updated != 1:
        db.rollback()
        raise ScheduledWorkflowFencingError("任务执行结算 CAS 失败")
    try:
        _release_owner_lease(
            db,
            owner_chat_stream_id=claim.owner_chat_stream_id,
            execution_id=claim.execution_id,
            token=claim.lease_token,
            required=True,
        )
    except ScheduledWorkflowFencingError:
        db.rollback()
        raise
    db.commit()


def _park_execution_until(
    db: Session,
    claim: ScheduledExecutionClaim,
    *,
    state_json: str,
    current_step_id: str,
    wake_at: datetime,
    now: datetime,
) -> None:
    """原子释放执行租约并写入可重新领取的 wake 时间。"""

    updated = (
        db.query(ScheduledTaskExecution)
        .filter(
            ScheduledTaskExecution.id == claim.execution_id,
            ScheduledTaskExecution.status == "running",
            ScheduledTaskExecution.lease_owner == claim.owner,
            ScheduledTaskExecution.lease_token == claim.lease_token,
            ScheduledTaskExecution.lease_generation == claim.generation,
            ScheduledTaskExecution.attempt_count == claim.attempt_no,
            ScheduledTaskExecution.lease_expires_at > now,
        )
        .update(
            {
                ScheduledTaskExecution.status: "waiting",
                ScheduledTaskExecution.state_json: state_json,
                ScheduledTaskExecution.current_step_id: current_step_id,
                ScheduledTaskExecution.wake_at: wake_at,
                ScheduledTaskExecution.updated_at: now,
                **_clear_lease_values(),
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        db.rollback()
        raise ScheduledWorkflowFencingError("任务 wait checkpoint CAS 失败")
    try:
        _release_owner_lease(
            db,
            owner_chat_stream_id=claim.owner_chat_stream_id,
            execution_id=claim.execution_id,
            token=claim.lease_token,
            required=True,
        )
    except ScheduledWorkflowFencingError:
        db.rollback()
        raise
    db.commit()


async def _invoke_callback(
    callback: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> ScheduledWorkflowStepOutcome:
    result = callback(*args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, ScheduledWorkflowStepOutcome):
        raise TypeError("任务 callback 必须返回 ScheduledWorkflowStepOutcome")
    return result


async def _with_lease_heartbeat(
    callback: Callable[[], Awaitable[ScheduledWorkflowStepOutcome]],
    *,
    claim: ScheduledExecutionClaim,
    session_factory: Callable[[], Session],
    lease_seconds: float,
) -> ScheduledWorkflowStepOutcome:
    task = asyncio.create_task(callback())
    interval = max(1.0, min(60.0, float(lease_seconds) / 3.0))
    while True:
        done, _pending = await asyncio.wait(
            {task},
            timeout=interval,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if task in done:
            return task.result()
        heartbeat_db = session_factory()
        try:
            renew_scheduled_task_execution_lease(
                heartbeat_db,
                claim,
                lease_seconds=lease_seconds,
            )
        except BaseException:
            task.cancel()
            try:
                await task
            except BaseException:
                pass
            raise
        finally:
            heartbeat_db.close()


def _attempt_input(
    step: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    operation = step["op"]
    if operation == "set":
        return {
            "name": step["name"],
            "value": _evaluate(step["value"], state),
        }
    if operation == "tool":
        args = _evaluate(step["args"], state)
        if not isinstance(args, dict):
            raise ScheduledWorkflowStateError("tool args 求值后必须是对象")
        idempotency_arg = str(step.get("idempotency_arg") or "")
        if idempotency_arg:
            existing = args.get(idempotency_arg)
            if existing not in {None, idempotency_key}:
                raise ScheduledWorkflowStateError(
                    "tool args 中的幂等键与执行器事实不一致"
                )
            args[idempotency_arg] = idempotency_key
        return {"tool": step["tool"], "args": args}
    if operation == "model":
        prompt = _evaluate(step["prompt"], state)
        if not isinstance(prompt, str) or not prompt.strip():
            raise ScheduledWorkflowStateError(
                "model prompt 求值后必须是非空字符串"
            )
        if len(prompt) > MAX_SCHEDULED_TASK_PROMPT_CHARS:
            raise ScheduledWorkflowStateError(
                "model prompt 求值后超过长度上限"
            )
        return {"prompt": prompt}
    if operation == "branch":
        return {"condition": bool(_evaluate(step["condition"], state))}
    if operation == "loop":
        if "items" in step:
            items = _evaluate(step["items"], state)
            if not isinstance(items, list):
                raise ScheduledWorkflowStateError(
                    "loop items 求值后必须是数组"
                )
            if len(items) > int(step["max_iterations"]):
                raise ScheduledWorkflowStateError(
                    "loop items 超过显式 max_iterations"
                )
            return {"mode": "items", "items": items}
        return {
            "mode": "condition",
            "condition": bool(_evaluate(step["condition"], state)),
        }
    if operation == "wait":
        seconds = _evaluate(step["seconds"], state)
        if (
            isinstance(seconds, bool)
            or type(seconds) not in {int, float}
            or not math.isfinite(float(seconds))
            or not 0 <= float(seconds) <= MAX_SCHEDULED_TASK_WAIT_SECONDS
        ):
            raise ScheduledWorkflowStateError(
                "wait seconds 求值后必须是允许范围内的有限数值"
            )
        return {"seconds": float(seconds)}
    content = _evaluate(step["content"], state)
    if not isinstance(content, str) or not content.strip():
        raise ScheduledWorkflowStateError(
            "emit content 求值后必须是非空字符串"
        )
    if len(content.encode("utf-8")) > MAX_WORKFLOW_OUTPUT_BYTES:
        raise ScheduledWorkflowStateError("emit content 超过输出上限")
    return {"content": content}


def _record_step_output(
    state: dict[str, Any],
    step: Mapping[str, Any],
    runtime_step_id: str,
    output: Any,
) -> None:
    _bounded_json(
        output,
        max_bytes=MAX_WORKFLOW_OUTPUT_BYTES,
        label="任务步骤输出",
    )
    static_id = str(step["id"])
    existing = state["steps"].get(static_id)
    history = (
        list(existing.get("outputs") or [])
        if isinstance(existing, Mapping)
        else []
    )
    history.append(
        {
            "runtime_step_id": runtime_step_id,
            "output": output,
        }
    )
    state["steps"][static_id] = {
        "output": output,
        "runtime_step_id": runtime_step_id,
        "outputs": history,
    }
    save_as = str(step.get("save_as") or "")
    if save_as:
        state["variables"][save_as] = output


def _apply_success_transition(
    state: dict[str, Any],
    step: Mapping[str, Any],
    runtime_step_id: str,
    outcome: ScheduledWorkflowStepOutcome,
    attempt_input: Mapping[str, Any],
) -> None:
    operation = str(step["op"])
    if operation == "set":
        value = attempt_input["value"]
        state["variables"][str(step["name"])] = value
        _record_step_output(state, step, runtime_step_id, value)
        _advance_step(state)
    elif operation == "branch":
        selected = step["then"] if attempt_input["condition"] else step["else"]
        _record_step_output(
            state,
            step,
            runtime_step_id,
            {"selected": "then" if attempt_input["condition"] else "else"},
        )
        _advance_step(state)
        if selected:
            state["frames"].append(
                {
                    "kind": "steps",
                    "step_ids": _step_ids(selected),
                    "index": 0,
                }
            )
    elif operation == "loop":
        mode = str(attempt_input["mode"])
        items = (
            list(attempt_input["items"])
            if mode == "items"
            else []
        )
        _record_step_output(
            state,
            step,
            runtime_step_id,
            (
                {"mode": "items", "item_count": len(items)}
                if mode == "items"
                else {
                    "mode": "condition",
                    "entered": bool(attempt_input["condition"]),
                }
            ),
        )
        _advance_step(state)
        _initialize_step_output_histories(state, step["steps"])
        if mode == "items":
            item_name = str(step["item"])
            index_name = str(step["index"])
            previous, missing = _capture_loop_variables(
                state,
                item_name=item_name,
                index_name=index_name,
            )
            if not items:
                for name in missing:
                    state["variables"][name] = None
                state["step_count"] = int(state["step_count"]) + 1
                return
            should_enter = True
        else:
            should_enter = bool(attempt_input["condition"])
            previous = {}
            missing = []
        if should_enter:
            state["loop_iterations"] = int(state["loop_iterations"]) + 1
            frame = {
                "kind": "loop",
                "mode": mode,
                "step_id": step["id"],
                "index": 0,
                "body_ids": _step_ids(step["steps"]),
            }
            if mode == "items":
                frame.update(
                    {
                        "items": items,
                        "item_name": step["item"],
                        "index_name": step["index"],
                        "previous_variables": previous,
                        "missing_variables": missing,
                    }
                )
            state["frames"].append(frame)
            if mode == "items":
                _set_loop_variables(state, frame)
            state["frames"].append(
                {
                    "kind": "steps",
                    "step_ids": list(frame["body_ids"]),
                    "index": 0,
                }
            )
    else:
        output = outcome.output
        if operation == "wait":
            output = {"waited": True}
        _record_step_output(state, step, runtime_step_id, output)
        _advance_step(state)
    state["step_count"] = int(state["step_count"]) + 1


def _latest_attempt(
    db: Session,
    execution_id: int,
    runtime_step_id: str,
) -> tuple[ScheduledTaskStepAttempt | None, int]:
    attempts = (
        db.query(ScheduledTaskStepAttempt)
        .filter(
            ScheduledTaskStepAttempt.execution_id == execution_id,
            ScheduledTaskStepAttempt.step_id == runtime_step_id,
        )
        .order_by(ScheduledTaskStepAttempt.attempt_no.asc())
        .all()
    )
    return (attempts[-1] if attempts else None, len(attempts))


def _started_attempt_is_retry_safe(step: Mapping[str, Any]) -> bool:
    if step["op"] != "tool":
        return step["op"] in _SAFE_RECOVERY_OPERATIONS
    return step.get("recovery") == "safe_retry"


def _mark_started_attempt_after_recovery(
    db: Session,
    attempt: ScheduledTaskStepAttempt,
    *,
    retry_safe: bool,
    now: datetime,
) -> None:
    attempt.status = "failed" if retry_safe else "ambiguous"
    attempt.error_type = (
        "worker_lease_lost_retry"
        if retry_safe
        else "side_effect_result_unknown"
    )
    attempt.error_summary = (
        "上一个 worker 在步骤完成前丢失租约，允许按声明重试"
        if retry_safe
        else "上一个 worker 在副作用步骤完成前丢失租约，结果无法确认"
    )
    attempt.completed_at = now
    db.flush()


def _step_model_trace_id(state: Mapping[str, Any]) -> str:
    """emit 继承最近 model 步骤的 Trace，便于 outbound 直接关联。"""

    for value in reversed(list(state["steps"].values())):
        if not isinstance(value, Mapping):
            continue
        trace_id = str(value.get("model_trace_id") or "")
        if trace_id:
            return trace_id
    return ""


async def execute_claimed_scheduled_task(
    db: Session,
    *,
    claim: ScheduledExecutionClaim,
    callbacks: ScheduledWorkflowCallbacks,
    session_factory: Callable[[], Session],
    lease_seconds: float = DEFAULT_WORKFLOW_LEASE_SECONDS,
    clock: Callable[[], datetime] | None = None,
) -> str:
    """执行一个已领取 execution，返回其本轮终态或 waiting。"""

    now_source = clock or (lambda: datetime.now(timezone.utc))

    def current_time() -> datetime:
        return _utc_naive(now_source())

    execution = _require_claim(db, claim, now=current_time())
    task_snapshot_mapping = _load_canonical_mapping(
        execution.task_snapshot_json,
        label="任务冻结快照",
        expected_sha256=execution.task_snapshot_sha256,
    )
    try:
        task_snapshot = ScheduledTaskSnapshot.from_mapping(
            task_snapshot_mapping
        )
    except ScheduledTaskOutboundError as exc:
        raise ScheduledWorkflowStateError(
            "任务冻结快照合同无效"
        ) from exc
    program, program_json, program_sha256 = normalize_scheduled_task_program(
        task_snapshot.program
    )
    if (
        program_json != str(execution.program_snapshot_json)
        or program_sha256 != str(execution.program_snapshot_sha256)
        or program_sha256 != task_snapshot.program_sha256
    ):
        raise ScheduledWorkflowStateError("任务冻结 program 不一致")
    trigger_snapshot = _load_canonical_mapping(
        execution.trigger_snapshot_json,
        label="任务 Trigger 快照",
    )
    raw_envelope = trigger_snapshot.get("envelope")
    expected_trigger_type = str(execution.trigger_type)
    expected_scheduled_for = execution.scheduled_for.isoformat(
        timespec="seconds"
    )
    if (
        str(trigger_snapshot.get("trigger_type") or "")
        != expected_trigger_type
        or str(trigger_snapshot.get("scheduled_for") or "")
        != expected_scheduled_for
    ):
        raise ScheduledWorkflowStateError("任务 Trigger 基础快照与 execution 不一致")
    raw_model_tool_names = trigger_snapshot.get("model_tool_names")
    if raw_model_tool_names is None:
        _tools, _has_emit, has_model = _program_trigger_capabilities(
            program["steps"]
        )
        raw_model_tool_names = (
            ["no_reply", "reply"]
            if raw_envelope is None and has_model
            else []
        )
    if not isinstance(raw_model_tool_names, list) or any(
        not isinstance(item, str)
        for item in raw_model_tool_names
    ):
        raise ScheduledWorkflowStateError("任务 Trigger 模型工具快照无效")
    model_tool_names = tuple(sorted({
        str(item).strip()
        for item in raw_model_tool_names
        if str(item).strip()
    }))
    if (
        list(model_tool_names) != raw_model_tool_names
        or any("*" in item or "?" in item for item in model_tool_names)
    ):
        raise ScheduledWorkflowStateError("任务 Trigger 模型工具快照无效")
    if raw_envelope is None:
        # 升级前已安全冻结的 execution 没有 envelope。只能从同一行的任务、
        # owner、occurrence 与 program 快照确定性补齐，不能回读 live task。
        trigger_envelope = _scheduled_trigger_envelope(
            task_snapshot,
            trigger_type=expected_trigger_type,
            scheduled_for=execution.scheduled_for,
            occurrence_key=str(execution.occurrence_key),
            program=program,
            model_tool_names=model_tool_names,
        )
        trigger_snapshot["model_tool_names"] = list(model_tool_names)
        trigger_snapshot["envelope"] = trigger_envelope.to_dict()
        execution = _require_claim(db, claim, now=current_time())
        execution.trigger_snapshot_json = _canonical_json(trigger_snapshot)
        execution.updated_at = current_time()
        db.commit()
        raw_envelope = trigger_snapshot["envelope"]
    if not isinstance(raw_envelope, Mapping):
        raise ScheduledWorkflowStateError("任务 Trigger envelope 无效")
    try:
        trigger_envelope = TriggerEnvelope.from_mapping(raw_envelope)
        ttl_seconds = int(
            (
                trigger_envelope.expires_at
                - trigger_envelope.occurred_at
            ).total_seconds()
        )
        expected_trigger = _scheduled_trigger_envelope(
            task_snapshot,
            trigger_type=str(execution.trigger_type),
            scheduled_for=execution.scheduled_for,
            occurrence_key=str(execution.occurrence_key),
            program=program,
            model_tool_names=model_tool_names,
            ttl_seconds=ttl_seconds,
        )
    except (TriggerContractError, TypeError, ValueError) as exc:
        raise ScheduledWorkflowStateError(
            "任务 Trigger envelope 无效"
        ) from exc
    if expected_trigger.to_dict() != trigger_envelope.to_dict():
        raise ScheduledWorkflowStateError("任务 Trigger envelope 与冻结任务不一致")
    state = _load_state(execution.state_json, program)
    steps_by_id = _step_map(program)
    limits = program["limits"]
    active_started_at = current_time()
    occurrence = ScheduledOccurrence(
        occurrence_key=str(execution.occurrence_key),
        scheduled_for=execution.scheduled_for,
    )

    while True:
        current = current_time()
        elapsed = (current - active_started_at).total_seconds()
        if elapsed > int(limits["max_duration_seconds"]):
            state_json = _bounded_json(
                state,
                max_bytes=MAX_WORKFLOW_STATE_BYTES,
                label="任务状态",
            )
            _finish_execution(
                db,
                claim,
                status="failed",
                state_json=state_json,
                error_code="duration_exceeded",
                error_summary="任务执行超过 max_duration_seconds",
                now=current,
            )
            return "failed"
        if int(state["step_count"]) >= int(limits["max_steps"]):
            try:
                _normalize_frames(state, steps_by_id)
            except ScheduledWorkflowLoopLimitError as exc:
                state_json = _bounded_json(
                    state,
                    max_bytes=MAX_WORKFLOW_STATE_BYTES,
                    label="任务状态",
                )
                _finish_execution(
                    db,
                    claim,
                    status="failed",
                    state_json=state_json,
                    error_code="loop_budget_exhausted",
                    error_summary=str(exc),
                    now=current,
                )
                return "failed"
            if state["frames"]:
                state_json = _bounded_json(
                    state,
                    max_bytes=MAX_WORKFLOW_STATE_BYTES,
                    label="任务状态",
                )
                _finish_execution(
                    db,
                    claim,
                    status="failed",
                    state_json=state_json,
                    error_code="step_budget_exhausted",
                    error_summary="任务执行超过 max_steps",
                    now=current,
                )
                return "failed"
        if int(state["loop_iterations"]) > int(
            limits["max_loop_iterations"]
        ):
            state_json = _bounded_json(
                state,
                max_bytes=MAX_WORKFLOW_STATE_BYTES,
                label="任务状态",
            )
            _finish_execution(
                db,
                claim,
                status="failed",
                state_json=state_json,
                error_code="loop_budget_exhausted",
                error_summary="任务执行超过 max_loop_iterations",
                now=current,
            )
            return "failed"

        try:
            next_item = _next_step(state, steps_by_id)
        except ScheduledWorkflowLoopLimitError as exc:
            state_json = _bounded_json(
                state,
                max_bytes=MAX_WORKFLOW_STATE_BYTES,
                label="任务状态",
            )
            _finish_execution(
                db,
                claim,
                status="failed",
                state_json=state_json,
                error_code="loop_budget_exhausted",
                error_summary=str(exc),
                now=current,
            )
            return "failed"
        if next_item is None:
            state_json = _bounded_json(
                state,
                max_bytes=MAX_WORKFLOW_STATE_BYTES,
                label="任务状态",
            )
            _finish_execution(
                db,
                claim,
                status="succeeded",
                state_json=state_json,
                now=current,
            )
            return "succeeded"
        # ``_next_step`` 会推进已完成的 loop frame；嵌套循环可能在这里
        # 才令全局迭代数越界，因此必须在执行下一步前再次检查。
        if int(state["loop_iterations"]) > int(
            limits["max_loop_iterations"]
        ):
            state_json = _bounded_json(
                state,
                max_bytes=MAX_WORKFLOW_STATE_BYTES,
                label="任务状态",
            )
            _finish_execution(
                db,
                claim,
                status="failed",
                state_json=state_json,
                error_code="loop_budget_exhausted",
                error_summary="任务执行超过 max_loop_iterations",
                now=current,
            )
            return "failed"
        step, runtime_step_id = next_item
        static_step_id = str(step["id"])
        operation = str(step["op"])
        latest, attempt_count = _latest_attempt(
            db,
            claim.execution_id,
            runtime_step_id,
        )

        if latest is not None and latest.status == "started":
            if operation == "wait":
                wake_raw = state["waits"].get(runtime_step_id)
                if not wake_raw:
                    # worker 可能在写入 started attempt 后、持久化 wake_at 前
                    # 崩溃。wait 没有外部副作用，可由冻结输入和 attempt
                    # 起始时间确定性重建原唤醒时刻。
                    recovered_input = _attempt_input(
                        step,
                        state,
                        idempotency_key=str(latest.idempotency_key),
                    )
                    wake_at = _utc_naive(latest.started_at) + timedelta(
                        seconds=float(recovered_input["seconds"])
                    )
                    state["waits"][runtime_step_id] = wake_at.isoformat(
                        timespec="microseconds"
                    )
                else:
                    try:
                        wake_at = datetime.fromisoformat(str(wake_raw))
                    except ValueError as exc:
                        raise ScheduledWorkflowStateError(
                            "wait wake checkpoint 无法解析"
                        ) from exc
                if current < wake_at:
                    state_json = _bounded_json(
                        state,
                        max_bytes=MAX_WORKFLOW_STATE_BYTES,
                        label="任务状态",
                    )
                    _park_execution_until(
                        db,
                        claim,
                        state_json=state_json,
                        current_step_id=runtime_step_id,
                        wake_at=wake_at,
                        now=current,
                    )
                    return "waiting"
                state["waits"].pop(runtime_step_id, None)
                _apply_success_transition(
                    state,
                    step,
                    runtime_step_id,
                    ScheduledWorkflowStepOutcome(output={"waited": True}),
                    {"seconds": 0.0},
                )
                checkpoint = _bounded_json(
                    state,
                    max_bytes=MAX_WORKFLOW_STATE_BYTES,
                    label="任务 checkpoint",
                )
                latest.status = "succeeded"
                latest.output_sha256 = _sha256_json({"waited": True})
                latest.checkpoint_json = checkpoint
                latest.completed_at = current
                execution = _require_claim(db, claim, now=current)
                execution.state_json = checkpoint
                execution.current_step_id = ""
                execution.updated_at = current
                db.commit()
                continue

            retry_safe = _started_attempt_is_retry_safe(step)
            _mark_started_attempt_after_recovery(
                db,
                latest,
                retry_safe=retry_safe,
                now=current,
            )
            if not retry_safe:
                state_json = _bounded_json(
                    state,
                    max_bytes=MAX_WORKFLOW_STATE_BYTES,
                    label="任务状态",
                )
                db.commit()
                _finish_execution(
                    db,
                    claim,
                    status="ambiguous",
                    state_json=state_json,
                    current_step_id=runtime_step_id,
                    error_code="side_effect_result_unknown",
                    error_summary=latest.error_summary,
                    now=current,
                )
                return "ambiguous"
            db.commit()

        max_attempts = _step_max_attempts(step)
        recovered_started = bool(
            latest is not None
            and latest.status == "failed"
            and latest.error_type == "worker_lease_lost_retry"
        )
        recovery_attempt_allowed = (
            recovered_started and attempt_count == max_attempts
        )
        if attempt_count >= max_attempts and not recovery_attempt_allowed:
            state_json = _bounded_json(
                state,
                max_bytes=MAX_WORKFLOW_STATE_BYTES,
                label="任务状态",
            )
            _finish_execution(
                db,
                claim,
                status="failed",
                state_json=state_json,
                current_step_id=runtime_step_id,
                error_code="attempts_exhausted",
                error_summary=f"步骤 {static_step_id} 已耗尽重试次数",
                now=current,
            )
            return "failed"

        attempt_no = attempt_count + 1
        idempotency_key = _attempt_idempotency_key(
            claim.execution_id,
            runtime_step_id,
        )
        try:
            resolved_input = _attempt_input(
                step,
                state,
                idempotency_key=idempotency_key,
            )
        except ScheduledWorkflowStateError as exc:
            state_json = _bounded_json(
                state,
                max_bytes=MAX_WORKFLOW_STATE_BYTES,
                label="任务状态",
            )
            _finish_execution(
                db,
                claim,
                status="blocked",
                state_json=state_json,
                current_step_id=runtime_step_id,
                error_code="program_evaluation_failed",
                error_summary=str(exc),
                now=current,
            )
            return "blocked"

        attempt = ScheduledTaskStepAttempt(
            execution_id=claim.execution_id,
            step_id=runtime_step_id,
            attempt_no=attempt_no,
            idempotency_key=idempotency_key,
            operation=operation,
            status="started",
            input_sha256=_sha256_json(resolved_input),
            started_at=current,
        )
        execution = _require_claim(db, claim, now=current)
        execution.current_step_id = runtime_step_id
        execution.state_json = _bounded_json(
            state,
            max_bytes=MAX_WORKFLOW_STATE_BYTES,
            label="任务状态",
        )
        execution.updated_at = current
        db.add(attempt)
        db.commit()

        context = ScheduledWorkflowContext(
            execution_id=claim.execution_id,
            task_snapshot=task_snapshot,
            occurrence=occurrence,
            trigger_type=str(execution.trigger_type),
            runtime_step_id=runtime_step_id,
            static_step_id=static_step_id,
            trigger_envelope=trigger_envelope,
            model_tool_names=model_tool_names,
        )
        try:
            trigger_envelope.assert_active(
                now=current.replace(tzinfo=timezone.utc),
            )
            if operation in {"set", "branch", "loop"}:
                outcome = ScheduledWorkflowStepOutcome(
                    output=resolved_input
                )
            elif operation == "wait":
                seconds = float(resolved_input["seconds"])
                if seconds > 0:
                    wake_at = current + timedelta(seconds=seconds)
                    state["waits"][runtime_step_id] = wake_at.isoformat(
                        timespec="microseconds"
                    )
                    state_json = _bounded_json(
                        state,
                        max_bytes=MAX_WORKFLOW_STATE_BYTES,
                        label="任务状态",
                    )
                    _park_execution_until(
                        db,
                        claim,
                        state_json=state_json,
                        current_step_id=runtime_step_id,
                        wake_at=wake_at,
                        now=current,
                    )
                    return "waiting"
                outcome = ScheduledWorkflowStepOutcome(
                    output={"waited": True}
                )
            elif operation == "tool":
                trigger_envelope.assert_tool(str(resolved_input["tool"]))

                async def call_tool() -> ScheduledWorkflowStepOutcome:
                    return await _invoke_callback(
                        callbacks.execute_tool,
                        context,
                        tool_name=str(resolved_input["tool"]),
                        args=dict(resolved_input["args"]),
                        idempotency_key=idempotency_key,
                    )

                outcome = await _with_lease_heartbeat(
                    call_tool,
                    claim=claim,
                    session_factory=session_factory,
                    lease_seconds=lease_seconds,
                )
            elif operation == "model":
                async def call_model() -> ScheduledWorkflowStepOutcome:
                    return await _invoke_callback(
                        callbacks.execute_model,
                        context,
                        prompt=str(resolved_input["prompt"]),
                        idempotency_key=idempotency_key,
                    )

                outcome = await _with_lease_heartbeat(
                    call_model,
                    claim=claim,
                    session_factory=session_factory,
                    lease_seconds=lease_seconds,
                )
            else:
                trigger_envelope.assert_delivery("qq_push")

                async def call_emit() -> ScheduledWorkflowStepOutcome:
                    return await _invoke_callback(
                        callbacks.emit,
                        context,
                        content=str(resolved_input["content"]),
                        idempotency_key=idempotency_key,
                        model_trace_id=_step_model_trace_id(state),
                    )

                outcome = await _with_lease_heartbeat(
                    call_emit,
                    claim=claim,
                    session_factory=session_factory,
                    lease_seconds=lease_seconds,
                )
        except ScheduledWorkflowFencingError:
            raise
        except TriggerContractError as exc:
            outcome = ScheduledWorkflowStepOutcome.failed(
                "trigger_policy_denied",
                str(exc),
                blocked=True,
                stop=True,
            )
        except Exception as exc:
            retry_safe = _started_attempt_is_retry_safe(step)
            outcome = ScheduledWorkflowStepOutcome.failed(
                "callback_exception",
                f"任务步骤回调异常: {type(exc).__name__}",
                retryable=retry_safe,
                ambiguous=not retry_safe,
            )

        current = current_time()
        execution = _require_claim(db, claim, now=current)
        attempt = db.get(ScheduledTaskStepAttempt, int(attempt.id))
        if attempt is None or attempt.status != "started":
            raise ScheduledWorkflowFencingError("任务步骤 attempt 已被其他 worker 结算")
        if not outcome.success:
            terminal_status = (
                "ambiguous"
                if outcome.ambiguous
                else "blocked"
                if outcome.blocked or outcome.stop
                else "failed"
            )
            can_retry = (
                outcome.retryable
                and not outcome.ambiguous
                and not outcome.stop
                and attempt_no < max_attempts
            )
            attempt.status = "failed" if can_retry else terminal_status
            attempt.error_type = str(outcome.error_code)[:128]
            attempt.error_summary = str(outcome.error_summary)[:1000]
            attempt.tool_call_id = str(outcome.tool_call_id or "")[:128]
            attempt.model_trace_id = str(
                outcome.model_trace_id or ""
            )[:128]
            attempt.completed_at = current
            if outcome.model_trace_id:
                execution.agent_trace_id = str(
                    outcome.model_trace_id
                )[:128]
            if outcome.agent_run_id:
                execution.agent_run_id = str(
                    outcome.agent_run_id
                )[:128]
            execution.updated_at = current
            db.commit()
            if can_retry:
                continue
            state_json = _bounded_json(
                state,
                max_bytes=MAX_WORKFLOW_STATE_BYTES,
                label="任务状态",
            )
            _finish_execution(
                db,
                claim,
                status=terminal_status,
                state_json=state_json,
                current_step_id=runtime_step_id,
                error_code=outcome.error_code,
                error_summary=outcome.error_summary,
                now=current,
            )
            return terminal_status

        output = outcome.output
        if outcome.stop:
            stopped_output = (
                output
                if output is not None
                else {"status": "stopped"}
            )
            try:
                _record_step_output(
                    state,
                    step,
                    runtime_step_id,
                    stopped_output,
                )
                if outcome.model_trace_id:
                    state["steps"][static_step_id][
                        "model_trace_id"
                    ] = str(outcome.model_trace_id)[:128]
                state["step_count"] = int(state["step_count"]) + 1
                state["frames"] = []
                checkpoint = _bounded_json(
                    state,
                    max_bytes=MAX_WORKFLOW_STATE_BYTES,
                    label="任务 stop checkpoint",
                )
            except ScheduledWorkflowStateError as exc:
                attempt.status = "blocked"
                attempt.error_type = "checkpoint_invalid"
                attempt.error_summary = str(exc)[:1000]
                attempt.completed_at = current
                db.commit()
                _finish_execution(
                    db,
                    claim,
                    status="blocked",
                    state_json=_bounded_json(
                        state,
                        max_bytes=MAX_WORKFLOW_STATE_BYTES,
                        label="任务状态",
                    ),
                    current_step_id=runtime_step_id,
                    error_code="checkpoint_invalid",
                    error_summary=str(exc),
                    now=current,
                )
                return "blocked"
            attempt.status = "succeeded"
            attempt.output_sha256 = _sha256_json(stopped_output)
            attempt.tool_call_id = str(
                outcome.tool_call_id or ""
            )[:128]
            attempt.model_trace_id = str(
                outcome.model_trace_id or ""
            )[:128]
            attempt.checkpoint_json = checkpoint
            attempt.completed_at = current
            execution.state_json = checkpoint
            execution.current_step_id = ""
            if outcome.model_trace_id:
                execution.agent_trace_id = str(
                    outcome.model_trace_id
                )[:128]
            if outcome.agent_run_id:
                execution.agent_run_id = str(
                    outcome.agent_run_id
                )[:128]
            execution.updated_at = current
            db.commit()
            _finish_execution(
                db,
                claim,
                status="succeeded",
                state_json=checkpoint,
                now=current,
            )
            return "succeeded"
        if operation == "model":
            if not isinstance(output, str) or not output.strip():
                outcome = ScheduledWorkflowStepOutcome.failed(
                    "empty_model_output",
                    "模型步骤没有生成内容",
                    retryable=False,
                    model_trace_id=outcome.model_trace_id,
                    agent_run_id=outcome.agent_run_id,
                )
                attempt.status = "blocked"
                attempt.error_type = outcome.error_code
                attempt.error_summary = outcome.error_summary
                attempt.model_trace_id = str(
                    outcome.model_trace_id or ""
                )[:128]
                attempt.completed_at = current
                if outcome.model_trace_id:
                    execution.agent_trace_id = str(
                        outcome.model_trace_id
                    )[:128]
                if outcome.agent_run_id:
                    execution.agent_run_id = str(
                        outcome.agent_run_id
                    )[:128]
                execution.updated_at = current
                db.commit()
                state_json = _bounded_json(
                    state,
                    max_bytes=MAX_WORKFLOW_STATE_BYTES,
                    label="任务状态",
                )
                _finish_execution(
                    db,
                    claim,
                    status="blocked",
                    state_json=state_json,
                    current_step_id=runtime_step_id,
                    error_code=outcome.error_code,
                    error_summary=outcome.error_summary,
                    now=current,
                )
                return "blocked"
        try:
            _apply_success_transition(
                state,
                step,
                runtime_step_id,
                outcome,
                resolved_input,
            )
            if outcome.model_trace_id:
                state["steps"][static_step_id]["model_trace_id"] = str(
                    outcome.model_trace_id
                )[:128]
            checkpoint = _bounded_json(
                state,
                max_bytes=MAX_WORKFLOW_STATE_BYTES,
                label="任务 checkpoint",
            )
        except ScheduledWorkflowStateError as exc:
            attempt.status = "blocked"
            attempt.error_type = "checkpoint_invalid"
            attempt.error_summary = str(exc)[:1000]
            attempt.completed_at = current
            db.commit()
            state_json = _bounded_json(
                state,
                max_bytes=MAX_WORKFLOW_STATE_BYTES,
                label="任务状态",
            )
            _finish_execution(
                db,
                claim,
                status="blocked",
                state_json=state_json,
                current_step_id=runtime_step_id,
                error_code="checkpoint_invalid",
                error_summary=str(exc),
                now=current,
            )
            return "blocked"

        attempt.status = "succeeded"
        attempt.output_sha256 = _sha256_json(output)
        attempt.tool_call_id = str(outcome.tool_call_id or "")[:128]
        attempt.model_trace_id = str(outcome.model_trace_id or "")[:128]
        attempt.checkpoint_json = checkpoint
        attempt.completed_at = current
        execution.state_json = checkpoint
        execution.current_step_id = ""
        if outcome.model_trace_id:
            execution.agent_trace_id = str(outcome.model_trace_id)[:128]
        if outcome.agent_run_id:
            execution.agent_run_id = str(outcome.agent_run_id)[:128]
        if outcome.outbound_run_id is not None:
            execution.outbound_run_id = int(outcome.outbound_run_id)
        execution.updated_at = current
        db.commit()


async def run_scheduled_task_workflow_worker(
    *,
    session_factory: Callable[[], Session],
    callbacks: ScheduledWorkflowCallbacks,
    owner: str = _PROCESS_OWNER,
    max_concurrency: int = DEFAULT_WORKFLOW_CONCURRENCY,
    lease_seconds: float = DEFAULT_WORKFLOW_LEASE_SECONDS,
    now: datetime | None = None,
) -> ScheduledWorkflowWorkerResult:
    """领取一批 execution 并有界并发执行；每个 execution 使用独立 Session。"""

    if type(max_concurrency) is not int or not 1 <= max_concurrency <= 32:
        raise ValueError("max_concurrency 必须是 1-32")
    discovery = session_factory()
    try:
        claims = claim_scheduled_task_executions(
            discovery,
            owner=owner,
            limit=max_concurrency,
            lease_seconds=lease_seconds,
            now=now,
        )
    finally:
        discovery.close()

    async def run_one(claim: ScheduledExecutionClaim) -> str:
        db = session_factory()
        try:
            return await execute_claimed_scheduled_task(
                db,
                claim=claim,
                callbacks=callbacks,
                session_factory=session_factory,
                lease_seconds=lease_seconds,
                clock=(
                    (lambda: now)
                    if now is not None
                    else None
                ),
            )
        except asyncio.CancelledError as exc:
            from core.durable_tasks import durable_cancel_status

            durable_status = durable_cancel_status(exc)
            if str(exc) not in {
                "durable_task_cancelled",
                "durable_task_timed_out",
                "durable_task_lease_lost",
            }:
                raise
            current = _utc_naive(now)
            try:
                execution = _require_claim(db, claim, now=current)
                (
                    db.query(ScheduledTaskStepAttempt)
                    .filter(
                        ScheduledTaskStepAttempt.execution_id
                        == claim.execution_id,
                        ScheduledTaskStepAttempt.status == "started",
                    )
                    .update(
                        {
                            ScheduledTaskStepAttempt.status: "blocked",
                            ScheduledTaskStepAttempt.error_type:
                                durable_status,
                            ScheduledTaskStepAttempt.error_summary:
                                "Agent Run 已取消或失去执行租约",
                            ScheduledTaskStepAttempt.completed_at: current,
                        },
                        synchronize_session=False,
                    )
                )
                _finish_execution(
                    db,
                    claim,
                    status="blocked",
                    state_json=str(execution.state_json or "{}"),
                    current_step_id=str(
                        execution.current_step_id or ""
                    ),
                    error_code=f"agent_run_{durable_status}",
                    error_summary="Agent Run 已取消或失去执行租约",
                    now=current,
                )
            except ScheduledWorkflowFencingError:
                return "ambiguous"
            return "blocked"
        except ScheduledWorkflowFencingError:
            return "ambiguous"
        except ScheduledWorkflowStateError as exc:
            current = _utc_naive(now)
            try:
                execution = _require_claim(db, claim, now=current)
                state_json = str(execution.state_json or "{}")
                _finish_execution(
                    db,
                    claim,
                    status="blocked",
                    state_json=state_json,
                    current_step_id=str(execution.current_step_id or ""),
                    error_code="workflow_state_invalid",
                    error_summary=str(exc),
                    now=current,
                )
            except ScheduledWorkflowFencingError:
                return "ambiguous"
            return "blocked"
        except Exception as exc:
            logger.exception(
                "Scheduled workflow execution failed execution_id=%s",
                claim.execution_id,
            )
            db.rollback()
            current = _utc_naive(now)
            try:
                execution = _require_claim(db, claim, now=current)
                _finish_execution(
                    db,
                    claim,
                    status="blocked",
                    state_json=str(execution.state_json or "{}"),
                    current_step_id=str(execution.current_step_id or ""),
                    error_code="workflow_internal_error",
                    error_summary=(
                        "任务执行器内部异常: "
                        f"{type(exc).__name__}"
                    ),
                    now=current,
                )
            except ScheduledWorkflowFencingError:
                return "ambiguous"
            return "blocked"
        finally:
            db.close()

    statuses = (
        await asyncio.gather(*(run_one(claim) for claim in claims))
        if claims
        else []
    )
    return ScheduledWorkflowWorkerResult(
        claimed=len(claims),
        succeeded=statuses.count("succeeded"),
        waiting=statuses.count("waiting"),
        failed=statuses.count("failed"),
        blocked=statuses.count("blocked"),
        ambiguous=statuses.count("ambiguous"),
    )


__all__ = [
    "DEFAULT_WORKFLOW_CONCURRENCY",
    "DEFAULT_WORKFLOW_LEASE_SECONDS",
    "MAX_WORKFLOW_OUTPUT_BYTES",
    "MAX_WORKFLOW_STATE_BYTES",
    "ScheduledExecutionClaim",
    "ScheduledExecutionEnqueueResult",
    "ScheduledWorkflowCallbacks",
    "ScheduledWorkflowContext",
    "ScheduledWorkflowError",
    "ScheduledWorkflowFencingError",
    "ScheduledWorkflowStateError",
    "ScheduledWorkflowStepOutcome",
    "ScheduledWorkflowWorkerResult",
    "claim_scheduled_task_executions",
    "enqueue_scheduled_task_execution",
    "execute_claimed_scheduled_task",
    "renew_scheduled_task_execution_lease",
    "run_scheduled_task_workflow_worker",
]
