"""框架无关、隐私安全的 Run Event Ledger 合同。

本模块只依赖 Python 标准库与同样框架无关的关联 ID 合同。数据库负责为每个
Run 分配严格递增的 ``sequence``；调用方只能提交不可变事件草稿，不能更新或
删除既有事实。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Protocol, TypeAlias, runtime_checkable

from core.telemetry.contracts import TelemetryCorrelation


RUN_LEDGER_SCHEMA_NAME = "nanobot.run_event"
RUN_LEDGER_SCHEMA_VERSION = 1

_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")
_PAYLOAD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ACTIVE_RUN_STATUSES = frozenset({
    "running",
    "waiting_approval",
    "waiting_input",
})
_TERMINAL_RUN_STATUSES = frozenset({
    "ambiguous",
    "cancelled",
    "failed",
    "succeeded",
    "timed_out",
})
_SAFE_AUDIT_SUFFIXES = (
    "_bytes",
    "_chars",
    "_code",
    "_count",
    "_id",
    "_sha256",
    "_status",
    "_tokens",
    "_truncated",
    "_type",
)
_SAFE_SENSITIVE_KEYS = frozenset({
    "input_token_estimate",
    "prompt_key",
    "prompt_mode",
})
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "args",
    "authorization",
    "body",
    "command",
    "content",
    "cookie",
    "data",
    "detail",
    "error",
    "headers",
    "input",
    "message",
    "oauth",
    "output",
    "password",
    "path",
    "payload",
    "prompt",
    "query",
    "raw",
    "reason",
    "reasoning",
    "request",
    "resource",
    "response",
    "result",
    "secret",
    "stderr",
    "stdout",
    "summary",
    "text",
    "token",
    "uri",
    "url",
)

RunLedgerScalar: TypeAlias = str | int | float | bool | None


def canonical_run_status(value: object) -> str:
    """把既有 Trace 状态归一为稳定的 Ledger 状态词汇。"""

    normalized = str(value or "unknown").strip().lower() or "unknown"
    return {
        "success": "succeeded",
        "stream_success": "succeeded",
        "no_reply": "succeeded",
        "cancel": "cancelled",
        "canceled": "cancelled",
        "timeout": "timed_out",
        "error": "failed",
        "failure": "failed",
        "empty": "failed",
        "suppressed": "failed",
    }.get(normalized, normalized)


def is_terminal_run_status(value: object) -> bool:
    return canonical_run_status(value) in _TERMINAL_RUN_STATUSES


class RunLedgerContractError(ValueError):
    """事件或序列不符合稳定 Ledger 合同。"""


class RunLedgerConflictError(RuntimeError):
    """幂等键、期望序列、身份或终态边界发生冲突。"""


class RunLedgerIntegrityError(RuntimeError):
    """持久记录的摘要链或 payload 与声明不一致。"""


class UnsupportedRunLedgerSchemaError(RunLedgerContractError):
    """读取到当前代码不支持的 Event schema。"""


def _required_identifier(
    value: object,
    field_name: str,
    *,
    max_chars: int = 160,
) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > max_chars
        or any(ord(character) < 32 for character in normalized)
    ):
        raise RunLedgerContractError(f"{field_name} 无效")
    return normalized


def _optional_identifier(
    value: object,
    field_name: str,
    *,
    max_chars: int = 160,
) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    return _required_identifier(
        normalized,
        field_name,
        max_chars=max_chars,
    )


def _event_type(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if _IDENTIFIER_RE.fullmatch(normalized) is None or "." not in normalized:
        raise RunLedgerContractError(f"event_type 无效：{normalized!r}")
    return normalized


def _normalize_payload(
    payload: Mapping[str, RunLedgerScalar],
) -> Mapping[str, RunLedgerScalar]:
    if not isinstance(payload, Mapping):
        raise RunLedgerContractError("payload 必须是 Mapping")
    if len(payload) > 64:
        raise RunLedgerContractError("payload 字段不能超过 64 个")
    normalized: dict[str, RunLedgerScalar] = {}
    for raw_key, value in payload.items():
        key = str(raw_key or "").strip().lower()
        if _PAYLOAD_KEY_RE.fullmatch(key) is None:
            raise RunLedgerContractError(f"payload 字段名无效：{key!r}")
        if (
            any(part in key for part in _SENSITIVE_KEY_PARTS)
            and key not in _SAFE_SENSITIVE_KEYS
            and not key.endswith(_SAFE_AUDIT_SUFFIXES)
        ):
            raise RunLedgerContractError(
                f"payload 字段可能包含敏感正文：{key}"
            )
        if type(value) not in {str, int, float, bool, type(None)}:
            raise RunLedgerContractError(
                f"payload.{key} 只允许 JSON scalar"
            )
        if isinstance(value, str):
            text = value.strip()
            if len(text) > 512 or any(ord(character) < 32 for character in text):
                raise RunLedgerContractError(f"payload.{key} 文本无效")
            normalized[key] = text
            continue
        if isinstance(value, float) and not math.isfinite(value):
            raise RunLedgerContractError(f"payload.{key} 数值必须有限")
        normalized[key] = value
    return MappingProxyType(normalized)


@dataclass(frozen=True, slots=True)
class RunLedgerIdentity:
    """可选的受信 actor／owner 快照；未知字段保持为空，不做推断。"""

    actor_type: str = ""
    actor_id: str = ""
    parent_actor_id: str = ""
    owner_platform: str = ""
    owner_type: str = ""
    owner_id: str = ""

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            object.__setattr__(
                self,
                field_name,
                _optional_identifier(
                    getattr(self, field_name),
                    f"identity.{field_name}",
                ),
            )
        owner_values = (
            self.owner_platform,
            self.owner_type,
            self.owner_id,
        )
        if any(owner_values) and not all(owner_values):
            raise RunLedgerContractError(
                "owner_platform、owner_type、owner_id 必须同时提供"
            )
        if self.actor_type and not self.actor_id:
            raise RunLedgerContractError("actor_type 需要 actor_id")
        if self.actor_id and not self.actor_type:
            raise RunLedgerContractError("actor_id 需要 actor_type")

    def to_dict(self) -> dict[str, str]:
        return {
            field_name: str(getattr(self, field_name) or "")
            for field_name in self.__dataclass_fields__
        }


@dataclass(frozen=True, slots=True)
class RunLedgerEventDraft:
    """尚未分配账本序号的不可变事件。"""

    event_id: str
    run_id: str
    event_type: str
    occurred_at: datetime
    source: str
    correlation: TelemetryCorrelation = field(
        default_factory=TelemetryCorrelation
    )
    identity: RunLedgerIdentity = field(default_factory=RunLedgerIdentity)
    status: str = ""
    payload: Mapping[str, RunLedgerScalar] = field(default_factory=dict)
    source_event_id: str = ""
    source_sequence: int = 0
    correction_of_event_id: str = ""
    schema_version: int = RUN_LEDGER_SCHEMA_VERSION
    dropped_field_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "event_id",
            _required_identifier(self.event_id, "event_id"),
        )
        run_id = _required_identifier(self.run_id, "run_id")
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "event_type", _event_type(self.event_type))
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise RunLedgerContractError("occurred_at 必须包含时区")
        object.__setattr__(
            self,
            "source",
            _required_identifier(self.source, "source", max_chars=128),
        )
        if not isinstance(self.correlation, TelemetryCorrelation):
            raise RunLedgerContractError("correlation 无效")
        if self.correlation.run_id and self.correlation.run_id != run_id:
            raise RunLedgerContractError("correlation.run_id 与 run_id 不一致")
        if not self.correlation.run_id:
            object.__setattr__(
                self,
                "correlation",
                replace(self.correlation, run_id=run_id),
            )
        if not isinstance(self.identity, RunLedgerIdentity):
            raise RunLedgerContractError("identity 无效")
        object.__setattr__(
            self,
            "status",
            _optional_identifier(self.status, "status", max_chars=64),
        )
        object.__setattr__(self, "payload", _normalize_payload(self.payload))
        object.__setattr__(
            self,
            "source_event_id",
            _optional_identifier(self.source_event_id, "source_event_id"),
        )
        if type(self.source_sequence) is not int or self.source_sequence < 0:
            raise RunLedgerContractError("source_sequence 必须是非负整数")
        object.__setattr__(
            self,
            "correction_of_event_id",
            _optional_identifier(
                self.correction_of_event_id,
                "correction_of_event_id",
            ),
        )
        if type(self.schema_version) is not int or self.schema_version <= 0:
            raise RunLedgerContractError("schema_version 必须是正整数")
        if self.schema_version != RUN_LEDGER_SCHEMA_VERSION:
            raise UnsupportedRunLedgerSchemaError(
                "新事件只能使用当前 Run Ledger schema version "
                f"{RUN_LEDGER_SCHEMA_VERSION}"
            )
        if (
            type(self.dropped_field_count) is not int
            or self.dropped_field_count < 0
        ):
            raise RunLedgerContractError(
                "dropped_field_count 必须是非负整数"
            )
        if self.event_type == "run.event_corrected":
            if not self.correction_of_event_id:
                raise RunLedgerContractError(
                    "run.event_corrected 必须声明 correction_of_event_id"
                )
        elif self.correction_of_event_id:
            raise RunLedgerContractError(
                "只有 run.event_corrected 可以声明 correction_of_event_id"
            )
        if self.event_type in {"run.status_changed", "run.terminated"} and not self.status:
            raise RunLedgerContractError(
                f"{self.event_type} 必须声明 status"
            )
        if self.event_type == "run.accepted" and self.status != "accepted":
            raise RunLedgerContractError("run.accepted 必须携带 accepted 状态")
        if (
            self.event_type == "run.status_changed"
            and self.status not in _ACTIVE_RUN_STATUSES
        ):
            raise RunLedgerContractError(
                "run.status_changed 必须携带非终态 RuntimeRunStatus"
            )
        if (
            self.event_type == "run.terminated"
            and self.status not in _TERMINAL_RUN_STATUSES
        ):
            raise RunLedgerContractError(
                "run.terminated 必须携带终态 RuntimeRunStatus"
            )

    @property
    def terminal(self) -> bool:
        return self.event_type == "run.terminated"


@dataclass(frozen=True, slots=True)
class RunLedgerEventRecord:
    """已提交到持久账本的不可变记录。"""

    position: int
    sequence: int
    event: RunLedgerEventDraft
    recorded_at: datetime
    previous_event_sha256: str
    event_sha256: str

    def __post_init__(self) -> None:
        if type(self.position) is not int or self.position <= 0:
            raise RunLedgerContractError("position 必须是正整数")
        if type(self.sequence) is not int or self.sequence <= 0:
            raise RunLedgerContractError("sequence 必须是正整数")
        if not isinstance(self.event, RunLedgerEventDraft):
            raise RunLedgerContractError("event 无效")
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise RunLedgerContractError("recorded_at 必须包含时区")
        previous = str(self.previous_event_sha256 or "").strip().lower()
        if previous and _SHA256_RE.fullmatch(previous) is None:
            raise RunLedgerContractError("previous_event_sha256 无效")
        digest = str(self.event_sha256 or "").strip().lower()
        if _SHA256_RE.fullmatch(digest) is None:
            raise RunLedgerContractError("event_sha256 无效")
        object.__setattr__(self, "previous_event_sha256", previous)
        object.__setattr__(self, "event_sha256", digest)

    @property
    def event_id(self) -> str:
        return self.event.event_id

    @property
    def run_id(self) -> str:
        return self.event.run_id

    @property
    def event_type(self) -> str:
        return self.event.event_type

    @property
    def status(self) -> str:
        return self.event.status

    @property
    def payload(self) -> Mapping[str, RunLedgerScalar]:
        return self.event.payload

    @property
    def terminal(self) -> bool:
        return self.event.terminal


@dataclass(frozen=True, slots=True)
class RunLedgerHead:
    run_id: str
    last_sequence: int
    last_event_id: str
    last_event_sha256: str
    terminal_sequence: int | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "run_id",
            _required_identifier(self.run_id, "run_id"),
        )
        if type(self.last_sequence) is not int or self.last_sequence < 0:
            raise RunLedgerContractError("last_sequence 必须是非负整数")
        if self.last_sequence == 0:
            if self.last_event_id or self.last_event_sha256:
                raise RunLedgerContractError("空 Ledger head 不能引用事件")
        else:
            _required_identifier(self.last_event_id, "last_event_id")
            if _SHA256_RE.fullmatch(self.last_event_sha256) is None:
                raise RunLedgerContractError("last_event_sha256 无效")
        if self.terminal_sequence is not None and (
            type(self.terminal_sequence) is not int
            or not 0 < self.terminal_sequence <= self.last_sequence
        ):
            raise RunLedgerContractError("terminal_sequence 无效")


def encode_run_ledger_payload(
    payload: Mapping[str, RunLedgerScalar],
) -> str:
    normalized = _normalize_payload(payload)
    return json.dumps(
        dict(normalized),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def decode_run_ledger_payload(
    payload_json: str,
    *,
    schema_version: int,
) -> Mapping[str, RunLedgerScalar]:
    """读取时的显式 upcast 入口；新增版本必须逐版注册迁移。"""

    if schema_version != RUN_LEDGER_SCHEMA_VERSION:
        raise UnsupportedRunLedgerSchemaError(
            "不支持 Run Ledger schema version "
            f"{schema_version}；当前为 {RUN_LEDGER_SCHEMA_VERSION}"
        )
    try:
        decoded = json.loads(str(payload_json or "{}"))
    except json.JSONDecodeError as exc:
        raise RunLedgerIntegrityError("Run Ledger payload JSON 已损坏") from exc
    if not isinstance(decoded, dict):
        raise RunLedgerIntegrityError("Run Ledger payload 根节点必须是对象")
    try:
        return _normalize_payload(decoded)
    except RunLedgerContractError as exc:
        raise RunLedgerIntegrityError(
            "Run Ledger payload 不符合对应 schema"
        ) from exc


def run_ledger_payload_sha256(
    payload: Mapping[str, RunLedgerScalar],
) -> str:
    return hashlib.sha256(
        encode_run_ledger_payload(payload).encode("utf-8")
    ).hexdigest()


def run_ledger_event_sha256(
    event: RunLedgerEventDraft,
    *,
    sequence: int,
    previous_event_sha256: str,
) -> str:
    if type(sequence) is not int or sequence <= 0:
        raise RunLedgerContractError("sequence 必须是正整数")
    previous = str(previous_event_sha256 or "").strip().lower()
    if previous and _SHA256_RE.fullmatch(previous) is None:
        raise RunLedgerContractError("previous_event_sha256 无效")
    canonical = {
        "schema_name": RUN_LEDGER_SCHEMA_NAME,
        "schema_version": event.schema_version,
        "event_id": event.event_id,
        "run_id": event.run_id,
        "sequence": sequence,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at.astimezone(timezone.utc).isoformat(
            timespec="microseconds"
        ),
        "source": event.source,
        "source_event_id": event.source_event_id,
        "source_sequence": event.source_sequence,
        "correlation": event.correlation.to_dict(),
        "identity": event.identity.to_dict(),
        "status": event.status,
        "payload": dict(event.payload),
        "dropped_field_count": event.dropped_field_count,
        "correction_of_event_id": event.correction_of_event_id,
        "previous_event_sha256": previous,
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@runtime_checkable
class RunEventLedgerPort(Protocol):
    """同步事务 Port；具体 Adapter 决定提交边界。"""

    def append(
        self,
        event: RunLedgerEventDraft,
        *,
        expected_sequence: int | None = None,
    ) -> RunLedgerEventRecord: ...

    def read(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        through_sequence: int | None = None,
        limit: int = 1000,
    ) -> tuple[RunLedgerEventRecord, ...]: ...

    def head(self, run_id: str) -> RunLedgerHead | None: ...


__all__ = [
    "RUN_LEDGER_SCHEMA_NAME",
    "RUN_LEDGER_SCHEMA_VERSION",
    "RunEventLedgerPort",
    "RunLedgerConflictError",
    "RunLedgerContractError",
    "RunLedgerEventDraft",
    "RunLedgerEventRecord",
    "RunLedgerHead",
    "RunLedgerIdentity",
    "RunLedgerIntegrityError",
    "RunLedgerScalar",
    "UnsupportedRunLedgerSchemaError",
    "canonical_run_status",
    "decode_run_ledger_payload",
    "encode_run_ledger_payload",
    "is_terminal_run_status",
    "run_ledger_event_sha256",
    "run_ledger_payload_sha256",
]
