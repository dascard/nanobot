"""持久 session grant、撤销与生产工具 Permission 组合。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable

from sqlalchemy.orm import Session

from core.agent_runtime.contracts import (
    RequestRuntimeContext,
    RuntimeAttribute,
    RuntimeRunIdentity,
)
from core.agent_runtime.errors import AgentRuntimePermissionError
from core.agent_runtime.governance_contracts import RuntimeAccessKind
from core.agent_runtime.service_ports import (
    PermissionPort,
    RuntimePermissionDecision,
    RuntimePermissionOutcome,
    RuntimePermissionRequest,
    RuntimePermissionRisk,
)
from core.db.models.permission import PermissionSessionGrantRow
from core.run_ledger.adapters import (
    permission_decision_event,
    permission_grant_issued_event,
    permission_grant_revoked_event,
)
from core.run_ledger.contracts import RunLedgerAuthorityError
from core.run_ledger.persistence import SqlAlchemyRunEventLedger
from core.time_utils import db_naive_to_utc, to_db_naive


def _required(value: object, name: str, *, max_length: int = 160) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} 不能为空")
    if len(normalized) > max_length:
        raise ValueError(f"{name} 不能超过 {max_length} 字符")
    return normalized


def _resource_sha256(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _binding_key(request: RuntimePermissionRequest) -> str:
    payload = {
        "owner": request.identity.owner.canonical_id,
        "session_id": request.session_id,
        "action": request.action,
        "resource_sha256": _resource_sha256(request.resource),
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _bounded_id(prefix: str, *parts: object) -> str:
    raw = ":".join(str(part or "").strip() for part in parts)
    candidate = f"{prefix}:{raw}"
    if len(candidate) <= 160:
        return candidate
    return f"{prefix}:sha256:{hashlib.sha256(raw.encode()).hexdigest()}"


@dataclass(frozen=True, slots=True)
class RuntimePermissionRevocationRequest:
    revocation_id: str
    grant_id: str
    identity: RuntimeRunIdentity
    session_id: str
    revoked_by: str
    reason: str
    revoked_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "revocation_id",
            _required(self.revocation_id, "revocation_id"),
        )
        object.__setattr__(self, "grant_id", _required(self.grant_id, "grant_id"))
        if not isinstance(self.identity, RuntimeRunIdentity):
            raise ValueError("revocation.identity 无效")
        object.__setattr__(
            self,
            "session_id",
            _required(self.session_id, "session_id"),
        )
        object.__setattr__(
            self,
            "revoked_by",
            _required(self.revoked_by, "revoked_by"),
        )
        object.__setattr__(self, "reason", _required(self.reason, "reason"))
        if self.revoked_at.tzinfo is None or self.revoked_at.utcoffset() is None:
            raise ValueError("revoked_at 必须包含时区")


@dataclass(frozen=True, slots=True)
class RuntimePermissionRevocation:
    revocation_id: str
    grant_id: str
    identity: RuntimeRunIdentity
    session_id: str
    revoked_by: str
    reason: str
    revoked_at: datetime


class ToolPermissionPolicyPort:
    """ToolPlan/访问范围先收窄；只有已证明 Sandbox grant 的高风险工具直通。"""

    async def evaluate(
        self,
        request: RuntimePermissionRequest,
    ) -> RuntimePermissionDecision:
        if not isinstance(request, RuntimePermissionRequest):
            raise TypeError("request 必须是 RuntimePermissionRequest")
        attributes = {item.key: item.value for item in request.attributes}
        authorization = str(attributes.get("authorization") or "")
        if request.risk in {
            RuntimePermissionRisk.LOW,
            RuntimePermissionRisk.MEDIUM,
        }:
            outcome = RuntimePermissionOutcome.ALLOW
            reason = "runtime_access_scope"
        elif (
            request.risk is RuntimePermissionRisk.HIGH
            and authorization == "sandbox_session_grant"
        ):
            outcome = RuntimePermissionOutcome.ALLOW
            reason = "sandbox_session_grant"
        else:
            outcome = RuntimePermissionOutcome.ASK
            reason = "interactive_approval_required"
        return RuntimePermissionDecision(
            decision_id=_bounded_id("permission-policy", request.request_id),
            request_id=request.request_id,
            outcome=outcome,
            reason=reason,
            decided_at=datetime.now(timezone.utc),
        )


class SqlAlchemySessionPermissionPort:
    """先复用精确 session grant，再原子保存决定、grant 与 Ledger 事实。"""

    def __init__(
        self,
        delegate: PermissionPort,
        session_factory: Callable[[], Session],
    ) -> None:
        if not isinstance(delegate, PermissionPort):
            raise TypeError("delegate 必须实现 PermissionPort")
        if not callable(session_factory):
            raise TypeError("session_factory 必须可调用")
        self._delegate = delegate
        self._session_factory = session_factory
        self._requests: dict[str, RuntimePermissionRequest] = {}
        self._decisions: dict[str, RuntimePermissionDecision] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _recorded_decision(
        db: Session,
        request: RuntimePermissionRequest,
    ) -> RuntimePermissionDecision | None:
        records = SqlAlchemyRunEventLedger(db).read(request.identity.run_id)
        for record in reversed(records):
            if record.event_type != "permission.decided":
                continue
            payload = record.payload
            if payload.get("permission_request_id") != request.request_id:
                continue
            exact = (
                payload.get("action") == request.action
                and payload.get("risk") == request.risk.value
                and payload.get("resource_sha256")
                == _resource_sha256(request.resource)
                and record.event.correlation.session_id == request.session_id
                and record.event.identity.owner_platform
                == request.identity.owner.platform
                and record.event.identity.owner_type
                == request.identity.owner.owner_type.value
                and record.event.identity.owner_id
                == request.identity.owner.owner_id
            )
            if not exact:
                raise ValueError(
                    "permission request_id 已绑定不同的持久化请求："
                    f"{request.request_id}"
                )
            expires_raw = str(payload.get("grant_expires_at") or "")
            return RuntimePermissionDecision(
                decision_id=str(payload.get("decision_id") or record.event_id),
                request_id=request.request_id,
                outcome=RuntimePermissionOutcome(str(payload.get("outcome") or "")),
                reason=(
                    "ledger_replay:"
                    + str(payload.get("reason_type") or "recorded_decision")
                ),
                decided_at=record.event.occurred_at,
                grant_id=str(payload.get("grant_id") or ""),
                grant_expires_at=(
                    datetime.fromisoformat(expires_raw) if expires_raw else None
                ),
            )
        return None

    @staticmethod
    def _active_grant(
        db: Session,
        request: RuntimePermissionRequest,
        now: datetime,
    ) -> PermissionSessionGrantRow | None:
        if not request.session_id:
            return None
        binding_key = _binding_key(request)
        row = (
            db.query(PermissionSessionGrantRow)
            .filter(
                PermissionSessionGrantRow.active_binding_key == binding_key,
                PermissionSessionGrantRow.revoked_at.is_(None),
            )
            .one_or_none()
        )
        if row is None:
            return None
        if row.expires_at <= to_db_naive(now):
            row.active_binding_key = None
            db.flush()
            return None
        return row

    @staticmethod
    def _grant_decision(
        request: RuntimePermissionRequest,
        row: PermissionSessionGrantRow,
        now: datetime,
    ) -> RuntimePermissionDecision:
        return RuntimePermissionDecision(
            decision_id=_bounded_id(
                "permission-session",
                request.request_id,
                row.grant_id,
            ),
            request_id=request.request_id,
            outcome=RuntimePermissionOutcome.ALLOW,
            reason="active_session_grant",
            decided_at=now,
            grant_id=str(row.grant_id),
            grant_expires_at=db_naive_to_utc(row.expires_at),
        )

    @staticmethod
    def _append_decision(
        db: Session,
        request: RuntimePermissionRequest,
        decision: RuntimePermissionDecision,
        *,
        issued: bool = False,
    ) -> None:
        ledger = SqlAlchemyRunEventLedger(db)
        ledger.append(permission_decision_event(request, decision))
        if issued:
            ledger.append(permission_grant_issued_event(request, decision))

    async def evaluate(
        self,
        request: RuntimePermissionRequest,
    ) -> RuntimePermissionDecision:
        if not isinstance(request, RuntimePermissionRequest):
            raise TypeError("request 必须是 RuntimePermissionRequest")
        async with self._lock:
            cached = self._decisions.get(request.request_id)
            if cached is not None:
                if self._requests[request.request_id] != request:
                    raise ValueError(
                        "permission request_id 已绑定不同请求："
                        f"{request.request_id}"
                    )
                return cached
            now = datetime.now(timezone.utc)
            db = self._session_factory()
            try:
                recorded = self._recorded_decision(db, request)
                if recorded is not None:
                    self._requests[request.request_id] = request
                    self._decisions[request.request_id] = recorded
                    return recorded
                active = self._active_grant(db, request, now)
                if active is not None:
                    decision = self._grant_decision(request, active, now)
                    self._append_decision(db, request, decision)
                    db.commit()
                    self._requests[request.request_id] = request
                    self._decisions[request.request_id] = decision
                    return decision
                db.commit()
            finally:
                db.close()

            decision = await self._delegate.evaluate(request)
            if not isinstance(decision, RuntimePermissionDecision):
                raise TypeError("delegate 返回了无效 Permission 决定")
            if decision.request_id != request.request_id:
                raise ValueError("delegate Permission 决定与请求不匹配")
            db = self._session_factory()
            try:
                issued = decision.outcome is RuntimePermissionOutcome.SESSION_GRANT
                if issued:
                    if not request.session_id:
                        raise ValueError("session grant 请求必须绑定 session_id")
                    existing = self._active_grant(db, request, decision.decided_at)
                    if existing is not None:
                        decision = RuntimePermissionDecision(
                            decision_id=decision.decision_id,
                            request_id=decision.request_id,
                            outcome=RuntimePermissionOutcome.SESSION_GRANT,
                            reason="session_grant_already_active",
                            decided_at=decision.decided_at,
                            grant_id=str(existing.grant_id),
                            grant_expires_at=db_naive_to_utc(existing.expires_at),
                        )
                        issued = False
                    else:
                        db.add(PermissionSessionGrantRow(
                            grant_id=decision.grant_id,
                            active_binding_key=_binding_key(request),
                            owner_platform=request.identity.owner.platform,
                            owner_type=request.identity.owner.owner_type.value,
                            owner_id=request.identity.owner.owner_id,
                            session_id=request.session_id,
                            action=request.action,
                            resource_sha256=_resource_sha256(request.resource),
                            risk=request.risk.value,
                            source_run_id=request.identity.run_id,
                            source_turn_id=request.identity.turn_id,
                            source_request_id=request.request_id,
                            source_decision_id=decision.decision_id,
                            issued_at=to_db_naive(decision.decided_at),
                            expires_at=to_db_naive(decision.grant_expires_at),
                        ))
                        db.flush()
                self._append_decision(db, request, decision, issued=issued)
                db.commit()
                self._requests[request.request_id] = request
                self._decisions[request.request_id] = decision
                return decision
            except RunLedgerAuthorityError:
                db.rollback()
                raise
            except Exception as exc:
                db.rollback()
                raise RunLedgerAuthorityError(
                    "Permission 决定或 session grant 权威提交失败",
                    run_id=request.identity.run_id,
                    event_type="permission.decided",
                ) from exc
            finally:
                db.close()

    async def revoke(
        self,
        request: RuntimePermissionRevocationRequest,
    ) -> RuntimePermissionRevocation:
        if not isinstance(request, RuntimePermissionRevocationRequest):
            raise TypeError("request 必须是 RuntimePermissionRevocationRequest")
        async with self._lock:
            db = self._session_factory()
            try:
                row = db.get(PermissionSessionGrantRow, request.grant_id)
                if (
                    row is None
                    or row.owner_platform != request.identity.owner.platform
                    or row.owner_type != request.identity.owner.owner_type.value
                    or row.owner_id != request.identity.owner.owner_id
                    or row.session_id != request.session_id
                ):
                    raise PermissionError("session grant 不存在或 owner 未授权")
                if row.revoked_at is not None:
                    if row.revocation_id != request.revocation_id:
                        raise ValueError("session grant 已由其他撤销请求处理")
                    revoked_at = db_naive_to_utc(row.revoked_at)
                    assert revoked_at is not None
                    return RuntimePermissionRevocation(
                        request.revocation_id,
                        request.grant_id,
                        request.identity,
                        request.session_id,
                        str(row.revoked_by),
                        str(row.revoke_reason),
                        revoked_at,
                    )
                revocation = RuntimePermissionRevocation(
                    request.revocation_id,
                    request.grant_id,
                    request.identity,
                    request.session_id,
                    request.revoked_by,
                    request.reason,
                    request.revoked_at,
                )
                row.active_binding_key = None
                row.revocation_id = request.revocation_id
                row.revoked_at = to_db_naive(request.revoked_at)
                row.revoked_by = request.revoked_by
                row.revoke_reason = request.reason
                SqlAlchemyRunEventLedger(db).append(
                    permission_grant_revoked_event(revocation)
                )
                db.commit()
                return revocation
            except RunLedgerAuthorityError:
                db.rollback()
                raise
            except (PermissionError, ValueError):
                db.rollback()
                raise
            except Exception as exc:
                db.rollback()
                raise RunLedgerAuthorityError(
                    "Permission session grant 撤销提交失败",
                    run_id=request.identity.run_id,
                    event_type="permission.grant_revoked",
                ) from exc
            finally:
                db.close()


def _tool_risk(tool_name: str) -> RuntimePermissionRisk:
    from core.tool_registration import get_tool_registration

    registration = get_tool_registration(tool_name)
    if registration is None:
        try:
            from core.mcp import get_current_mcp_runtime

            runtime = get_current_mcp_runtime()
            descriptor = (
                runtime.descriptor(tool_name) if runtime is not None else None
            )
        except Exception:
            descriptor = None
        if descriptor is not None and descriptor.read_only:
            return RuntimePermissionRisk.MEDIUM
    raw = (
        registration.descriptor.definition.risk_level
        if registration is not None
        else RuntimePermissionRisk.HIGH.value
    )
    return RuntimePermissionRisk(raw)


async def authorize_tool_execution(
    permission_port: PermissionPort,
    *,
    context: RequestRuntimeContext,
    tool_name: str,
    tool_call_id: str,
) -> RuntimePermissionDecision:
    """按精确访问 grant 构造统一请求；ask/deny 均失败关闭。"""

    if not isinstance(permission_port, PermissionPort):
        raise TypeError("permission_port 必须实现 PermissionPort")
    normalized_tool = _required(tool_name, "tool_name")
    grant = context.governance.access.find(
        RuntimeAccessKind.TOOL,
        f"tool:{normalized_tool}",
        "execute",
    )
    if grant is None:
        grant = context.governance.access.find(
            RuntimeAccessKind.TOOL,
            normalized_tool,
            "execute",
        )
    if grant is None:
        raise AgentRuntimePermissionError(
            f"工具不在 Runtime 访问范围：{normalized_tool}",
            runtime_id="runtime-permission",
        )
    request = RuntimePermissionRequest(
        request_id=_bounded_id(
            "permission-tool",
            context.request_id,
            tool_call_id,
        ),
        identity=context.execution_identity(),
        action="tool.execute",
        resource=f"tool:{normalized_tool}",
        risk=_tool_risk(normalized_tool),
        requested_at=datetime.now(timezone.utc),
        session_id=context.session_id,
        attributes=(
            RuntimeAttribute("authorization", grant.authorization),
            RuntimeAttribute(
                "governance_sha256",
                context.governance.content_sha256,
            ),
        ),
    )
    decision = await permission_port.evaluate(request)
    if decision.outcome in {
        RuntimePermissionOutcome.ALLOW,
        RuntimePermissionOutcome.ALLOW_ONCE,
        RuntimePermissionOutcome.SESSION_GRANT,
    }:
        return decision
    raise AgentRuntimePermissionError(
        (
            f"工具权限等待审批：{normalized_tool}"
            if decision.outcome is RuntimePermissionOutcome.ASK
            else f"工具权限被拒绝：{normalized_tool}"
        ),
        runtime_id="runtime-permission",
    )


def default_session_permission_port() -> SqlAlchemySessionPermissionPort:
    from core import database

    return SqlAlchemySessionPermissionPort(
        ToolPermissionPolicyPort(),
        lambda: database.SessionLocal(),
    )


__all__ = [
    "RuntimePermissionRevocation",
    "RuntimePermissionRevocationRequest",
    "SqlAlchemySessionPermissionPort",
    "ToolPermissionPolicyPort",
    "authorize_tool_execution",
    "default_session_permission_port",
]
