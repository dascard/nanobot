"""入站消息 claim 与传输无关完成结果。"""

from __future__ import annotations

import json
import math
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.database import InboundMessageClaim
from core.message_envelope import sanitize_reply_meta
from core.sqlite_retry import run_sqlite_locked_retry


DEFAULT_LEASE_SECONDS = 900
_MAX_ACQUIRE_STATE_RETRIES = 3
_PLATFORM_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_COMPLETED_OUTCOMES = frozenset({"respond", "no_reply", "silent", "wait", "blocked"})
_COMPLETED_FIELDS = frozenset({
    "schema_version",
    "outcome",
    "reply",
    "reply_meta",
    "reason",
    "source",
    "intent",
    "guardrail_status",
    "unprocessed_logs",
    "group",
})
_GROUP_FIELDS = frozenset({
    "generation",
    "delay_seconds",
    "diagnostics",
    "duplicate_reply",
    "hard_rule",
})
_GROUP_DIAGNOSTIC_FIELDS = frozenset({"timing_action", "agent_result"})
_DUPLICATE_REPLY_FIELDS = frozenset({
    "previous_log_id",
    "similarity",
    "previous_created_at",
})


class ClaimStatus(str, Enum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ClaimDecisionKind(str, Enum):
    BYPASS = "bypass"
    ACQUIRED = "acquired"
    DUPLICATE_INFLIGHT = "duplicate_inflight"
    REPLAY = "replay"


class DirtyClaimSessionError(RuntimeError):
    """Session 已有活动事务，claim service 拒绝接管。"""


class CorruptClaimResponse(ValueError):
    """已完成 claim 的持久化响应不符合已知 schema。"""


class ClaimContentionError(RuntimeError):
    """有界裁决后 claim 状态仍无法稳定读取。"""


class _RetryClaimState(RuntimeError):
    pass


def _copy_json_value(value: Any, *, path: str) -> Any:
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} 不能包含 NaN 或 Infinity")
        return value
    if type(value) is list:
        return [
            _copy_json_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError(f"{path} 的对象键必须是字符串")
            copied[key] = _copy_json_value(item, path=f"{path}.{key}")
        return copied
    raise TypeError(f"{path} 只能包含 JSON 值")


def _require_string(value: Any, *, field_name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name} 必须是字符串")
    return value


def _require_optional_int(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise TypeError(f"{field_name} 必须是整数或 null")
    return value


def _require_optional_number(value: Any, *, field_name: str) -> int | float | None:
    if value is None:
        return None
    if type(value) not in (int, float):
        raise TypeError(f"{field_name} 必须是数字或 null")
    if type(value) is float and not math.isfinite(value):
        raise ValueError(f"{field_name} 不能是 NaN 或 Infinity")
    return value


def _copy_group_diagnostics(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("group.diagnostics 必须是 Mapping")
    copied = _copy_json_value(value, path="group.diagnostics")
    unknown = set(copied) - _GROUP_DIAGNOSTIC_FIELDS
    if unknown:
        raise ValueError(f"group.diagnostics 包含未知字段: {sorted(unknown)!r}")
    for key, item in copied.items():
        _require_string(item, field_name=f"group.diagnostics.{key}")
    return copied


def _copy_duplicate_reply(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("group.duplicate_reply 必须是 Mapping")
    copied = _copy_json_value(value, path="group.duplicate_reply")
    unknown = set(copied) - _DUPLICATE_REPLY_FIELDS
    if unknown:
        raise ValueError(f"group.duplicate_reply 包含未知字段: {sorted(unknown)!r}")
    if "previous_log_id" in copied:
        copied["previous_log_id"] = _require_optional_int(
            copied["previous_log_id"],
            field_name="group.duplicate_reply.previous_log_id",
        )
    if "similarity" in copied:
        copied["similarity"] = _require_optional_number(
            copied["similarity"],
            field_name="group.duplicate_reply.similarity",
        )
    if "previous_created_at" in copied:
        _require_string(
            copied["previous_created_at"],
            field_name="group.duplicate_reply.previous_created_at",
        )
    return copied


@dataclass(frozen=True, slots=True)
class GroupReplayFields:
    generation: int | None = None
    delay_seconds: int | float | None = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    duplicate_reply: Mapping[str, Any] = field(default_factory=dict)
    hard_rule: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "generation",
            _require_optional_int(self.generation, field_name="group.generation"),
        )
        object.__setattr__(
            self,
            "delay_seconds",
            _require_optional_number(self.delay_seconds, field_name="group.delay_seconds"),
        )
        object.__setattr__(
            self,
            "diagnostics",
            _copy_group_diagnostics(self.diagnostics),
        )
        object.__setattr__(
            self,
            "duplicate_reply",
            _copy_duplicate_reply(self.duplicate_reply),
        )
        _require_string(self.hard_rule, field_name="group.hard_rule")


@dataclass(frozen=True, slots=True)
class CompletedInboundResponse:
    outcome: str
    reply: str = ""
    reply_meta: Mapping[str, Any] = field(default_factory=dict)
    reason: str = ""
    source: str = ""
    intent: str = ""
    guardrail_status: str | None = None
    unprocessed_logs: int | None = None
    group: GroupReplayFields | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("schema_version 必须为 1")
        outcome = _require_string(self.outcome, field_name="outcome")
        if outcome not in _COMPLETED_OUTCOMES:
            raise ValueError(f"未知完成结果 outcome: {outcome}")
        for field_name in ("reply", "reason", "source", "intent"):
            _require_string(getattr(self, field_name), field_name=field_name)
        if self.guardrail_status is not None:
            _require_string(self.guardrail_status, field_name="guardrail_status")
        if not isinstance(self.reply_meta, Mapping):
            raise TypeError("reply_meta 必须是 Mapping")
        object.__setattr__(
            self,
            "reply_meta",
            _copy_json_value(sanitize_reply_meta(self.reply_meta), path="reply_meta"),
        )
        object.__setattr__(
            self,
            "unprocessed_logs",
            _require_optional_int(self.unprocessed_logs, field_name="unprocessed_logs"),
        )
        if self.group is not None and type(self.group) is not GroupReplayFields:
            raise TypeError("group 必须是 GroupReplayFields 或 null")


@dataclass(frozen=True, slots=True)
class InboundClaimKey:
    platform: str
    chat_type: str
    session_id: str
    message_id: str


@dataclass(frozen=True, slots=True)
class InboundClaimHandle:
    key: InboundClaimKey
    owner_token: str
    lease_expires_at: datetime
    attempt_count: int


@dataclass(frozen=True, slots=True)
class InboundClaimDecision:
    kind: ClaimDecisionKind
    handle: InboundClaimHandle | None = None
    response: CompletedInboundResponse | None = None


def encode_completed_inbound_response(response: CompletedInboundResponse) -> str:
    if type(response) is not CompletedInboundResponse:
        raise TypeError("claim complete 只接受 CompletedInboundResponse")
    group = None
    if response.group is not None:
        group = {
            "generation": response.group.generation,
            "delay_seconds": response.group.delay_seconds,
            "diagnostics": _copy_group_diagnostics(response.group.diagnostics),
            "duplicate_reply": _copy_duplicate_reply(response.group.duplicate_reply),
            "hard_rule": response.group.hard_rule,
        }
    payload = {
        "schema_version": response.schema_version,
        "outcome": response.outcome,
        "reply": response.reply,
        "reply_meta": _copy_json_value(
            sanitize_reply_meta(response.reply_meta),
            path="reply_meta",
        ),
        "reason": response.reason,
        "source": response.source,
        "intent": response.intent,
        "guardrail_status": response.guardrail_status,
        "unprocessed_logs": response.unprocessed_logs,
        "group": group,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"非法 JSON 常量: {value}")


def decode_completed_inbound_response(payload: str) -> CompletedInboundResponse:
    if type(payload) is not str:
        raise CorruptClaimResponse("完成结果必须是 JSON 字符串")
    try:
        body = json.loads(payload, parse_constant=_reject_json_constant)
        if type(body) is not dict:
            raise TypeError("完成结果根节点必须是 object")
        if set(body) != _COMPLETED_FIELDS:
            raise ValueError("完成结果顶层字段不匹配 schema v1")
        if type(body["schema_version"]) is not int or body["schema_version"] != 1:
            raise ValueError("未知完成结果 schema_version")
        if type(body["reply_meta"]) is not dict:
            raise TypeError("reply_meta 必须是 object")
        raw_reply_meta = _copy_json_value(body["reply_meta"], path="reply_meta")
        if raw_reply_meta != sanitize_reply_meta(raw_reply_meta):
            raise ValueError("持久化 reply_meta 包含非 allowlist 字段或非法空值")

        group_payload = body["group"]
        group = None
        if group_payload is not None:
            if type(group_payload) is not dict:
                raise TypeError("group 必须是 object 或 null")
            if set(group_payload) != _GROUP_FIELDS:
                raise ValueError("group 字段不匹配 schema v1")
            if type(group_payload["diagnostics"]) is not dict:
                raise TypeError("group.diagnostics 必须是 object")
            if type(group_payload["duplicate_reply"]) is not dict:
                raise TypeError("group.duplicate_reply 必须是 object")
            group = GroupReplayFields(
                generation=group_payload["generation"],
                delay_seconds=group_payload["delay_seconds"],
                diagnostics=group_payload["diagnostics"],
                duplicate_reply=group_payload["duplicate_reply"],
                hard_rule=group_payload["hard_rule"],
            )
        return CompletedInboundResponse(
            schema_version=body["schema_version"],
            outcome=body["outcome"],
            reply=body["reply"],
            reply_meta=raw_reply_meta,
            reason=body["reason"],
            source=body["source"],
            intent=body["intent"],
            guardrail_status=body["guardrail_status"],
            unprocessed_logs=body["unprocessed_logs"],
            group=group,
        )
    except CorruptClaimResponse:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CorruptClaimResponse(str(exc)) from exc


def normalize_inbound_claim_key(
    platform: str,
    chat_type: str,
    session_id: str,
    message_id: str | None,
) -> InboundClaimKey | None:
    if message_id is None:
        return None
    if type(message_id) is not str:
        raise ValueError("message_id 必须是字符串或 null")
    normalized_message_id = message_id.strip()
    if not normalized_message_id:
        return None

    if type(platform) is not str:
        raise ValueError("platform 必须是字符串")
    normalized_platform = platform.strip().lower()
    if _PLATFORM_PATTERN.fullmatch(normalized_platform) is None:
        raise ValueError("platform 格式无效")

    if type(chat_type) is not str:
        raise ValueError("chat_type 必须是字符串")
    normalized_chat_type = chat_type.strip().lower()
    if normalized_chat_type not in {"private", "group"}:
        raise ValueError("chat_type 仅支持 private/group")

    if type(session_id) is not str:
        raise ValueError("session_id 必须是字符串")
    normalized_session_id = session_id.strip()
    if not normalized_session_id or len(normalized_session_id) > 255:
        raise ValueError("session_id 必须为 1-255 字符")
    if len(normalized_message_id) > 255:
        raise ValueError("message_id 最多 255 字符")

    return InboundClaimKey(
        platform=normalized_platform,
        chat_type=normalized_chat_type,
        session_id=normalized_session_id,
        message_id=normalized_message_id,
    )


def _utc_naive(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if not isinstance(value, datetime):
        raise TypeError("now 必须是 datetime 或 null")
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _lease_expiry(now: datetime, lease_seconds: int | float) -> datetime:
    if type(lease_seconds) not in (int, float):
        raise ValueError("lease_seconds 必须是正数")
    if not math.isfinite(float(lease_seconds)) or lease_seconds <= 0:
        raise ValueError("lease_seconds 必须是正数")
    return now + timedelta(seconds=lease_seconds)


def _reject_dirty_session(db: Any) -> None:
    try:
        in_transaction = getattr(db, "in_transaction")
        if not callable(in_transaction):
            raise AttributeError("in_transaction")
        if in_transaction():
            raise DirtyClaimSessionError(
                "claim service 要求入口 Session 不存在活动事务"
            )
        pending = bool(db.new) or bool(db.dirty) or bool(db.deleted)
    except AttributeError as exc:
        raise TypeError("db 必须是 SQLAlchemy Session") from exc
    if pending:
        raise DirtyClaimSessionError("claim service 拒绝含待提交 ORM 变更的 Session")


def _key_filters(key: InboundClaimKey) -> tuple[Any, ...]:
    return (
        InboundMessageClaim.platform == key.platform,
        InboundMessageClaim.chat_type == key.chat_type,
        InboundMessageClaim.session_id == key.session_id,
        InboundMessageClaim.message_id == key.message_id,
    )


def _rollback_preserving_primary(
    db: Any,
    primary: BaseException,
    *,
    label: str,
) -> None:
    """rollback 失败时保留主异常，并附加可诊断的次异常信息。"""
    try:
        db.rollback()
    except BaseException as rollback_error:
        try:
            note = (
                f"{label} rollback 失败: "
                f"{type(rollback_error).__name__}: {rollback_error}"
            )
        except BaseException:
            note = f"{label} rollback 失败，且无法格式化回滚异常"
        add_note = getattr(primary, "add_note", None)
        if callable(add_note):
            try:
                add_note(note)
                return
            except BaseException:
                pass
        try:
            primary.__cause__ = rollback_error
        except BaseException:
            try:
                primary.__context__ = rollback_error
            except BaseException:
                try:
                    setattr(primary, "__claim_rollback_note__", note)
                except BaseException:
                    pass


def _run_retry(db: Any, operation: Any, *, label: str) -> Any:
    try:
        return run_sqlite_locked_retry(
            operation,
            rollback=db.rollback,
            label=label,
        )
    except BaseException as exc:
        _rollback_preserving_primary(db, exc, label=label)
        raise


def acquire_inbound_claim(
    db: Any,
    key: InboundClaimKey | None,
    *,
    now: datetime | None = None,
    lease_seconds: int | float = DEFAULT_LEASE_SECONDS,
) -> InboundClaimDecision:
    if key is None:
        return InboundClaimDecision(kind=ClaimDecisionKind.BYPASS)
    _reject_dirty_session(db)
    try:
        if type(key) is not InboundClaimKey:
            raise TypeError("key 必须是 InboundClaimKey 或 null")
        acquired_at = _utc_naive(now)
        lease_expires_at = _lease_expiry(acquired_at, lease_seconds)
        owner_token = secrets.token_hex(32)
    except BaseException as exc:
        _rollback_preserving_primary(
            db,
            exc,
            label="acquire inbound claim parameter preparation",
        )
        raise
    key_filters = _key_filters(key)
    insert_statement = (
        sqlite_insert(InboundMessageClaim)
        .values(
            platform=key.platform,
            chat_type=key.chat_type,
            session_id=key.session_id,
            message_id=key.message_id,
            status=ClaimStatus.PROCESSING.value,
            owner_token=owner_token,
            lease_expires_at=lease_expires_at,
            response_json="",
            error_summary="",
            attempt_count=1,
            created_at=acquired_at,
            updated_at=acquired_at,
            completed_at=None,
        )
        .on_conflict_do_nothing(
            index_elements=["platform", "chat_type", "session_id", "message_id"]
        )
    )
    takeover_statement = (
        update(InboundMessageClaim)
        .where(
            *key_filters,
            or_(
                InboundMessageClaim.status == ClaimStatus.FAILED.value,
                and_(
                    InboundMessageClaim.status == ClaimStatus.PROCESSING.value,
                    or_(
                        InboundMessageClaim.lease_expires_at.is_(None),
                        InboundMessageClaim.lease_expires_at <= acquired_at,
                    ),
                ),
            ),
        )
        .values(
            status=ClaimStatus.PROCESSING.value,
            owner_token=owner_token,
            lease_expires_at=lease_expires_at,
            response_json="",
            error_summary="",
            attempt_count=InboundMessageClaim.attempt_count + 1,
            updated_at=acquired_at,
            completed_at=None,
        )
    )
    takeover_attempt_statement = select(InboundMessageClaim.attempt_count).where(
        *key_filters,
        InboundMessageClaim.owner_token == owner_token,
    )
    state_statement = select(
        InboundMessageClaim.status,
        InboundMessageClaim.owner_token,
        InboundMessageClaim.lease_expires_at,
        InboundMessageClaim.response_json,
        InboundMessageClaim.attempt_count,
    ).where(*key_filters)

    def operation() -> InboundClaimDecision:
        insert_result = db.execute(insert_statement)
        if insert_result.rowcount == 1:
            db.commit()
            return InboundClaimDecision(
                kind=ClaimDecisionKind.ACQUIRED,
                handle=InboundClaimHandle(
                    key=key,
                    owner_token=owner_token,
                    lease_expires_at=lease_expires_at,
                    attempt_count=1,
                ),
            )

        takeover_result = db.execute(takeover_statement)
        if takeover_result.rowcount == 1:
            takeover_attempt = db.execute(takeover_attempt_statement).scalar_one_or_none()
            if takeover_attempt is None:
                db.rollback()
                raise _RetryClaimState("claim takeover attempt could not be read")
            db.commit()
            return InboundClaimDecision(
                kind=ClaimDecisionKind.ACQUIRED,
                handle=InboundClaimHandle(
                    key=key,
                    owner_token=owner_token,
                    lease_expires_at=lease_expires_at,
                    attempt_count=int(takeover_attempt),
                ),
            )

        state = db.execute(state_statement).one_or_none()
        if state is None:
            db.rollback()
            raise _RetryClaimState("claim disappeared during acquisition")
        if state.status == ClaimStatus.PROCESSING.value:
            if state.owner_token == owner_token:
                handle = InboundClaimHandle(
                    key=key,
                    owner_token=owner_token,
                    lease_expires_at=state.lease_expires_at,
                    attempt_count=int(state.attempt_count),
                )
                db.rollback()
                return InboundClaimDecision(kind=ClaimDecisionKind.ACQUIRED, handle=handle)
            db.rollback()
            return InboundClaimDecision(kind=ClaimDecisionKind.DUPLICATE_INFLIGHT)
        if state.status == ClaimStatus.COMPLETED.value:
            try:
                response = decode_completed_inbound_response(state.response_json)
            except BaseException as exc:
                _rollback_preserving_primary(
                    db,
                    exc,
                    label="acquire inbound claim replay decode",
                )
                raise
            db.rollback()
            return InboundClaimDecision(kind=ClaimDecisionKind.REPLAY, response=response)

        db.rollback()
        raise _RetryClaimState(f"claim state changed during acquisition: {state.status}")

    for _ in range(_MAX_ACQUIRE_STATE_RETRIES):
        try:
            return _run_retry(db, operation, label="acquire inbound claim")
        except _RetryClaimState:
            continue
    raise ClaimContentionError("claim 状态在有界重试后仍不稳定")


def renew_inbound_claim(
    db: Any,
    handle: InboundClaimHandle,
    *,
    now: datetime | None = None,
    lease_seconds: int | float = DEFAULT_LEASE_SECONDS,
) -> bool:
    _reject_dirty_session(db)
    try:
        if type(handle) is not InboundClaimHandle:
            raise TypeError("handle 必须是 InboundClaimHandle")
        renewed_at = _utc_naive(now)
        lease_expires_at = _lease_expiry(renewed_at, lease_seconds)
    except BaseException as exc:
        _rollback_preserving_primary(
            db,
            exc,
            label="renew inbound claim parameter preparation",
        )
        raise
    statement = (
        update(InboundMessageClaim)
        .where(
            *_key_filters(handle.key),
            InboundMessageClaim.status == ClaimStatus.PROCESSING.value,
            InboundMessageClaim.owner_token == handle.owner_token,
        )
        .values(lease_expires_at=lease_expires_at, updated_at=renewed_at)
    )

    def operation() -> bool:
        result = db.execute(statement)
        if result.rowcount != 1:
            db.rollback()
            return False
        db.commit()
        return True

    return bool(_run_retry(db, operation, label="renew inbound claim"))


def complete_inbound_claim(
    db: Any,
    handle: InboundClaimHandle,
    response: CompletedInboundResponse,
    *,
    now: datetime | None = None,
) -> bool:
    _reject_dirty_session(db)
    try:
        if type(handle) is not InboundClaimHandle:
            raise TypeError("handle 必须是 InboundClaimHandle")
        response_json = encode_completed_inbound_response(response)
        completed_at = _utc_naive(now)
    except BaseException as exc:
        _rollback_preserving_primary(
            db,
            exc,
            label="complete inbound claim parameter preparation",
        )
        raise
    key_filters = _key_filters(handle.key)
    statement = (
        update(InboundMessageClaim)
        .where(
            *key_filters,
            InboundMessageClaim.status == ClaimStatus.PROCESSING.value,
            InboundMessageClaim.owner_token == handle.owner_token,
        )
        .values(
            status=ClaimStatus.COMPLETED.value,
            lease_expires_at=None,
            response_json=response_json,
            error_summary="",
            updated_at=completed_at,
            completed_at=completed_at,
        )
    )
    state_statement = select(
        InboundMessageClaim.status,
        InboundMessageClaim.owner_token,
        InboundMessageClaim.response_json,
    ).where(*key_filters)

    def operation() -> bool:
        result = db.execute(statement)
        if result.rowcount == 1:
            db.commit()
            return True
        state = db.execute(state_statement).one_or_none()
        is_same_terminal = bool(
            state is not None
            and state.status == ClaimStatus.COMPLETED.value
            and state.owner_token == handle.owner_token
            and state.response_json == response_json
        )
        db.rollback()
        return is_same_terminal

    return bool(_run_retry(db, operation, label="complete inbound claim"))


def _error_summary(error: Any) -> str:
    return " ".join(str(error).splitlines()).strip()[:500]


def fail_inbound_claim(
    db: Any,
    handle: InboundClaimHandle,
    error: Any,
    *,
    now: datetime | None = None,
) -> bool:
    _reject_dirty_session(db)
    try:
        if type(handle) is not InboundClaimHandle:
            raise TypeError("handle 必须是 InboundClaimHandle")
        failed_at = _utc_naive(now)
        error_summary = _error_summary(error)
    except BaseException as exc:
        _rollback_preserving_primary(
            db,
            exc,
            label="fail inbound claim parameter preparation",
        )
        raise
    key_filters = _key_filters(handle.key)
    statement = (
        update(InboundMessageClaim)
        .where(
            *key_filters,
            InboundMessageClaim.status == ClaimStatus.PROCESSING.value,
            InboundMessageClaim.owner_token == handle.owner_token,
        )
        .values(
            status=ClaimStatus.FAILED.value,
            lease_expires_at=None,
            response_json="",
            error_summary=error_summary,
            updated_at=failed_at,
            completed_at=None,
        )
    )
    state_statement = select(
        InboundMessageClaim.status,
        InboundMessageClaim.owner_token,
        InboundMessageClaim.error_summary,
    ).where(*key_filters)

    def operation() -> bool:
        result = db.execute(statement)
        if result.rowcount == 1:
            db.commit()
            return True
        state = db.execute(state_statement).one_or_none()
        is_same_terminal = bool(
            state is not None
            and state.status == ClaimStatus.FAILED.value
            and state.owner_token == handle.owner_token
            and state.error_summary == error_summary
        )
        db.rollback()
        return is_same_terminal

    return bool(_run_retry(db, operation, label="fail inbound claim"))
