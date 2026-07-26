"""Server 主动拉取 sandboxd Lease 事实并收敛业务账本。"""

from __future__ import annotations

import logging
import re
import secrets
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.database import (
    SANDBOX_LEASE_NONTERMINAL_STATUSES,
    SandboxControllerState,
    SandboxLease,
    SandboxRun,
    Workspace,
)
from core.sandbox.backend import SandboxBackend
from core.sandbox.client import HttpSandboxdAdminBackend
from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.paths import validate_workspace_id
from core.settings_service import settings
from core.time_utils import db_now_naive


logger = logging.getLogger("nanobot.sandbox.lease_reconciler")
_STATE_KEY = "sandboxd"
_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})
_CONTROLLER_EPOCH_RE = re.compile(r"sbxctl_[0-9a-f]{32}")
_LEASE_ID_RE = re.compile(r"sbxlease_[A-Za-z0-9_-]{1,55}")
_PROCESS_ID_RE = re.compile(r"sbxrun_[A-Za-z0-9_-]{1,56}")
_PROFILE_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_TERMINATION_REASON_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_PROCESS_EXECUTION_STATUSES = frozenset({
    "running",
    "completed",
    "failed",
    "cancelled",
})
_PROCESS_STATES = frozenset({"running", "exited", "lost"})


@dataclass(frozen=True, slots=True)
class ReconcileClaim:
    owner: str
    token: str
    previous_controller_epoch: str
    expires_at: datetime


def _data(response: Mapping[str, Any]) -> dict[str, Any]:
    value = response.get("data")
    if not isinstance(value, Mapping):
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "sandboxd Lease 对账响应无效",
            retryable=True,
            stop=False,
        )
    return dict(value)


def _safe_datetime(value: object) -> datetime | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(timestamp)
    except (OSError, OverflowError, ValueError):
        return None


def _require_controller_epoch(value: object) -> str:
    normalized = str(value or "")
    if not _CONTROLLER_EPOCH_RE.fullmatch(normalized):
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "sandboxd controller epoch 无效",
        )
    return normalized


def _require_lease_id(value: object) -> str:
    normalized = str(value or "")
    if not _LEASE_ID_RE.fullmatch(normalized):
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "sandboxd Lease 标识无效",
        )
    return normalized


def _require_process_id(value: object) -> str:
    normalized = str(value or "")
    if not _PROCESS_ID_RE.fullmatch(normalized):
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "sandboxd 进程标识无效",
        )
    return normalized


def _require_workspace_id(value: object) -> str:
    try:
        return validate_workspace_id(str(value or ""))
    except SandboxServiceError as exc:
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "sandboxd Workspace 标识无效",
        ) from exc


def _require_profile_id(value: object) -> str:
    normalized = str(value or "")
    if not _PROFILE_ID_RE.fullmatch(normalized):
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "sandboxd Profile 标识无效",
        )
    return normalized


def _require_id_list(
    value: object,
    *,
    validator: Callable[[object], str],
    summary: str,
) -> list[str]:
    if not isinstance(value, list):
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            summary,
        )
    return [validator(item) for item in value]


def _require_nonnegative_int(
    value: object,
    *,
    nullable: bool = False,
) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "sandboxd 进程数值事实无效",
        )
    normalized = value
    if normalized < 0:
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "sandboxd 进程数值事实无效",
        )
    return normalized


