"""统一渠道会话绑定、权威 Run 状态和远程控制服务。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.db.models import AgentRun
from core.db.models.gateway_control import (
    GatewayControlEventRow,
    GatewayRunBindingRow,
    GatewaySessionBindingRow,
)
from core.durable_tasks import (
    RunTaskConflict,
    SqlAlchemyRunTaskService,
)
from core.gateway_control.contracts import (
    GatewayControlAccessDenied,
    GatewayControlConflict,
    GatewayControlIntegrityError,
    GatewayControlNotFound,
    GatewayControlPrincipal,
    GatewayPendingKind,
    GatewayRunAdmission,
    normalize_transport,
    required_text,
)
from core.run_ledger import load_authoritative_run_view
from core.run_ledger.contracts import RunLedgerIntegrityError
from core.run_ledger.persistence import SqlAlchemyRunEventLedger


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _document_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _utc_naive(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        return current
    return current.astimezone(timezone.utc).replace(tzinfo=None)


def build_gateway_session_binding_id(
    transport: object,
    chat_stream_id: object,
) -> str:
    """由渠道类型与 canonical chat stream 生成稳定绑定 ID。"""

    normalized_transport = normalize_transport(transport)
    normalized_stream = required_text(
        chat_stream_id,
        "chat_stream_id",
        max_chars=640,
    )
    return _document_sha256({
        "schema_version": 1,
        "transport": normalized_transport,
        "chat_stream_id": normalized_stream,
    })


def gateway_run_admission_from_metadata(
    *,
    metadata: Mapping[str, Any],
    runtime_session_id: str,
) -> GatewayRunAdmission | None:
    """只接受类型化消息 Adapter 写入的完整受信身份投影。"""

    if not isinstance(metadata, Mapping):
        return None
    admission = metadata.get("_gateway_run_admission")
    if admission is None:
        return None
    if not isinstance(admission, GatewayRunAdmission):
        raise GatewayControlIntegrityError(
            "Gateway Run admission 不是受信合同"
        )
    if admission.runtime_session_id != str(runtime_session_id or "").strip():
        raise GatewayControlIntegrityError(
            "Gateway Run admission 与 Runtime session 不一致"
        )
    return admission


def admit_gateway_run(
    db: Session,
    *,
    run_id: str,
    admission: GatewayRunAdmission,
    admitted_at: datetime,
) -> GatewaySessionBindingRow:
    """随 Run 接纳原子写入会话投影和不可变 Run 绑定。"""

    if not isinstance(db, Session):
        raise TypeError("db 必须是 SQLAlchemy Session")
    if not isinstance(admission, GatewayRunAdmission):
        raise TypeError("admission 必须是 GatewayRunAdmission")
    normalized_run_id = required_text(run_id, "run_id", max_chars=160)
    owner = admission.principal
    immutable = (
        admission.transport,
        owner.platform,
        owner.owner_type.value,
        owner.owner_id,
        admission.chat_type,
        admission.chat_stream_id,
        admission.runtime_session_id,
    )
    row = db.get(GatewaySessionBindingRow, admission.binding_id)
    now = _utc_naive(admitted_at)
    if row is None:
        row = GatewaySessionBindingRow(
            binding_id=admission.binding_id,
            transport=admission.transport,
            owner_platform=owner.platform,
            owner_type=owner.owner_type.value,
            owner_id=owner.owner_id,
            actor_id=admission.actor_id,
            chat_type=admission.chat_type,
            chat_stream_id=admission.chat_stream_id,
            runtime_session_id=admission.runtime_session_id,
            current_run_id=normalized_run_id,
            generation=1,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()
    else:
        existing = (
            str(row.transport),
            str(row.owner_platform),
            str(row.owner_type),
            str(row.owner_id),
            str(row.chat_type),
            str(row.chat_stream_id),
            str(row.runtime_session_id),
        )
        if existing != immutable:
            raise GatewayControlIntegrityError(
                "Gateway binding_id 已绑定其他会话或 owner"
            )
        if str(row.current_run_id) != normalized_run_id:
            row.current_run_id = normalized_run_id
            row.actor_id = admission.actor_id
            next_generation = int(row.generation) + 1
            effective_generation = int(
                row.preferred_model_effective_generation or 0
            )
            if (
                str(row.preferred_model_profile_id or "")
                and effective_generation > 0
                and next_generation >= effective_generation
            ):
                row.active_model_profile_id = str(
                    row.preferred_model_profile_id
                )
                row.preferred_model_profile_id = ""
                row.preferred_model_effective_generation = 0
            row.generation = next_generation
            row.updated_at = now
            db.flush()

    existing_run = db.get(GatewayRunBindingRow, normalized_run_id)
    if existing_run is not None:
        exact = (
            str(existing_run.binding_id) == admission.binding_id
            and str(existing_run.owner_platform) == owner.platform
            and str(existing_run.owner_type) == owner.owner_type.value
            and str(existing_run.owner_id) == owner.owner_id
            and str(existing_run.runtime_session_id)
            == admission.runtime_session_id
        )
        if not exact:
            raise GatewayControlIntegrityError(
                "Run 已绑定其他 Gateway 会话或 owner"
            )
        return row
    db.add(GatewayRunBindingRow(
        run_id=normalized_run_id,
        binding_id=admission.binding_id,
        transport=admission.transport,
        owner_platform=owner.platform,
        owner_type=owner.owner_type.value,
        owner_id=owner.owner_id,
        actor_id=admission.actor_id,
        chat_type=admission.chat_type,
        chat_stream_id=admission.chat_stream_id,
        runtime_session_id=admission.runtime_session_id,
        admitted_at=now,
    ))
    db.flush()
    return row


class SqlAlchemyGatewayControlService:
    """以 Gateway Run binding、Ledger 与 Durable Task 为唯一控制依据。"""

    def __init__(self, db: Session) -> None:
        if not isinstance(db, Session):
            raise TypeError("db 必须是 SQLAlchemy Session")
        self._db = db

    @staticmethod
    def _owner_tuple(row: GatewayRunBindingRow) -> tuple[str, str, str]:
        return (
            str(row.owner_platform),
            str(row.owner_type),
            str(row.owner_id),
        )

    @staticmethod
    def _principal_tuple(
        principal: GatewayControlPrincipal,
    ) -> tuple[str, str, str]:
        owner = principal.principal
        return owner.platform, owner.owner_type.value, owner.owner_id

    def _run_binding(
        self,
        run_id: str,
        principal: GatewayControlPrincipal,
    ) -> GatewayRunBindingRow:
        if not isinstance(principal, GatewayControlPrincipal):
            raise TypeError("principal 必须是 GatewayControlPrincipal")
        row = self._db.get(
            GatewayRunBindingRow,
            required_text(run_id, "run_id", max_chars=160),
        )
        if row is None:
            raise GatewayControlNotFound("Run 没有可控制的 Gateway 绑定")
        if (
            not principal.is_admin
            and self._owner_tuple(row) != self._principal_tuple(principal)
        ):
            raise GatewayControlAccessDenied("当前主体无权控制该 Run")
        if (
            not principal.is_admin
            and principal.transport
            and str(row.transport) != principal.transport
        ):
            raise GatewayControlAccessDenied("当前渠道无权控制该 Run")
        if (
            not principal.is_admin
            and principal.runtime_session_id
            and str(row.runtime_session_id) != principal.runtime_session_id
        ):
            raise GatewayControlAccessDenied("当前会话无权控制该 Run")
        return row

    def _view(self, run_id: str):
        try:
            view = load_authoritative_run_view(
                SqlAlchemyRunEventLedger(self._db),
                run_id,
            )
        except RunLedgerIntegrityError as exc:
            raise GatewayControlIntegrityError(
                "Run Ledger 无法生成权威状态"
            ) from exc
        if view is None:
            raise GatewayControlIntegrityError("Run Ledger 不存在")
        return view

    def status(
        self,
        run_id: str,
        principal: GatewayControlPrincipal,
    ) -> dict[str, object]:
        run_binding = self._run_binding(run_id, principal)
        binding = self._db.get(
            GatewaySessionBindingRow,
            str(run_binding.binding_id),
        )
        if binding is None or self._owner_tuple(run_binding) != (
            str(binding.owner_platform),
            str(binding.owner_type),
            str(binding.owner_id),
        ):
            raise GatewayControlIntegrityError("Gateway 会话投影不完整")
        view = self._view(str(run_binding.run_id))
        task = SqlAlchemyRunTaskService(self._db).get(str(run_binding.run_id))
        legacy = self._db.get(AgentRun, str(run_binding.run_id))
        status = str(view.projection.status)
        pending = GatewayPendingKind.NONE
        if status == "waiting_approval":
            pending = GatewayPendingKind.APPROVAL
        elif status == "waiting_input":
            pending = GatewayPendingKind.QUESTION
        return {
            "run_id": str(run_binding.run_id),
            "binding": {
                "binding_id": str(binding.binding_id),
                "transport": str(binding.transport),
                "platform": str(binding.owner_platform),
                "chat_type": str(binding.chat_type),
                "chat_stream_id": str(binding.chat_stream_id),
                "generation": int(binding.generation),
            },
            "status": status,
            "terminal": bool(view.projection.terminal),
            "pending": pending.value,
            "pending_approval": pending is GatewayPendingKind.APPROVAL,
            "pending_question": pending is GatewayPendingKind.QUESTION,
            "stop": {
                "supported": not view.projection.terminal,
                "requested": bool(
                    task is not None and task.cancel_requested_at is not None
                ),
            },
            "resume": {
                "supported": bool(
                    view.projection.terminal
                    or pending is not GatewayPendingKind.NONE
                ),
                "mode": "channel_continuation",
                "latest_checkpoint_id": (
                    view.projection.latest_checkpoint_id
                ),
                "checkpoint_resumable": bool(
                    view.projection.latest_checkpoint_resumable
                ),
            },
            "model": {
                "current": str(legacy.model if legacy is not None else ""),
                "active_profile_id": str(
                    binding.active_model_profile_id or ""
                ),
                "preferred_profile_id": str(
                    binding.preferred_model_profile_id or ""
                ),
                "effective_from": "next_run",
                "effective_from_generation": int(
                    binding.preferred_model_effective_generation or 0
                ),
            },
            "high_water_sequence": int(view.head.last_sequence),
        }

    @staticmethod
    def _request_identity(
        *,
        request_id: str,
        action: str,
        run_id: str,
        principal: GatewayControlPrincipal,
        payload: Mapping[str, object],
    ) -> tuple[str, str]:
        normalized_request = required_text(
            request_id,
            "request_id",
            max_chars=160,
        )
        request_sha256 = _document_sha256({
            "schema_version": 1,
            "request_id": normalized_request,
            "actor": principal.actor_id,
            "actor_principal": principal.principal.canonical_id,
            "admin": principal.is_admin,
        })
        fingerprint = _document_sha256({
            "schema_version": 1,
            "request_id_sha256": request_sha256,
            "action": action,
            "run_id": run_id,
            "actor": principal.actor_id,
            "actor_principal": principal.principal.canonical_id,
            "admin": principal.is_admin,
            "payload": dict(payload),
        })
        return request_sha256, fingerprint

    def _replay(
        self,
        request_sha256: str,
        fingerprint: str,
    ) -> dict[str, object] | None:
        row = (
            self._db.query(GatewayControlEventRow)
            .filter(
                GatewayControlEventRow.request_id_sha256
                == request_sha256
            )
            .one_or_none()
        )
        if row is None:
            return None
        if str(row.request_fingerprint_sha256) != fingerprint:
            raise GatewayControlConflict(
                "request_id 已绑定不同的远程控制请求"
            )
        try:
            result = json.loads(str(row.result_json or "{}"))
        except json.JSONDecodeError as exc:
            raise GatewayControlIntegrityError(
                "Gateway 控制审计结果损坏"
            ) from exc
        if not isinstance(result, dict):
            raise GatewayControlIntegrityError(
                "Gateway 控制审计结果不是对象"
            )
        result["idempotent_replay"] = True
        return result

    def _record(
        self,
        *,
        request_sha256: str,
        fingerprint: str,
        run_binding: GatewayRunBindingRow,
        action: str,
        principal: GatewayControlPrincipal,
        outcome: str,
        result: Mapping[str, object],
    ) -> dict[str, object]:
        payload = dict(result)
        payload["idempotent_replay"] = False
        self._db.add(GatewayControlEventRow(
            event_id=f"gateway-control:{request_sha256}",
            request_id_sha256=request_sha256,
            request_fingerprint_sha256=fingerprint,
            binding_id=str(run_binding.binding_id),
            run_id=str(run_binding.run_id),
            action=action,
            actor_platform=principal.principal.platform,
            actor_type=(
                "admin"
                if principal.is_admin
                else principal.principal.owner_type.value
            ),
            actor_id=principal.actor_id,
            outcome=outcome,
            result_json=_canonical_json(payload),
            occurred_at=_utc_naive(),
        ))
        try:
            self._db.commit()
        except IntegrityError as exc:
            self._db.rollback()
            replay = self._replay(request_sha256, fingerprint)
            if replay is not None:
                return replay
            raise GatewayControlIntegrityError(
                "Gateway 控制审计写入失败"
            ) from exc
        return payload

    def stop(
        self,
        *,
        run_id: str,
        request_id: str,
        reason_code: str,
        principal: GatewayControlPrincipal,
    ) -> dict[str, object]:
        run_binding = self._run_binding(run_id, principal)
        reason = required_text(
            reason_code,
            "reason_code",
            max_chars=64,
        )
        request_sha256, fingerprint = self._request_identity(
            request_id=request_id,
            action="stop",
            run_id=str(run_binding.run_id),
            principal=principal,
            payload={"reason_code": reason},
        )
        replay = self._replay(request_sha256, fingerprint)
        if replay is not None:
            return replay
        view = self._view(str(run_binding.run_id))
        outcome = "already_terminal" if view.projection.terminal else "accepted"
        if not view.projection.terminal:
            try:
                task = SqlAlchemyRunTaskService(self._db).request_cancel(
                    str(run_binding.run_id),
                    reason=f"gateway_stop:{reason}"[:128],
                )
            except RunTaskConflict as exc:
                raise GatewayControlConflict(str(exc)) from exc
            cancel_requested_at = task.cancel_requested_at
        else:
            cancel_requested_at = None
        return self._record(
            request_sha256=request_sha256,
            fingerprint=fingerprint,
            run_binding=run_binding,
            action="stop",
            principal=principal,
            outcome=outcome,
            result={
                "run_id": str(run_binding.run_id),
                "status": outcome,
                "cancel_requested_at": (
                    cancel_requested_at.isoformat()
                    if cancel_requested_at is not None
                    else None
                ),
            },
        )

    def authorize_resume(
        self,
        *,
        run_id: str,
        request_id: str,
        principal: GatewayControlPrincipal,
    ) -> dict[str, object]:
        run_binding = self._run_binding(run_id, principal)
        request_sha256, fingerprint = self._request_identity(
            request_id=request_id,
            action="resume",
            run_id=str(run_binding.run_id),
            principal=principal,
            payload={},
        )
        replay = self._replay(request_sha256, fingerprint)
        if replay is not None:
            return replay
        view = self._view(str(run_binding.run_id))
        status = str(view.projection.status)
        if not view.projection.terminal and status not in {
            "waiting_approval",
            "waiting_input",
        }:
            raise GatewayControlConflict(
                "只有终态或等待人工交互的 Run 可以从渠道继续"
            )
        binding = self._db.get(
            GatewaySessionBindingRow,
            str(run_binding.binding_id),
        )
        if binding is None:
            raise GatewayControlIntegrityError("Gateway 会话投影不存在")
        return self._record(
            request_sha256=request_sha256,
            fingerprint=fingerprint,
            run_binding=run_binding,
            action="resume",
            principal=principal,
            outcome="accepted",
            result={
                "run_id": str(run_binding.run_id),
                "status": "authorized",
                "resume_mode": "channel_continuation",
                "binding_id": str(binding.binding_id),
                "binding_generation": int(binding.generation),
            },
        )

    def switch_model(
        self,
        *,
        run_id: str,
        request_id: str,
        profile_id: str,
        expected_generation: int,
        available_profile_ids: Sequence[str],
        principal: GatewayControlPrincipal,
    ) -> dict[str, object]:
        run_binding = self._run_binding(run_id, principal)
        profile = required_text(
            profile_id,
            "profile_id",
            max_chars=160,
        )
        if profile not in {
            str(item or "").strip()
            for item in available_profile_ids
            if str(item or "").strip()
        }:
            raise GatewayControlConflict(
                "目标模型 Profile 不是当前 reply Route 的可用候选"
            )
        if type(expected_generation) is not int or expected_generation <= 0:
            raise ValueError("expected_generation 必须是正整数")
        request_sha256, fingerprint = self._request_identity(
            request_id=request_id,
            action="model_switch",
            run_id=str(run_binding.run_id),
            principal=principal,
            payload={
                "profile_id": profile,
                "expected_generation": expected_generation,
            },
        )
        replay = self._replay(request_sha256, fingerprint)
        if replay is not None:
            return replay
        updated = (
            self._db.query(GatewaySessionBindingRow)
            .filter(
                GatewaySessionBindingRow.binding_id
                == str(run_binding.binding_id),
                GatewaySessionBindingRow.generation
                == expected_generation,
            )
            .update(
                {
                    GatewaySessionBindingRow.preferred_model_profile_id: profile,
                    GatewaySessionBindingRow.preferred_model_effective_generation:
                    expected_generation + 2,
                    GatewaySessionBindingRow.generation:
                    expected_generation + 1,
                    GatewaySessionBindingRow.updated_at: _utc_naive(),
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            self._db.rollback()
            replay = self._replay(request_sha256, fingerprint)
            if replay is not None:
                return replay
            raise GatewayControlConflict(
                "Gateway 会话 generation 已变化，请刷新状态后重试"
            )
        return self._record(
            request_sha256=request_sha256,
            fingerprint=fingerprint,
            run_binding=run_binding,
            action="model_switch",
            principal=principal,
            outcome="accepted",
            result={
                "run_id": str(run_binding.run_id),
                "status": "accepted",
                "profile_id": profile,
                "binding_generation": expected_generation + 1,
                "effective_from_generation": expected_generation + 2,
                "effective_from": "next_run",
            },
        )


def active_gateway_model_profile(binding_id: str) -> str:
    """在模型候选冻结前读取已由本轮 Run 接纳激活的 Profile。"""

    normalized = str(binding_id or "").strip().lower()
    if not normalized:
        return ""
    from core import database

    db = database.SessionLocal()
    try:
        row = db.get(GatewaySessionBindingRow, normalized)
        return str(row.active_model_profile_id or "") if row else ""
    finally:
        db.close()


__all__ = [
    "SqlAlchemyGatewayControlService",
    "active_gateway_model_profile",
    "admit_gateway_run",
    "build_gateway_session_binding_id",
    "gateway_run_admission_from_metadata",
]
