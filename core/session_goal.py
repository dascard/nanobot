"""Session Goal、Plan Mode 与服务端批准状态机。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
import unicodedata
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session

from core.db.models.session_goal import (
    SessionGoalEventRow,
    SessionGoalRow,
    SessionPlanAssetRow,
)
from core.time_utils import db_now_naive


MAX_GOAL_OBJECTIVE_CHARS = 1_500
MAX_COMPLETION_CRITERIA = 8
MAX_COMPLETION_CRITERION_CHARS = 500
MAX_PLAN_BYTES = 256 * 1024
MAX_PLAN_CONTEXT_CHARS = 1_500


class SessionGoalError(RuntimeError):
    """Session Goal 稳定错误基类。"""


class SessionGoalNotFoundError(SessionGoalError):
    """目标不存在，或对当前 owner 不可见。"""


class SessionGoalConflictError(SessionGoalError):
    """版本、状态或计划证明冲突。"""


class SessionGoalValidationError(SessionGoalError):
    """目标或计划输入不满足边界。"""


class SessionGoalStatus(str, Enum):
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"

    @property
    def terminal(self) -> bool:
        return self in {
            SessionGoalStatus.COMPLETED,
            SessionGoalStatus.CANCELLED,
            SessionGoalStatus.FAILED,
        }


class SessionGoalMode(str, Enum):
    PLAN = "plan"
    EXECUTE = "execute"


@dataclass(frozen=True, slots=True)
class SessionGoalBudget:
    max_model_steps: int = 64
    max_tool_calls: int = 128
    max_input_tokens: int = 1_000_000
    max_output_tokens: int = 200_000
    max_cost_microunits: int = 50_000_000
    max_elapsed_seconds: int = 86_400

    def __post_init__(self) -> None:
        limits = {
            "max_model_steps": (1, 10_000),
            "max_tool_calls": (1, 100_000),
            "max_input_tokens": (1, 1_000_000_000),
            "max_output_tokens": (1, 1_000_000_000),
            "max_cost_microunits": (1, 10_000_000_000_000),
            "max_elapsed_seconds": (1, 31_536_000),
        }
        for name, (minimum, maximum) in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise SessionGoalValidationError(
                    f"{name} 必须是 {minimum}..{maximum} 的整数"
                )


@dataclass(frozen=True, slots=True)
class SessionGoalPrincipal:
    platform: str
    owner_type: str
    owner_id: str
    session_id: str

    def __post_init__(self) -> None:
        platform = str(self.platform or "").strip().lower()
        owner_type = str(self.owner_type or "").strip().lower()
        owner_id = str(self.owner_id or "").strip()
        session_id = str(self.session_id or "").strip()
        if not platform or len(platform) > 32:
            raise SessionGoalValidationError("platform 无效")
        if owner_type not in {"user", "group", "project"}:
            raise SessionGoalValidationError("owner_type 无效")
        if not owner_id or len(owner_id) > 255:
            raise SessionGoalValidationError("owner_id 无效")
        if not session_id or len(session_id) > 255:
            raise SessionGoalValidationError("session_id 无效")
        object.__setattr__(self, "platform", platform)
        object.__setattr__(self, "owner_type", owner_type)
        object.__setattr__(self, "owner_id", owner_id)
        object.__setattr__(self, "session_id", session_id)


@dataclass(frozen=True, slots=True)
class SessionPlanAsset:
    revision: int
    content: str
    content_sha256: str
    size_bytes: int
    source_run_id: str
    created_by: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SessionGoalSnapshot:
    goal_id: str
    principal: SessionGoalPrincipal
    objective: str
    completion_criteria: tuple[str, ...]
    budget: SessionGoalBudget
    status: SessionGoalStatus
    mode: SessionGoalMode
    version: int
    latest_plan_revision: int
    latest_plan_sha256: str
    approved_plan_revision: int
    approved_plan_sha256: str
    approved_by: str
    approved_at: datetime | None
    execution_started_at: datetime | None
    finished_at: datetime | None
    terminal_reason: str
    created_at: datetime
    updated_at: datetime
    snapshot_sha256: str


@dataclass(frozen=True, slots=True)
class SessionGoalRuntimePolicy:
    snapshot: SessionGoalSnapshot
    plan: SessionPlanAsset | None
    plan_writable: bool

    @property
    def goal_id(self) -> str:
        return self.snapshot.goal_id

    @property
    def mode(self) -> SessionGoalMode:
        return self.snapshot.mode

    @property
    def status(self) -> SessionGoalStatus:
        return self.snapshot.status

    @property
    def plan_readable(self) -> bool:
        return self.plan is not None

    def runtime_context(self) -> str:
        plan_content = self.plan.content if self.plan is not None else ""
        truncated = len(plan_content) > MAX_PLAN_CONTEXT_CHARS
        excerpt = (
            plan_content[:MAX_PLAN_CONTEXT_CHARS]
            if truncated
            else plan_content
        )
        payload = {
            "goal_id": self.snapshot.goal_id,
            "goal_version": self.snapshot.version,
            "snapshot_sha256": self.snapshot.snapshot_sha256,
            "status": self.snapshot.status.value,
            "mode": self.snapshot.mode.value,
            "objective": self.snapshot.objective,
            "completion_criteria": list(self.snapshot.completion_criteria),
            "budget": asdict(self.snapshot.budget),
            "plan": {
                "revision": self.plan.revision if self.plan else 0,
                "sha256": self.plan.content_sha256 if self.plan else "",
                "content": excerpt,
                "truncated": truncated,
            },
            "plan_writable": self.plan_writable,
        }
        body = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        boundary = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16].upper()
        return (
            '<session_goal_context trust="untrusted_data" '
            'instruction_authority="none">\n'
            f"NANOBOT_SESSION_GOAL_{boundary}_BEGIN\n"
            f"{body}\n"
            f"NANOBOT_SESSION_GOAL_{boundary}_END\n"
            "</session_goal_context>"
        )


def _normalize_text(value: str, *, field_name: str, max_chars: int) -> str:
    normalized = unicodedata.normalize(
        "NFC",
        str(value or "").replace("\r\n", "\n").replace("\r", "\n"),
    ).strip()
    if not normalized:
        raise SessionGoalValidationError(f"{field_name} 不能为空")
    if "\x00" in normalized or len(normalized) > max_chars:
        raise SessionGoalValidationError(f"{field_name} 超出允许范围")
    return normalized


def normalize_completion_criteria(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise SessionGoalValidationError("completion_criteria 必须是列表")
    criteria = tuple(
        _normalize_text(
            str(value or ""),
            field_name="completion criterion",
            max_chars=MAX_COMPLETION_CRITERION_CHARS,
        )
        for value in values
    )
    if not criteria or len(criteria) > MAX_COMPLETION_CRITERIA:
        raise SessionGoalValidationError(
            f"completion_criteria 必须包含 1..{MAX_COMPLETION_CRITERIA} 项"
        )
    if len(criteria) != len(set(criteria)):
        raise SessionGoalValidationError("completion_criteria 不能重复")
    return criteria


def normalize_plan_content(value: str) -> str:
    normalized = unicodedata.normalize(
        "NFC",
        str(value or "").replace("\r\n", "\n").replace("\r", "\n"),
    ).strip()
    size_bytes = len(normalized.encode("utf-8"))
    if not normalized or "\x00" in normalized or size_bytes > MAX_PLAN_BYTES:
        raise SessionGoalValidationError("计划正文为空或超过 256 KiB")
    return normalized


def _normalize_actor_id(value: str) -> str:
    actor = str(value or "").strip()
    if not actor or len(actor) > 255:
        raise SessionGoalValidationError("actor_id 无效")
    return actor


def _sha256_json(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _criteria_from_row(row: SessionGoalRow) -> tuple[str, ...]:
    try:
        raw = json.loads(str(row.completion_criteria_json or "[]"))
    except (TypeError, ValueError) as exc:
        raise SessionGoalValidationError("目标完成条件存储已损坏") from exc
    return normalize_completion_criteria(raw)


def _budget_from_row(row: SessionGoalRow) -> SessionGoalBudget:
    return SessionGoalBudget(
        max_model_steps=int(row.max_model_steps),
        max_tool_calls=int(row.max_tool_calls),
        max_input_tokens=int(row.max_input_tokens),
        max_output_tokens=int(row.max_output_tokens),
        max_cost_microunits=int(row.max_cost_microunits),
        max_elapsed_seconds=int(row.max_elapsed_seconds),
    )


def _snapshot(row: SessionGoalRow) -> SessionGoalSnapshot:
    principal = SessionGoalPrincipal(
        str(row.platform),
        str(row.owner_type),
        str(row.owner_id),
        str(row.session_id),
    )
    criteria = _criteria_from_row(row)
    budget = _budget_from_row(row)
    status = SessionGoalStatus(str(row.status))
    mode = SessionGoalMode(str(row.mode))
    digest = _sha256_json({
        "goal_id": row.goal_id,
        "principal": asdict(principal),
        "objective": row.objective,
        "completion_criteria": criteria,
        "budget": asdict(budget),
        "status": status.value,
        "mode": mode.value,
        "version": row.version,
        "latest_plan_revision": row.latest_plan_revision,
        "latest_plan_sha256": row.latest_plan_sha256,
        "approved_plan_revision": row.approved_plan_revision,
        "approved_plan_sha256": row.approved_plan_sha256,
        "approved_by": row.approved_by,
        "approved_at": row.approved_at,
        "execution_started_at": row.execution_started_at,
        "finished_at": row.finished_at,
        "terminal_reason": row.terminal_reason,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    })
    return SessionGoalSnapshot(
        goal_id=str(row.goal_id),
        principal=principal,
        objective=str(row.objective),
        completion_criteria=criteria,
        budget=budget,
        status=status,
        mode=mode,
        version=int(row.version),
        latest_plan_revision=int(row.latest_plan_revision),
        latest_plan_sha256=str(row.latest_plan_sha256 or ""),
        approved_plan_revision=int(row.approved_plan_revision),
        approved_plan_sha256=str(row.approved_plan_sha256 or ""),
        approved_by=str(row.approved_by or ""),
        approved_at=row.approved_at,
        execution_started_at=row.execution_started_at,
        finished_at=row.finished_at,
        terminal_reason=str(row.terminal_reason or ""),
        created_at=row.created_at,
        updated_at=row.updated_at,
        snapshot_sha256=digest,
    )


def _plan_asset(row: SessionPlanAssetRow) -> SessionPlanAsset:
    content = str(row.content)
    encoded = content.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    if (
        str(row.media_type) != "text/markdown"
        or int(row.size_bytes) != len(encoded)
        or str(row.content_sha256).lower() != digest
    ):
        raise SessionGoalValidationError("Session Plan 内容证明不一致")
    return SessionPlanAsset(
        revision=int(row.revision),
        content=content,
        content_sha256=digest,
        size_bytes=int(row.size_bytes),
        source_run_id=str(row.source_run_id or ""),
        created_by=str(row.created_by),
        created_at=row.created_at,
    )


class SessionGoalService:
    """使用调用方事务管理 Session Goal，并强制 owner、版本和状态。"""

    def __init__(self, db: Session) -> None:
        if not isinstance(db, Session):
            raise TypeError("db 必须是 SQLAlchemy Session")
        self._db = db

    def _owned_row(
        self,
        goal_id: str,
        principal: SessionGoalPrincipal,
    ) -> SessionGoalRow:
        normalized_id = str(goal_id or "").strip()
        row = (
            self._db.query(SessionGoalRow)
            .filter(
                SessionGoalRow.goal_id == normalized_id,
                SessionGoalRow.platform == principal.platform,
                SessionGoalRow.owner_type == principal.owner_type,
                SessionGoalRow.owner_id == principal.owner_id,
                SessionGoalRow.session_id == principal.session_id,
            )
            .first()
        )
        if row is None:
            raise SessionGoalNotFoundError("Session Goal 不存在或 owner 不匹配")
        return row

    @staticmethod
    def _require_version(row: SessionGoalRow, expected_version: int) -> None:
        if type(expected_version) is not int or expected_version < 1:
            raise SessionGoalValidationError("expected_version 必须是正整数")
        if int(row.version) != expected_version:
            raise SessionGoalConflictError(
                f"Session Goal 版本冲突：当前为 {row.version}"
            )

    def _append_event(
        self,
        *,
        row: SessionGoalRow,
        event_kind: str,
        goal_version: int,
        previous_status: str,
        previous_mode: str,
        actor_id: str,
        source_run_id: str = "",
        plan_revision: int = 0,
        plan_sha256: str = "",
        occurred_at: datetime,
    ) -> None:
        actor = _normalize_actor_id(actor_id)
        event_payload = {
            "goal_id": row.goal_id,
            "goal_version": goal_version,
            "event_kind": event_kind,
            "previous_status": previous_status,
            "current_status": row.status,
            "previous_mode": previous_mode,
            "current_mode": row.mode,
            "plan_revision": plan_revision,
            "plan_sha256": plan_sha256,
            "actor_id": actor,
            "source_run_id": str(source_run_id or ""),
            "occurred_at": occurred_at.isoformat(),
        }
        event_sha = _sha256_json(event_payload)
        self._db.add(SessionGoalEventRow(
            event_id=f"sge_{uuid4().hex}",
            goal_id=str(row.goal_id),
            goal_version=goal_version,
            event_kind=event_kind,
            previous_status=previous_status,
            current_status=str(row.status),
            previous_mode=previous_mode,
            current_mode=str(row.mode),
            plan_revision=plan_revision,
            plan_sha256=plan_sha256,
            actor_id=actor,
            source_run_id=str(source_run_id or "")[:160],
            event_sha256=event_sha,
            occurred_at=occurred_at,
        ))

    def _apply_update(
        self,
        *,
        row: SessionGoalRow,
        expected_version: int,
        values: dict[str, object],
        event_kind: str,
        actor_id: str,
        source_run_id: str = "",
        plan_revision: int = 0,
        plan_sha256: str = "",
    ) -> SessionGoalRow:
        actor = _normalize_actor_id(actor_id)
        self._require_version(row, expected_version)
        previous_status = str(row.status)
        previous_mode = str(row.mode)
        occurred_at = db_now_naive()
        next_version = expected_version + 1
        update_values = {
            **values,
            "version": next_version,
            "updated_at": occurred_at,
        }
        result = self._db.execute(
            update(SessionGoalRow)
            .where(
                SessionGoalRow.goal_id == row.goal_id,
                SessionGoalRow.version == expected_version,
            )
            .values(**update_values)
        )
        if result.rowcount != 1:
            raise SessionGoalConflictError("Session Goal 被并发修改")
        self._db.expire(row)
        refreshed = self._db.query(SessionGoalRow).filter(
            SessionGoalRow.goal_id == row.goal_id
        ).one()
        self._append_event(
            row=refreshed,
            event_kind=event_kind,
            goal_version=next_version,
            previous_status=previous_status,
            previous_mode=previous_mode,
            actor_id=actor,
            source_run_id=source_run_id,
            plan_revision=plan_revision,
            plan_sha256=plan_sha256,
            occurred_at=occurred_at,
        )
        self._db.flush()
        return refreshed

    def create_goal(
        self,
        *,
        principal: SessionGoalPrincipal,
        objective: str,
        completion_criteria: object,
        budget: SessionGoalBudget,
        actor_id: str,
    ) -> SessionGoalSnapshot:
        actor = _normalize_actor_id(actor_id)
        objective_text = _normalize_text(
            objective,
            field_name="objective",
            max_chars=MAX_GOAL_OBJECTIVE_CHARS,
        )
        criteria = normalize_completion_criteria(completion_criteria)
        if not isinstance(budget, SessionGoalBudget):
            raise SessionGoalValidationError("budget 无效")
        now = db_now_naive()
        row = SessionGoalRow(
            goal_id=f"goal_{uuid4().hex}",
            platform=principal.platform,
            owner_type=principal.owner_type,
            owner_id=principal.owner_id,
            session_id=principal.session_id,
            objective=objective_text,
            completion_criteria_json=json.dumps(
                criteria,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            **asdict(budget),
            status=SessionGoalStatus.PLANNING.value,
            mode=SessionGoalMode.PLAN.value,
            version=1,
            created_at=now,
            updated_at=now,
        )
        self._db.add(row)
        self._db.flush()
        self._append_event(
            row=row,
            event_kind="created",
            goal_version=1,
            previous_status="",
            previous_mode="",
            actor_id=actor,
            occurred_at=now,
        )
        self._db.flush()
        return _snapshot(row)

    def get_goal(
        self,
        goal_id: str,
        principal: SessionGoalPrincipal,
    ) -> SessionGoalSnapshot:
        return _snapshot(self._owned_row(goal_id, principal))

    def get_plan(
        self,
        goal_id: str,
        principal: SessionGoalPrincipal,
        *,
        revision: int = 0,
    ) -> SessionPlanAsset | None:
        goal = self._owned_row(goal_id, principal)
        target = int(revision or goal.latest_plan_revision)
        if target <= 0:
            return None
        row = (
            self._db.query(SessionPlanAssetRow)
            .filter(
                SessionPlanAssetRow.goal_id == goal.goal_id,
                SessionPlanAssetRow.revision == target,
            )
            .first()
        )
        if row is None:
            raise SessionGoalValidationError("Session Plan 版本存储已损坏")
        return _plan_asset(row)

    def write_plan(
        self,
        *,
        goal_id: str,
        principal: SessionGoalPrincipal,
        content: str,
        expected_version: int,
        actor_id: str,
        source_run_id: str = "",
    ) -> SessionGoalSnapshot:
        row = self._owned_row(goal_id, principal)
        actor = _normalize_actor_id(actor_id)
        self._require_version(row, expected_version)
        if SessionGoalStatus(str(row.status)) is not SessionGoalStatus.PLANNING:
            raise SessionGoalConflictError("当前状态不允许写入计划")
        normalized = normalize_plan_content(content)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        revision = int(row.latest_plan_revision) + 1
        self._db.add(SessionPlanAssetRow(
            goal_id=row.goal_id,
            revision=revision,
            content=normalized,
            content_sha256=digest,
            size_bytes=len(normalized.encode("utf-8")),
            source_run_id=str(source_run_id or "")[:160],
            created_by=actor,
            created_at=db_now_naive(),
        ))
        updated = self._apply_update(
            row=row,
            expected_version=expected_version,
            values={
                "status": SessionGoalStatus.PLANNING.value,
                "mode": SessionGoalMode.PLAN.value,
                "latest_plan_revision": revision,
                "latest_plan_sha256": digest,
                "approved_plan_revision": 0,
                "approved_plan_sha256": "",
                "approved_by": "",
                "approved_at": None,
            },
            event_kind="plan_written",
            actor_id=actor,
            source_run_id=source_run_id,
            plan_revision=revision,
            plan_sha256=digest,
        )
        return _snapshot(updated)

    def request_approval(
        self,
        *,
        goal_id: str,
        principal: SessionGoalPrincipal,
        expected_version: int,
        actor_id: str,
    ) -> SessionGoalSnapshot:
        row = self._owned_row(goal_id, principal)
        self._require_version(row, expected_version)
        if SessionGoalStatus(str(row.status)) is not SessionGoalStatus.PLANNING:
            raise SessionGoalConflictError("当前状态不能请求批准")
        if int(row.latest_plan_revision) <= 0 or not row.latest_plan_sha256:
            raise SessionGoalConflictError("尚无可批准的计划版本")
        updated = self._apply_update(
            row=row,
            expected_version=expected_version,
            values={"status": SessionGoalStatus.AWAITING_APPROVAL.value},
            event_kind="approval_requested",
            actor_id=actor_id,
            plan_revision=int(row.latest_plan_revision),
            plan_sha256=str(row.latest_plan_sha256),
        )
        return _snapshot(updated)

    def approve(
        self,
        *,
        goal_id: str,
        principal: SessionGoalPrincipal,
        expected_version: int,
        expected_plan_revision: int,
        expected_plan_sha256: str,
        approver_id: str,
    ) -> SessionGoalSnapshot:
        row = self._owned_row(goal_id, principal)
        self._require_version(row, expected_version)
        if (
            SessionGoalStatus(str(row.status))
            is not SessionGoalStatus.AWAITING_APPROVAL
        ):
            raise SessionGoalConflictError("当前状态不能批准计划")
        digest = str(expected_plan_sha256 or "").strip().lower()
        if (
            int(row.latest_plan_revision) != expected_plan_revision
            or str(row.latest_plan_sha256) != digest
        ):
            raise SessionGoalConflictError("待批准计划版本或摘要已变化")
        now = db_now_naive()
        updated = self._apply_update(
            row=row,
            expected_version=expected_version,
            values={
                "status": SessionGoalStatus.APPROVED.value,
                "mode": SessionGoalMode.PLAN.value,
                "approved_plan_revision": expected_plan_revision,
                "approved_plan_sha256": digest,
                "approved_by": str(approver_id or "")[:255],
                "approved_at": now,
            },
            event_kind="approved",
            actor_id=approver_id,
            plan_revision=expected_plan_revision,
            plan_sha256=digest,
        )
        return _snapshot(updated)

    def start_execution(
        self,
        *,
        goal_id: str,
        principal: SessionGoalPrincipal,
        expected_version: int,
        actor_id: str,
    ) -> SessionGoalSnapshot:
        row = self._owned_row(goal_id, principal)
        self._require_version(row, expected_version)
        if SessionGoalStatus(str(row.status)) is not SessionGoalStatus.APPROVED:
            raise SessionGoalConflictError("只有已批准计划才能退出 Plan Mode")
        if (
            int(row.approved_plan_revision) != int(row.latest_plan_revision)
            or str(row.approved_plan_sha256) != str(row.latest_plan_sha256)
        ):
            raise SessionGoalConflictError("批准证明与最新计划不一致")
        updated = self._apply_update(
            row=row,
            expected_version=expected_version,
            values={
                "status": SessionGoalStatus.EXECUTING.value,
                "mode": SessionGoalMode.EXECUTE.value,
                "execution_started_at": db_now_naive(),
            },
            event_kind="execution_started",
            actor_id=actor_id,
            plan_revision=int(row.approved_plan_revision),
            plan_sha256=str(row.approved_plan_sha256),
        )
        return _snapshot(updated)

    def finish(
        self,
        *,
        goal_id: str,
        principal: SessionGoalPrincipal,
        expected_version: int,
        actor_id: str,
        status: SessionGoalStatus,
        reason: str,
    ) -> SessionGoalSnapshot:
        if status not in {
            SessionGoalStatus.COMPLETED,
            SessionGoalStatus.CANCELLED,
            SessionGoalStatus.FAILED,
        }:
            raise SessionGoalValidationError("终态无效")
        row = self._owned_row(goal_id, principal)
        self._require_version(row, expected_version)
        current = SessionGoalStatus(str(row.status))
        if current.terminal:
            raise SessionGoalConflictError("Session Goal 已经终止")
        if status is SessionGoalStatus.COMPLETED and (
            current is not SessionGoalStatus.EXECUTING
        ):
            raise SessionGoalConflictError("只有执行中的目标可以完成")
        normalized_reason = _normalize_text(
            reason,
            field_name="terminal reason",
            max_chars=512,
        )
        updated = self._apply_update(
            row=row,
            expected_version=expected_version,
            values={
                "status": status.value,
                "finished_at": db_now_naive(),
                "terminal_reason": normalized_reason,
            },
            event_kind=status.value,
            actor_id=actor_id,
            plan_revision=int(row.approved_plan_revision),
            plan_sha256=str(row.approved_plan_sha256 or ""),
        )
        return _snapshot(updated)

    def runtime_policy(
        self,
        *,
        goal_id: str,
        principal: SessionGoalPrincipal,
    ) -> SessionGoalRuntimePolicy:
        snapshot = self.get_goal(goal_id, principal)
        if snapshot.status.terminal:
            raise SessionGoalConflictError("终态 Session Goal 不能绑定新 Turn")
        revision = (
            snapshot.approved_plan_revision
            if snapshot.status in {
                SessionGoalStatus.APPROVED,
                SessionGoalStatus.EXECUTING,
            }
            else snapshot.latest_plan_revision
        )
        plan = self.get_plan(goal_id, principal, revision=revision)
        expected_plan_sha256 = (
            snapshot.approved_plan_sha256
            if snapshot.status
            in {SessionGoalStatus.APPROVED, SessionGoalStatus.EXECUTING}
            else snapshot.latest_plan_sha256
        )
        if (
            (plan is None and expected_plan_sha256)
            or (
                plan is not None
                and plan.content_sha256 != expected_plan_sha256
            )
        ):
            raise SessionGoalValidationError("Session Plan 投影证明不一致")
        return SessionGoalRuntimePolicy(
            snapshot=snapshot,
            plan=plan,
            plan_writable=(snapshot.status is SessionGoalStatus.PLANNING),
        )


def join_session_goal_project_context(
    project_context: str,
    policy: SessionGoalRuntimePolicy | None,
) -> str:
    existing = str(project_context or "").strip()
    if policy is None:
        return existing
    goal_context = policy.runtime_context()
    return f"{goal_context}\n\n{existing}".strip()


__all__ = [
    "MAX_COMPLETION_CRITERIA",
    "MAX_COMPLETION_CRITERION_CHARS",
    "MAX_GOAL_OBJECTIVE_CHARS",
    "MAX_PLAN_BYTES",
    "SessionGoalBudget",
    "SessionGoalConflictError",
    "SessionGoalError",
    "SessionGoalMode",
    "SessionGoalNotFoundError",
    "SessionGoalPrincipal",
    "SessionGoalRuntimePolicy",
    "SessionGoalService",
    "SessionGoalSnapshot",
    "SessionGoalStatus",
    "SessionGoalValidationError",
    "SessionPlanAsset",
    "join_session_goal_project_context",
    "normalize_completion_criteria",
    "normalize_plan_content",
]