class SandboxLeaseReconciler:
    """带数据库 leader fencing 的周期主动拉取 reconciler。"""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        backend_factory: Callable[[], SandboxBackend] | None = None,
        worker_id: str = "",
        leader_lease_seconds: int = 60,
        interval_seconds: float = 15.0,
        provisioning_grace_seconds: int = 60,
    ) -> None:
        self.session_factory = session_factory
        self.backend_factory = backend_factory or self._default_backend
        self.worker_id = (
            str(worker_id or "")
            or f"sandbox-lease-reconciler-{secrets.token_hex(8)}"
        )[:128]
        self.leader_lease_seconds = max(10, int(leader_lease_seconds))
        self.interval_seconds = max(1.0, float(interval_seconds))
        self.provisioning_grace_seconds = max(
            1,
            int(provisioning_grace_seconds),
        )
        self._run_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _default_backend() -> SandboxBackend:
        return HttpSandboxdAdminBackend(
            socket_path=settings.get_str("sandbox.sandboxd_socket"),
            token_file=settings.get_str(
                "sandbox.sandboxd_admin_token_file"
            ),
            timeout_seconds=settings.get_float(
                "sandbox.backend_timeout_seconds",
                15,
            ),
            run_timeout_seconds=settings.get_float(
                "sandbox.run_timeout_seconds",
                165,
            ),
        )

    @staticmethod
    def _ensure_state_row(db: Session) -> SandboxControllerState:
        row = db.get(SandboxControllerState, _STATE_KEY)
        if row is not None:
            return row
        try:
            with db.begin_nested():
                row = SandboxControllerState(state_key=_STATE_KEY)
                db.add(row)
                db.flush()
        except IntegrityError:
            db.expire_all()
            row = db.get(SandboxControllerState, _STATE_KEY)
            if row is None:
                raise
        return row

    def _claim(self) -> ReconcileClaim | None:
        db = self.session_factory()
        try:
            self._ensure_state_row(db)
            db.commit()
            now = db_now_naive()
            expires = now + timedelta(seconds=self.leader_lease_seconds)
            token = secrets.token_hex(16)
            changed = db.execute(
                update(SandboxControllerState)
                .where(
                    SandboxControllerState.state_key == _STATE_KEY,
                    or_(
                        SandboxControllerState.leader_expires_at.is_(None),
                        SandboxControllerState.leader_expires_at <= now,
                        SandboxControllerState.leader_owner
                        == self.worker_id,
                    ),
                )
                .values(
                    leader_owner=self.worker_id,
                    leader_token=token,
                    leader_expires_at=expires,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if int(changed.rowcount or 0) != 1:
                db.rollback()
                return None
            db.commit()
            db.expire_all()
            row = db.get(SandboxControllerState, _STATE_KEY)
            if row is None or str(row.leader_token or "") != token:
                return None
            return ReconcileClaim(
                owner=self.worker_id,
                token=token,
                previous_controller_epoch=str(
                    row.controller_epoch or ""
                ),
                expires_at=expires,
            )
        finally:
            db.close()

    @staticmethod
    def _termination_status(reason: str) -> tuple[str, str]:
        if reason in {"lease_idle_ttl", "lease_max_ttl"}:
            return "expired", "failed"
        if reason in {
            "cancelled",
            "admin_terminated",
            "kill_switch",
            "lease_recycled",
            "quota_reconfigured",
        }:
            return "stopped", "cancelled"
        return "failed", "failed"

    @staticmethod
    def _settle_active_runs(
        db: Session,
        lease: SandboxLease,
        *,
        reason: str,
        now: datetime,
    ) -> None:
        _lease_status, run_status = (
            SandboxLeaseReconciler._termination_status(reason)
        )
        rows = (
            db.query(SandboxRun)
            .filter(
                SandboxRun.lease_id == lease.lease_id,
                SandboxRun.status.in_({"pending", "running"}),
            )
            .all()
        )
        for row in rows:
            if str(row.status) in _TERMINAL_RUN_STATUSES:
                continue
            row.status = run_status
            row.process_state = "lost"
            row.termination_reason = reason[:64]
            row.finished_at = now
            row.last_seen_at = now
            row.updated_at = now

    def _terminate_lease_row(
        self,
        db: Session,
        lease: SandboxLease,
        *,
        reason: str,
        now: datetime,
    ) -> None:
        if str(lease.status) not in SANDBOX_LEASE_NONTERMINAL_STATUSES:
            return
        lease_status, _run_status = self._termination_status(reason)
        lease.status = lease_status
        lease.stopped_at = now
        lease.reconciled_at = now
        lease.last_error_code = reason[:64]
        lease.last_error_summary = (
            "sandboxd controller 重启，旧 Lease 已回收"
            if reason == "controller_restarted"
            else "Sandbox Lease 已按控制器事实收敛"
        )[:255]
        self._settle_active_runs(db, lease, reason=reason, now=now)

    @staticmethod
    def _fact_matches(lease: SandboxLease, fact: Mapping[str, Any]) -> bool:
        return (
            str(fact.get("lease_id") or "") == str(lease.lease_id)
            and str(fact.get("workspace_id") or "")
            == str(lease.workspace_id)
            and str(fact.get("profile_id") or "") == str(lease.profile_id)
            and str(fact.get("catalog_generation") or "")
            == str(lease.catalog_generation)
            and str(fact.get("policy_sha256") or "").lower()
            == str(lease.policy_sha256).lower()
        )

    def _settle(
        self,
        claim: ReconcileClaim,
        *,
        controller_data: Mapping[str, Any],
        sandboxd_reconcile_data: Mapping[str, Any],
        lease_data: Mapping[str, Any],
        process_data: Mapping[str, Any],
    ) -> dict[str, int]:
        current_epoch = _require_controller_epoch(
            controller_data.get("controller_epoch")
        )
        recovered_lease_ids = _require_id_list(
            controller_data.get("recovered_lease_ids"),
            validator=_require_lease_id,
            summary="sandboxd controller 恢复 Lease 列表无效",
        )
        _require_id_list(
            controller_data.get("recovered_process_ids"),
            validator=_require_process_id,
            summary="sandboxd controller 恢复进程列表无效",
        )
        reconcile_epoch = _require_controller_epoch(
            sandboxd_reconcile_data.get("controller_epoch")
        )
        if reconcile_epoch != current_epoch:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "sandboxd 对账响应来自其他 controller epoch",
            )
        _require_id_list(
            sandboxd_reconcile_data.get("failed_lease_ids"),
            validator=_require_lease_id,
            summary="sandboxd Lease 对账失败列表无效",
        )
        raw_leases = lease_data.get("leases")
        if not isinstance(raw_leases, list) or any(
            not isinstance(item, Mapping) for item in raw_leases
        ):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "sandboxd Lease 列表响应无效",
            )
        facts: dict[str, dict[str, Any]] = {}
        for raw_fact in raw_leases:
            fact = dict(raw_fact)
            lease_id = _require_lease_id(fact.get("lease_id"))
            if lease_id in facts:
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "sandboxd Lease 列表包含重复标识",
                )
            _require_controller_epoch(fact.get("controller_epoch"))
            fact["active_process_ids"] = _require_id_list(
                fact.get("active_process_ids"),
                validator=_require_process_id,
                summary="sandboxd Lease 活动进程列表无效",
            )
            facts[lease_id] = fact

        process_epoch = _require_controller_epoch(
            process_data.get("controller_epoch")
        )
        if process_epoch != current_epoch:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "sandboxd 进程事实来自其他 controller epoch",
            )
        raw_processes = process_data.get("processes")
        if not isinstance(raw_processes, list) or any(
            not isinstance(item, Mapping) for item in raw_processes
        ):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "sandboxd 进程事实列表无效",
            )
        process_facts: dict[str, dict[str, Any]] = {}
        for raw_process in raw_processes:
            process = dict(raw_process)
            process_id = _require_process_id(
                process.get("process_id")
            )
            if process_id in process_facts:
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "sandboxd 进程事实包含重复标识",
                )
            lease_id = _require_lease_id(process.get("lease_id"))
            process["workspace_id"] = _require_workspace_id(
                process.get("workspace_id")
            )
            process["profile_id"] = _require_profile_id(
                process.get("profile_id")
            )
            process["controller_epoch"] = _require_controller_epoch(
                process.get("controller_epoch")
            )
            execution_status = str(
                process.get("execution_status") or ""
            )
            process_state = str(process.get("process_state") or "")
            if (
                execution_status not in _PROCESS_EXECUTION_STATUSES
                or process_state not in _PROCESS_STATES
                or not isinstance(process.get("lease_recycled"), bool)
                or not isinstance(process.get("stdout_truncated"), bool)
                or not isinstance(process.get("stderr_truncated"), bool)
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "sandboxd 进程状态事实无效",
                )
            process["exit_code"] = _require_nonnegative_int(
                process.get("exit_code"),
                nullable=True,
            )
            for field_name in (
                "stdout_bytes",
                "stderr_bytes",
                "cpu_time_ms",
                "peak_memory_bytes",
            ):
                process[field_name] = _require_nonnegative_int(
                    process.get(field_name)
                )
            process["affected_process_ids"] = _require_id_list(
                process.get("affected_process_ids"),
                validator=_require_process_id,
                summary="sandboxd 进程受影响标识列表无效",
            )
            if len(process["affected_process_ids"]) != len(
                set(process["affected_process_ids"])
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "sandboxd 进程受影响标识列表包含重复值",
                )
            reason = str(process.get("termination_reason") or "")
            if execution_status == "running":
                if (
                    process_state != "running"
                    or process["exit_code"] is not None
                    or reason
                    or process["lease_recycled"]
                ):
                    raise SandboxServiceError(
                        SandboxErrorCode.RUNTIME_UNAVAILABLE,
                        "sandboxd 运行中进程事实无效",
                    )
            elif (
                not _TERMINATION_REASON_RE.fullmatch(reason)
                or process_state not in {"exited", "lost"}
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "sandboxd 终态进程事实无效",
                )
            if execution_status != "running":
                if process["lease_recycled"] is True:
                    if (
                        process_state != "lost"
                        or process["exit_code"] is not None
                        or process_id
                        not in process["affected_process_ids"]
                    ):
                        raise SandboxServiceError(
                            SandboxErrorCode.RUNTIME_UNAVAILABLE,
                            "sandboxd Lease 回收进程事实无效",
                        )
                elif (
                    process_state != "exited"
                    or process["affected_process_ids"] != [process_id]
                    or process["exit_code"] is None
                    or (
                        execution_status == "completed"
                        and (
                            process["exit_code"] != 0
                            or reason != "completed"
                        )
                    )
                    or (
                        execution_status == "failed"
                        and (
                            process["exit_code"] == 0
                            or reason != "nonzero_exit"
                        )
                    )
                    or execution_status == "cancelled"
                ):
                    raise SandboxServiceError(
                        SandboxErrorCode.RUNTIME_UNAVAILABLE,
                        "sandboxd 普通退出进程事实无效",
                    )
            process["lease_id"] = lease_id
            process["execution_status"] = execution_status
            process["process_state"] = process_state
            process["termination_reason"] = reason
            process_facts[process_id] = process
        reasons: dict[str, str] = {}
        for lease_id in recovered_lease_ids:
            reasons[lease_id] = "controller_restarted"
        recycled = sandboxd_reconcile_data.get("recycled")
        if not isinstance(recycled, list):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "sandboxd Lease 对账明细无效",
            )
        for item in recycled:
            if not isinstance(item, Mapping):
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "sandboxd Lease 对账明细无效",
                )
            lease_id = _require_lease_id(item.get("lease_id"))
            affected = _require_id_list(
                item.get("affected_process_ids"),
                validator=_require_process_id,
                summary="sandboxd Lease 对账受影响进程列表无效",
            )
            del affected
            reasons[lease_id] = str(
                item.get("termination_reason") or "lease_missing"
            )
        for process in process_facts.values():
            if process["lease_recycled"] is True:
                reasons.setdefault(
                    str(process["lease_id"]),
                    str(
                        process["termination_reason"]
                        or "lease_recycled"
                    ),
                )

        db = self.session_factory()
        try:
            now = db_now_naive()
            state = db.get(SandboxControllerState, _STATE_KEY)
            if (
                state is None
                or str(state.leader_owner or "") != claim.owner
                or str(state.leader_token or "") != claim.token
                or state.leader_expires_at is None
                or state.leader_expires_at <= now
            ):
                db.rollback()
                return {"observed": 0, "updated": 0, "terminated": 0}

            observed = 0
            updated = 0
            terminated = 0
            leases = (
                db.query(SandboxLease)
                .filter(
                    SandboxLease.status.in_(
                        SANDBOX_LEASE_NONTERMINAL_STATUSES
                    )
                )
                .all()
            )
            for lease in leases:
                observed += 1
                lease_id = str(lease.lease_id)
                fact = facts.get(lease_id)
                reason = reasons.get(lease_id, "")
                if (
                    not reason
                    and str(lease.controller_epoch or "")
                    and str(lease.controller_epoch) != current_epoch
                ):
                    reason = "controller_restarted"
                if not reason and fact is None:
                    created_at = lease.created_at or now
                    if (
                        str(lease.status) == "provisioning"
                        and created_at
                        > now
                        - timedelta(
                            seconds=self.provisioning_grace_seconds
                        )
                    ):
                        continue
                    reason = "lease_missing"
                if reason:
                    self._terminate_lease_row(
                        db,
                        lease,
                        reason=reason,
                        now=now,
                    )
                    terminated += 1
                    continue
                if fact is None:
                    continue
                if (
                    str(fact.get("controller_epoch") or "") != current_epoch
                    or not self._fact_matches(lease, fact)
                    or fact.get("present") is not True
                    or fact.get("running") is not True
                ):
                    self._terminate_lease_row(
                        db,
                        lease,
                        reason="lease_fact_mismatch",
                        now=now,
                    )
                    terminated += 1
                    continue

                lease.status = (
                    "active"
                    if str(fact.get("status") or "") == "active"
                    else "idle"
                )
                lease.controller_epoch = current_epoch
                lease.image_digest = str(
                    fact.get("image_digest") or ""
                )[:255]
                lease.last_active_at = (
                    _safe_datetime(fact.get("last_active_at_unix"))
                    or lease.last_active_at
                    or now
                )
                lease.idle_expires_at = _safe_datetime(
                    fact.get("idle_expires_at_unix")
                )
                lease.max_expires_at = _safe_datetime(
                    fact.get("max_expires_at_unix")
                )
                lease.reconciled_at = now
                lease.last_error_code = ""
                lease.last_error_summary = ""
                active_process_ids = set(fact["active_process_ids"])
                for process_id in active_process_ids:
                    process = process_facts.get(process_id)
                    if (
                        process is None
                        or process["execution_status"] != "running"
                        or str(process["lease_id"]) != lease_id
                        or str(process["workspace_id"])
                        != str(lease.workspace_id)
                        or str(process.get("profile_id") or "")
                        != str(lease.profile_id)
                        or str(process["controller_epoch"])
                        != current_epoch
                    ):
                        raise SandboxServiceError(
                            SandboxErrorCode.RUNTIME_UNAVAILABLE,
                            "sandboxd Lease 与活动进程事实不一致",
                        )
                active_rows = (
                    db.query(SandboxRun)
                    .filter(
                        SandboxRun.lease_id == lease_id,
                        SandboxRun.status.in_({"pending", "running"}),
                    )
                    .all()
                )
                for run in active_rows:
                    process_id = str(run.run_id)
                    process = process_facts.get(process_id)
                    if process_id in active_process_ids:
                        run.status = "running"
                        run.process_state = "running"
                        run.started_at = run.started_at or now
                        run.last_seen_at = now
                        run.updated_at = now
                        continue
                    if process is None:
                        continue
                    if (
                        str(process["lease_id"]) != lease_id
                        or str(process["workspace_id"])
                        != str(lease.workspace_id)
                        or str(process.get("profile_id") or "")
                        != str(lease.profile_id)
                        or str(process["controller_epoch"])
                        != current_epoch
                        or process["execution_status"] == "running"
                    ):
                        raise SandboxServiceError(
                            SandboxErrorCode.RUNTIME_UNAVAILABLE,
                            "sandboxd 终态进程归属事实不一致",
                        )
                    run.status = str(process["execution_status"])
                    run.process_state = str(process["process_state"])
                    run.exit_code = process["exit_code"]
                    run.termination_reason = str(
                        process["termination_reason"]
                    )[:64]
                    run.cpu_time_ms = int(process["cpu_time_ms"] or 0)
                    run.peak_memory_bytes = int(
                        process["peak_memory_bytes"] or 0
                    )
                    run.stdout_bytes = int(
                        process["stdout_bytes"] or 0
                    )
                    run.stderr_bytes = int(
                        process["stderr_bytes"] or 0
                    )
                    run.stdout_truncated = bool(
                        process["stdout_truncated"]
                    )
                    run.stderr_truncated = bool(
                        process["stderr_truncated"]
                    )
                    run.finished_at = (
                        _safe_datetime(
                            process.get("finished_at_unix")
                        )
                        or now
                    )
                    run.last_seen_at = now
                    run.updated_at = now
                updated += 1

            state.controller_epoch = current_epoch
            state.reconciled_at = now
            state.last_error_code = ""
            state.last_error_summary = ""
            state.leader_owner = ""
            state.leader_token = ""
            state.leader_expires_at = None
            state.updated_at = now
            db.commit()
            return {
                "observed": observed,
                "updated": updated,
                "terminated": terminated,
            }
        finally:
            db.close()

    def _project_workspace_usage(self, usage_data: Mapping[str, Any]) -> None:
        """把 sandboxd 核算的 Workspace 用量事实投影到业务账本。

        数据库 used_bytes 只是管理端投影，配额强制墙仍是宿主 project
        quota；dirty 快照代表 sandboxd 侧尚未完成对账，跳过等下轮。
        """

        facts = usage_data.get("workspaces")
        if not isinstance(facts, list):
            return
        db = self.session_factory()
        try:
            now = db_now_naive()
            changed = False
            for fact in facts:
                if not isinstance(fact, Mapping) or fact.get("dirty") is True:
                    continue
                try:
                    workspace_id = validate_workspace_id(
                        str(fact.get("workspace_id") or "")
                    )
                except SandboxServiceError:
                    continue
                observed = fact.get("workspace_bytes")
                if (
                    isinstance(observed, bool)
                    or not isinstance(observed, int)
                    or observed < 0
                ):
                    continue
                result = db.execute(
                    update(Workspace)
                    .where(
                        Workspace.id == workspace_id,
                        Workspace.used_bytes != int(observed),
                    )
                    .values(used_bytes=int(observed), updated_at=now)
                    .execution_options(synchronize_session=False)
                )
                if int(result.rowcount or 0):
                    changed = True
            if changed:
                db.commit()
            else:
                db.rollback()
        finally:
            db.close()

    def _record_failure(
        self,
        claim: ReconcileClaim,
        *,
        code: str,
        summary: str,
    ) -> None:
        db = self.session_factory()
        try:
            now = db_now_naive()
            changed = db.execute(
                update(SandboxControllerState)
                .where(
                    SandboxControllerState.state_key == _STATE_KEY,
                    SandboxControllerState.leader_owner == claim.owner,
                    SandboxControllerState.leader_token == claim.token,
                )
                .values(
                    last_error_code=str(code or "runtime_unavailable")[:64],
                    last_error_summary=str(
                        summary or "Sandbox Lease 对账失败"
                    )[:255],
                    leader_owner="",
                    leader_token="",
                    leader_expires_at=None,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if int(changed.rowcount or 0) == 1:
                db.commit()
            else:
                db.rollback()
        finally:
            db.close()

    def run_once(self) -> bool:
        if not self._run_lock.acquire(blocking=False):
            return False
        backend: SandboxBackend | None = None
        claim: ReconcileClaim | None = None
        try:
            claim = self._claim()
            if claim is None:
                return False
            backend = self.backend_factory()
            controller_data = _data(backend.controller_state())
            request_id = f"sbxreconcile_{secrets.token_hex(12)}"
            sandboxd_reconcile_data = _data(
                backend.reconcile_leases(request_id=request_id)
            )
            lease_data = _data(backend.list_leases())
            process_data = _data(backend.list_processes())
            self._settle(
                claim,
                controller_data=controller_data,
                sandboxd_reconcile_data=sandboxd_reconcile_data,
                lease_data=lease_data,
                process_data=process_data,
            )
            # 用量投影同步走同一拉取通道；失败只记日志，不阻断 Lease 收敛。
            try:
                usage_data = _data(backend.workspace_usage())
            except SandboxServiceError:
                logger.warning("Workspace 用量事实拉取失败", exc_info=True)
            else:
                self._project_workspace_usage(usage_data)
            return True
        except SandboxServiceError as exc:
            if claim is not None:
                self._record_failure(
                    claim,
                    code=exc.code.value,
                    summary=exc.summary,
                )
            return False
        except Exception:
            if claim is not None:
                self._record_failure(
                    claim,
                    code="runtime_unavailable",
                    summary="Sandbox Lease 对账发生内部错误",
                )
            logger.error("Sandbox Lease 对账异常", exc_info=True)
            return False
        finally:
            if backend is not None:
                close = getattr(backend, "close", None)
                if callable(close):
                    close()
            self._run_lock.release()

    def start(self) -> "SandboxLeaseReconciler":
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="sandbox-lease-reconciler",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, float(timeout)))
        self._thread = None

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self.interval_seconds)


__all__ = ["ReconcileClaim", "SandboxLeaseReconciler"]
