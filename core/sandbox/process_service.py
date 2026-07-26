"""Server 侧 Lease 进程编排、对象级授权与 Run 账本收敛。"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from core.database import (
    SANDBOX_LEASE_NONTERMINAL_STATUSES,
    SandboxLease,
    SandboxRun,
)
from core.sandbox.access_contracts import (
    SandboxAccessDecision,
    SandboxCapability,
)
from core.sandbox.access_policy import SandboxAccessPolicy
from core.sandbox.backend import SandboxBackend
from core.sandbox.contracts import (
    SandboxErrorCode,
    SandboxServiceError,
    success_result,
)
from core.sandbox.execution_profiles import (
    ExecutionProfileRegistry,
    load_execution_profile_registry,
)
from core.sandbox.lease_service import SandboxLeaseService
from core.sandbox.repositories import SandboxLeaseRepository
from core.sandbox.run_ledger import SandboxRunLedger
from core.time_utils import db_now_naive
from core.tracing_context import get_tool_trace_context, get_trace_context


_PROCESS_ID_RE = re.compile(r"sbxrun_[A-Za-z0-9_-]{1,56}")
_CONTROLLER_EPOCH_RE = re.compile(r"sbxctl_[0-9a-f]{32}")
_CURSOR_RE = re.compile(r"v1:(0|[1-9][0-9]*):(0|[1-9][0-9]*)")
_TERMINATION_REASON_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_EXECUTION_STATUSES = frozenset({
    "running",
    "completed",
    "failed",
    "cancelled",
})
_PROCESS_STATES = frozenset({"running", "exited", "lost"})
_TERMINAL_RUN_STATUSES = frozenset({
    "completed",
    "failed",
    "cancelled",
})
_CANCEL_REASONS = frozenset({
    "cancelled",
    "admin_terminated",
    "kill_switch",
    "lease_recycled",
    "quota_reconfigured",
})


def _data(response: Mapping[str, Any]) -> dict[str, Any]:
    value = response.get("data")
    if not isinstance(value, Mapping):
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "Sandbox 进程控制面返回了无效响应",
            retryable=True,
            stop=False,
        )
    return dict(value)


def _process_id(value: object) -> str:
    normalized = str(value or "")
    if not _PROCESS_ID_RE.fullmatch(normalized):
        raise SandboxServiceError(
            SandboxErrorCode.AUTHORIZATION_FAILED,
            "Sandbox 进程不可访问",
        )
    return normalized


def _generic_denied() -> SandboxServiceError:
    return SandboxServiceError(
        SandboxErrorCode.AUTHORIZATION_FAILED,
        "Sandbox 进程不可访问",
    )


class SandboxProcessService:
    """模型不可提交身份；所有对象操作都重新核对完整归属链。"""

    def __init__(
        self,
        db: Session,
        backend: SandboxBackend,
        *,
        profile_registry: ExecutionProfileRegistry | None = None,
        run_session_factory: Callable[[], Session] | None = None,
    ) -> None:
        self.db = db
        self.backend = backend
        self.profile_registry = (
            profile_registry or load_execution_profile_registry()
        )
        if run_session_factory is None:
            run_session_factory = sessionmaker(
                bind=db.get_bind(),
                autoflush=False,
                expire_on_commit=False,
            )
        self.run_ledger = SandboxRunLedger(run_session_factory)
        self.lease_service = SandboxLeaseService(
            db,
            backend,
            profile_registry=self.profile_registry,
        )
        self.lease_repository = SandboxLeaseRepository(db)

    @staticmethod
    def _same_access(
        expected: SandboxAccessDecision,
        current: SandboxAccessDecision,
    ) -> bool:
        return (
            current.allowed
            and current.required_capability is SandboxCapability.EXEC
            and current.granted_capability >= SandboxCapability.EXEC
            and current.identity is not None
            and expected.identity is not None
            and current.identity.chat_stream_id
            == expected.identity.chat_stream_id
            and current.grant_id == expected.grant_id
            and current.workspace_id == expected.workspace_id
            and current.execution_profile
            == expected.execution_profile
            and current.grant_version == expected.grant_version
            and current.quota_generation == expected.quota_generation
            and current.grant_version > 0
            and current.quota_generation > 0
        )

    def _fresh_access(
        self,
        access: SandboxAccessDecision,
    ) -> SandboxAccessDecision:
        identity = access.identity
        if (
            not access.allowed
            or identity is None
            or access.granted_capability < SandboxCapability.EXEC
        ):
            raise _generic_denied()
        self.db.expire_all()
        current = SandboxAccessPolicy(self.db).evaluate(
            "sandbox_exec",
            platform=identity.platform,
            chat_type=identity.chat_type,
            session_id=identity.external_session_id,
        )
        if not self._same_access(access, current):
            raise _generic_denied()
        return current

    def _owned_process(
        self,
        access: SandboxAccessDecision,
        process_id: str,
        *,
        allow_terminal: bool,
    ) -> tuple[SandboxAccessDecision, SandboxRun, SandboxLease]:
        process_id = _process_id(process_id)
        current = self._fresh_access(access)
        self.db.expire_all()
        run = self.db.get(SandboxRun, process_id)
        lease = (
            self.db.get(SandboxLease, str(run.lease_id or ""))
            if run is not None and run.lease_id
            else None
        )
        identity = current.identity
        if (
            run is None
            or lease is None
            or identity is None
            or str(run.execution_mode) != "lease"
            or str(run.workspace_id) != current.workspace_id
            or str(run.lease_id) != str(lease.lease_id)
            or str(run.profile_id) != current.execution_profile
            or str(lease.grant_id) != current.grant_id
            or str(lease.chat_stream_id) != identity.chat_stream_id
            or str(lease.workspace_id) != current.workspace_id
            or str(lease.profile_id) != current.execution_profile
            or str(run.image_digest) != str(lease.image_digest)
        ):
            raise _generic_denied()
        is_terminal = str(run.status) in _TERMINAL_RUN_STATUSES
        if is_terminal and not allow_terminal:
            raise _generic_denied()
        if not is_terminal:
            current_lease = self.lease_repository.get_current_by_lease_key(
                str(lease.lease_key)
            )
            if (
                str(lease.status) not in SANDBOX_LEASE_NONTERMINAL_STATUSES
                or current_lease is None
                or str(current_lease.lease_id) != str(lease.lease_id)
                or not _CONTROLLER_EPOCH_RE.fullmatch(
                    str(lease.controller_epoch or "")
                )
            ):
                raise _generic_denied()
        return current, run, lease

    @staticmethod
    def _integer(
        value: object,
        *,
        nullable: bool = False,
    ) -> int | None:
        if value is None and nullable:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("value is not integer")
        result = value
        if result < 0:
            raise ValueError("negative integer")
        return result

    @staticmethod
    def _id_list(value: object) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("not a list")
        normalized = [_process_id(item) for item in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("duplicate process id")
        return normalized

    @staticmethod
    def _active_processes(value: object) -> list[dict[str, str]]:
        if not isinstance(value, list):
            raise ValueError("not a list")
        result: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, Mapping):
                raise ValueError("invalid active process")
            process_id = _process_id(item.get("process_id"))
            state = str(item.get("state") or "")
            if process_id in seen or state != "running":
                raise ValueError("invalid active process")
            seen.add(process_id)
            result.append({
                "process_id": process_id,
                "state": state,
            })
        return sorted(result, key=lambda item: item["process_id"])

    def _normalize_process_data(
        self,
        raw: Mapping[str, Any],
        *,
        run: SandboxRun,
        lease: SandboxLease,
    ) -> dict[str, Any]:
        try:
            data = dict(raw)
            if (
                _process_id(data.get("process_id")) != str(run.run_id)
                or str(data.get("lease_id") or "")
                != str(lease.lease_id)
                or str(data.get("workspace_id") or "")
                != str(lease.workspace_id)
                or str(data.get("profile_id") or "")
                != str(lease.profile_id)
                or str(data.get("controller_epoch") or "")
                != str(lease.controller_epoch)
            ):
                raise ValueError("ownership mismatch")
            execution_status = str(
                data.get("execution_status") or ""
            )
            process_state = str(data.get("process_state") or "")
            if (
                execution_status not in _EXECUTION_STATUSES
                or process_state not in _PROCESS_STATES
            ):
                raise ValueError("invalid status")
            exit_code = self._integer(
                data.get("exit_code"),
                nullable=True,
            )
            reason = str(data.get("termination_reason") or "")
            cursor = str(data.get("next_cursor") or "")
            if not _CURSOR_RE.fullmatch(cursor) or len(cursor) > 96:
                raise ValueError("invalid cursor")
            lease_recycled = data.get("lease_recycled")
            stdout_truncated = data.get("stdout_truncated")
            stderr_truncated = data.get("stderr_truncated")
            if (
                not isinstance(lease_recycled, bool)
                or not isinstance(stdout_truncated, bool)
                or not isinstance(stderr_truncated, bool)
            ):
                raise ValueError("invalid recycled flag")
            affected = self._id_list(
                data.get("affected_process_ids")
            )
            active = self._active_processes(
                data.get("active_processes")
            )
            active_ids = {item["process_id"] for item in active}
            if execution_status == "running":
                if (
                    process_state != "running"
                    or exit_code is not None
                    or reason
                    or lease_recycled
                    or affected
                    or str(run.run_id) not in active_ids
                ):
                    raise ValueError("invalid running state")
            else:
                if (
                    not _TERMINATION_REASON_RE.fullmatch(reason)
                    or str(run.run_id) in active_ids
                ):
                    raise ValueError("invalid terminal state")
                if lease_recycled:
                    if (
                        process_state != "lost"
                        or exit_code is not None
                        or str(run.run_id) not in affected
                    ):
                        raise ValueError("invalid recycled terminal state")
                elif (
                    process_state != "exited"
                    or affected != [str(run.run_id)]
                    or exit_code is None
                    or (
                        execution_status == "completed"
                        and (exit_code != 0 or reason != "completed")
                    )
                    or (
                        execution_status == "failed"
                        and (exit_code == 0 or reason != "nonzero_exit")
                    )
                    or execution_status == "cancelled"
                ):
                    raise ValueError("invalid local terminal state")
            if lease_recycled and (
                str(data.get("termination_scope") or "") != "lease"
                or data.get("workspace_preserved") is not True
                or data.get("runtime_preserved") is not True
            ):
                raise ValueError("invalid lease recycle state")
            normalized: dict[str, Any] = {
                "process_id": str(run.run_id),
                "lease_id": str(lease.lease_id),
                "workspace_id": str(lease.workspace_id),
                "profile_id": str(lease.profile_id),
                "controller_epoch": str(lease.controller_epoch),
                "execution_status": execution_status,
                "process_state": process_state,
                "exit_code": exit_code,
                "termination_reason": reason,
                "next_cursor": cursor,
                "stdout_bytes": self._integer(
                    data.get("stdout_bytes")
                ),
                "stderr_bytes": self._integer(
                    data.get("stderr_bytes")
                ),
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
                "cpu_time_ms": self._integer(
                    data.get("cpu_time_ms")
                ),
                "peak_memory_bytes": self._integer(
                    data.get("peak_memory_bytes")
                ),
                "lease_recycled": lease_recycled,
                "affected_process_ids": affected,
                "active_processes": active,
            }
            for key in (
                "stdout",
                "stderr",
                "stdout_delta",
                "stderr_delta",
            ):
                if key in data:
                    if not isinstance(data[key], str):
                        raise ValueError("invalid output")
                    normalized[key] = data[key]
            if lease_recycled:
                normalized.update({
                    "termination_scope": "lease",
                    "workspace_preserved": True,
                    "runtime_preserved": True,
                })
            return normalized
        except (SandboxServiceError, TypeError, ValueError) as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 进程控制面返回了无效响应",
                retryable=True,
                stop=False,
            ) from exc

    @staticmethod
    def _run_status(data: Mapping[str, Any]) -> str:
        execution_status = str(data.get("execution_status") or "")
        if execution_status == "completed":
            return "completed"
        if execution_status == "cancelled":
            return "cancelled"
        return "failed"

    def _persist_response(
        self,
        *,
        run: SandboxRun,
        lease: SandboxLease,
        data: dict[str, Any],
    ) -> None:
        if data["lease_recycled"]:
            self.run_ledger.settle_lease(
                str(lease.lease_id),
                termination_reason=str(
                    data["termination_reason"] or "lease_recycled"
                ),
                status=self._run_status(data),
                affected_process_ids=set(
                    data["affected_process_ids"]
                ),
                target_process_id=str(run.run_id),
                target_data=data,
            )
            self.db.expire_all()
            return
        if data["execution_status"] == "running":
            self.run_ledger.mark_observed(
                str(run.run_id),
                process_state="running",
                data=data,
            )
        else:
            self.run_ledger.mark_terminal(
                str(run.run_id),
                status=self._run_status(data),
                termination_reason=str(data["termination_reason"]),
                data=data,
            )
        self.db.expire_all()
        current_lease = self.db.get(SandboxLease, str(lease.lease_id))
        if (
            current_lease is not None
            and str(current_lease.status)
            in SANDBOX_LEASE_NONTERMINAL_STATUSES
        ):
            current_lease.status = (
                "active"
                if data["active_processes"]
                else "idle"
            )
            current_lease.last_active_at = db_now_naive()
            current_lease.reconciled_at = db_now_naive()
            self.db.commit()

    def start(
        self,
        access: SandboxAccessDecision,
        args: Mapping[str, Any],
        *,
        authorized_assets: Sequence[Mapping[str, str]] = (),
    ) -> dict[str, Any]:
        current = self._fresh_access(access)
        descriptor = self.profile_registry.descriptor(
            current.execution_profile
        )
        if descriptor.execution_mode != "lease":
            raise _generic_denied()
        lease = self.lease_service.ensure_for_access(
            current,
            authorized_assets=authorized_assets,
        )
        current = self._fresh_access(current)
        if (
            str(lease.grant_id) != current.grant_id
            or str(lease.workspace_id) != current.workspace_id
            or str(lease.profile_id) != current.execution_profile
        ):
            raise _generic_denied()

        process_id = f"sbxrun_{secrets.token_hex(16)}"
        trace_id, agent_run_id = get_trace_context()
        tool_call_id = get_tool_trace_context()
        row = SandboxRun(
            run_id=process_id,
            request_id=process_id,
            workspace_id=str(lease.workspace_id),
            lease_id=str(lease.lease_id),
            profile_id=str(lease.profile_id),
            execution_mode="lease",
            process_state="starting",
            trace_id=str(trace_id or "")[:64],
            agent_run_id=str(agent_run_id or "")[:64],
            tool_call_id=str(tool_call_id or "")[:64],
            image_digest=str(lease.image_digest),
            status="pending",
        )
        self.db.add(row)
        self.db.commit()
        self.run_ledger.mark_running(
            process_id,
            process_state="starting",
        )
        payload: dict[str, Any] = {
            "request_id": process_id,
            "command": str(args.get("command") or ""),
            "cwd": str(args.get("cwd") or ""),
            "yield_time_ms": int(
                args.get("yield_time_ms")
                if args.get("yield_time_ms") is not None
                else 10_000
            ),
        }
        if args.get("timeout_seconds") is not None:
            payload["timeout_seconds"] = int(args["timeout_seconds"])
        try:
            response = self.backend.start_process(
                str(lease.lease_id),
                payload,
            )
            data = self._normalize_process_data(
                _data(response),
                run=row,
                lease=lease,
            )
            self._persist_response(run=row, lease=lease, data=data)
            return success_result(
                "Sandbox Lease 进程已启动",
                data=data,
            )
        except SandboxServiceError as exc:
            if exc.code in {
                SandboxErrorCode.AUTHORIZATION_FAILED,
                SandboxErrorCode.SANDBOX_BUSY,
            }:
                self.run_ledger.mark_failed(
                    process_id,
                    termination_reason=exc.code.value,
                )
            raise
        except Exception as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 进程启动状态暂时无法确认",
                retryable=True,
                stop=False,
            ) from exc

    def poll(
        self,
        access: SandboxAccessDecision,
        process_id: str,
        *,
        cursor: str = "",
    ) -> dict[str, Any]:
        _current, run, lease = self._owned_process(
            access,
            process_id,
            allow_terminal=False,
        )
        response = self.backend.get_process(
            str(run.run_id),
            cursor=str(cursor or ""),
        )
        data = self._normalize_process_data(
            _data(response),
            run=run,
            lease=lease,
        )
        self._persist_response(run=run, lease=lease, data=data)
        return success_result(
            "Sandbox 进程状态读取完成",
            data=data,
        )

    def write_stdin(
        self,
        access: SandboxAccessDecision,
        process_id: str,
        *,
        chars: str,
    ) -> dict[str, Any]:
        _current, run, lease = self._owned_process(
            access,
            process_id,
            allow_terminal=False,
        )
        descriptor = self.profile_registry.descriptor(
            str(lease.profile_id)
        )
        if not descriptor.allow_stdin:
            raise _generic_denied()
        request_id = f"sbxstdin_{secrets.token_hex(16)}"
        response = self.backend.write_process_stdin(
            str(run.run_id),
            {
                "request_id": request_id,
                "chars": str(chars),
            },
        )
        data = _data(response)
        try:
            if (
                _process_id(data.get("process_id")) != str(run.run_id)
                or str(data.get("lease_id") or "")
                != str(lease.lease_id)
                or str(data.get("workspace_id") or "")
                != str(lease.workspace_id)
                or str(data.get("profile_id") or "")
                != str(lease.profile_id)
                or str(data.get("controller_epoch") or "")
                != str(lease.controller_epoch)
                or str(data.get("execution_status") or "") != "running"
            ):
                raise ValueError("stdin ownership mismatch")
            written = self._integer(data.get("written_bytes"))
            active = self._active_processes(
                data.get("active_processes")
            )
            if str(run.run_id) not in {
                item["process_id"] for item in active
            }:
                raise ValueError("stdin target is not active")
        except (SandboxServiceError, TypeError, ValueError) as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox stdin 控制面返回了无效响应",
                retryable=True,
                stop=False,
            ) from exc
        self.run_ledger.mark_observed(str(run.run_id))
        return success_result(
            "Sandbox stdin 已写入",
            data={
                "process_id": str(run.run_id),
                "written_bytes": written,
                "execution_status": "running",
                "active_processes": active,
            },
        )

    def terminate(
        self,
        access: SandboxAccessDecision,
        process_id: str,
    ) -> dict[str, Any]:
        _current, run, lease = self._owned_process(
            access,
            process_id,
            allow_terminal=True,
        )
        if str(run.status) in _TERMINAL_RUN_STATUSES:
            return success_result(
                "Sandbox 进程已处于终态",
                data={
                    "process_id": str(run.run_id),
                    "lease_id": str(lease.lease_id),
                    "execution_status": str(run.status),
                    "process_state": str(run.process_state),
                    "termination_reason": str(
                        run.termination_reason or "lease_recycled"
                    ),
                    "termination_scope": "lease",
                    "lease_recycled": (
                        str(lease.status)
                        not in SANDBOX_LEASE_NONTERMINAL_STATUSES
                    ),
                    "affected_process_ids": [str(run.run_id)],
                    "active_processes": [],
                },
            )
        response = self.backend.terminate_process(
            str(run.run_id),
            request_id=f"sbxterm_{secrets.token_hex(16)}",
        )
        data = self._normalize_process_data(
            _data(response),
            run=run,
            lease=lease,
        )
        # 进程可能在终止请求到达前已自然退出/超时/被回收；sandboxd 此时返回
        # 既有终态事实。幂等落账该事实即可，只有仍在 running 才是终止失败。
        # 「cancelled 必须伴随整 Lease 回收」由 _normalize_process_data 强制。
        if data["execution_status"] == "running":
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 终止响应未确认进程终态",
                retryable=True,
                stop=False,
            )
        self._persist_response(run=run, lease=lease, data=data)
        summary = (
            "Sandbox Lease 已按进程终止请求回收"
            if data["lease_recycled"]
            else "Sandbox 进程已处于终态，无需回收 Lease"
        )
        return success_result(summary, data=data)


__all__ = ["SandboxProcessService"]
