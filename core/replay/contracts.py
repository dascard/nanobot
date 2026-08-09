"""离线语义回放的稳定合同。

冻结数据只保存标识、状态、计数和 SHA-256 摘要，不保存原始消息、Prompt、
工具参数、工具结果或隐藏推理。这样同一份 fixture 可以被安全地用于本地回放，
同时不会把生产内容复制进评测报告。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Any

from core.agent_runtime.contracts import RuntimeToolCallStatus
from core.agent_runtime.recovery import (
    RuntimeSideEffectState,
    RuntimeToolEffectClass,
)


REPLAY_SCHEMA_VERSION = 1
REPLAY_MODE = "semantic"
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,255}")
_MAX_FROZEN_EVENTS = 10_000
_MAX_MODEL_RESPONSES = 1_000
_MAX_STREAM_CHUNKS = 10_000
_MAX_TOOL_OUTCOMES = 1_000


class ReplayContractError(ValueError):
    """冻结回放输入不满足安全或一致性合同。"""


class ReplayFaultKind(str, Enum):
    MODEL_TIMEOUT = "model_timeout"
    STREAM_INTERRUPTED = "stream_interrupted"
    TOOL_FAILURE = "tool_failure"
    DB_LOCKED = "db_locked"
    LEASE_LOST = "lease_lost"
    SANDBOX_RESTARTED = "sandbox_restarted"


REQUIRED_FAULT_KINDS = tuple(ReplayFaultKind)


class ReplayStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


def canonical_json(value: object) -> str:
    """生成与键顺序无关的稳定 JSON。"""

    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ReplayContractError(f"{name} 必须是字符串")
    normalized = value.strip()
    if not normalized:
        raise ReplayContractError(f"{name} 不能为空")
    if len(normalized) > 256:
        raise ReplayContractError(f"{name} 不能超过 256 个字符")
    return normalized


def _identifier(value: object, name: str) -> str:
    normalized = _required_text(value, name)
    if _IDENTIFIER_RE.fullmatch(normalized) is None:
        raise ReplayContractError(
            f"{name} 只能包含安全标识符字符"
        )
    return normalized


def _sha256(value: object, name: str) -> str:
    normalized = _required_text(value, name).lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ReplayContractError(f"{name} 必须是 64 位十六进制摘要")
    return normalized


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ReplayContractError(f"{name} 必须是正整数")
    return value


def _non_negative_int(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ReplayContractError(f"{name} 必须是非负整数")
    return value


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReplayContractError(f"{name} 必须是 JSON 对象")
    if any(not isinstance(key, str) for key in value):
        raise ReplayContractError(f"{name} 的键必须是字符串")
    return value


def _sequence(value: object, name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ReplayContractError(f"{name} 必须是 JSON 数组")
    return value


def _keys(
    value: Mapping[str, Any],
    *,
    name: str,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> None:
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - required - optional)
    if missing:
        raise ReplayContractError(f"{name} 缺少字段: {', '.join(missing)}")
    if unknown:
        raise ReplayContractError(
            f"{name} 包含未允许字段: {', '.join(unknown)}"
        )


@dataclass(frozen=True, slots=True)
class ReplayComponentRef:
    component_id: str
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "component_id",
            _identifier(self.component_id, "component_id"),
        )
        object.__setattr__(self, "sha256", _sha256(self.sha256, "sha256"))

    def to_dict(self) -> dict[str, str]:
        return {"id": self.component_id, "sha256": self.sha256}

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        name: str,
    ) -> "ReplayComponentRef":
        payload = _mapping(value, name)
        _keys(
            payload,
            name=name,
            required=frozenset({"id", "sha256"}),
        )
        return cls(
            component_id=payload["id"],
            sha256=payload["sha256"],
        )


@dataclass(frozen=True, slots=True)
class ReplayVariant:
    runtime: ReplayComponentRef
    prompt: ReplayComponentRef
    model: ReplayComponentRef
    skill_set: ReplayComponentRef
    context_policy: ReplayComponentRef

    DIMENSIONS = (
        "runtime",
        "prompt",
        "model",
        "skill_set",
        "context_policy",
    )

    def __post_init__(self) -> None:
        for name in self.DIMENSIONS:
            if not isinstance(getattr(self, name), ReplayComponentRef):
                raise ReplayContractError(f"variant.{name} 无效")

    @property
    def fingerprint(self) -> str:
        return sha256_json(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            name: getattr(self, name).to_dict()
            for name in self.DIMENSIONS
        }

    @classmethod
    def from_dict(cls, value: object) -> "ReplayVariant":
        payload = _mapping(value, "variant")
        dimensions = frozenset(cls.DIMENSIONS)
        _keys(payload, name="variant", required=dimensions)
        return cls(**{
            name: ReplayComponentRef.from_dict(
                payload[name],
                name=f"variant.{name}",
            )
            for name in cls.DIMENSIONS
        })


@dataclass(frozen=True, slots=True)
class FrozenEvent:
    event_id: str
    sequence: int
    kind: str
    status: str
    payload_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "event_id",
            _identifier(self.event_id, "event_id"),
        )
        object.__setattr__(
            self,
            "sequence",
            _positive_int(self.sequence, "event.sequence"),
        )
        object.__setattr__(self, "kind", _identifier(self.kind, "event.kind"))
        object.__setattr__(
            self,
            "status",
            _identifier(self.status, "event.status"),
        )
        object.__setattr__(
            self,
            "payload_sha256",
            _sha256(self.payload_sha256, "event.payload_sha256"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "kind": self.kind,
            "status": self.status,
            "payload_sha256": self.payload_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> "FrozenEvent":
        payload = _mapping(value, "event")
        _keys(
            payload,
            name="event",
            required=frozenset({
                "event_id",
                "sequence",
                "kind",
                "status",
                "payload_sha256",
            }),
        )
        return cls(**dict(payload))


@dataclass(frozen=True, slots=True)
class FrozenReplayFixture:
    fixture_id: str
    source_run_id: str
    events: tuple[FrozenEvent, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "fixture_id",
            _identifier(self.fixture_id, "fixture_id"),
        )
        object.__setattr__(
            self,
            "source_run_id",
            _identifier(self.source_run_id, "source_run_id"),
        )
        events = tuple(self.events)
        if not events:
            raise ReplayContractError("fixture.events 不能为空")
        if len(events) > _MAX_FROZEN_EVENTS:
            raise ReplayContractError(
                f"fixture.events 不能超过 {_MAX_FROZEN_EVENTS} 项"
            )
        if any(not isinstance(item, FrozenEvent) for item in events):
            raise ReplayContractError("fixture.events 包含无效 Event")
        expected_sequences = tuple(range(1, len(events) + 1))
        if tuple(item.sequence for item in events) != expected_sequences:
            raise ReplayContractError("fixture.events sequence 必须从 1 连续递增")
        if len({item.event_id for item in events}) != len(events):
            raise ReplayContractError("fixture.events event_id 不能重复")
        object.__setattr__(self, "events", events)

    @property
    def fingerprint(self) -> str:
        return sha256_json(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": REPLAY_SCHEMA_VERSION,
            "fixture_id": self.fixture_id,
            "source_run_id": self.source_run_id,
            "events": [item.to_dict() for item in self.events],
        }

    @classmethod
    def from_dict(cls, value: object) -> "FrozenReplayFixture":
        payload = _mapping(value, "fixture")
        _keys(
            payload,
            name="fixture",
            required=frozenset({
                "schema_version",
                "fixture_id",
                "source_run_id",
                "events",
            }),
        )
        if (
            type(payload["schema_version"]) is not int
            or payload["schema_version"] != REPLAY_SCHEMA_VERSION
        ):
            raise ReplayContractError("fixture.schema_version 不受支持")
        return cls(
            fixture_id=payload["fixture_id"],
            source_run_id=payload["source_run_id"],
            events=tuple(
                FrozenEvent.from_dict(item)
                for item in _sequence(payload["events"], "fixture.events")
            ),
        )


@dataclass(frozen=True, slots=True)
class ReplayUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_microunits: int = 0

    def __post_init__(self) -> None:
        for name in (
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "cost_microunits",
        ):
            object.__setattr__(
                self,
                name,
                _non_negative_int(getattr(self, name), f"usage.{name}"),
            )

    def __add__(self, other: "ReplayUsage") -> "ReplayUsage":
        if not isinstance(other, ReplayUsage):
            return NotImplemented
        return ReplayUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            cost_microunits=self.cost_microunits + other.cost_microunits,
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cost_microunits": self.cost_microunits,
        }

    @classmethod
    def from_dict(cls, value: object | None) -> "ReplayUsage":
        if value is None:
            return cls()
        payload = _mapping(value, "usage")
        fields = frozenset({
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "cost_microunits",
        })
        _keys(payload, name="usage", required=frozenset(), optional=fields)
        return cls(**{name: payload.get(name, 0) for name in fields})


@dataclass(frozen=True, slots=True)
class FrozenToolOutcome:
    tool_call_id: str
    tool_name: str
    request_sha256: str
    result_sha256: str
    effect_class: RuntimeToolEffectClass
    status: RuntimeToolCallStatus
    receipt_id: str = ""
    receipt_state: RuntimeSideEffectState | None = None

    def __post_init__(self) -> None:
        for name in ("tool_call_id", "tool_name"):
            object.__setattr__(
                self,
                name,
                _identifier(getattr(self, name), name),
            )
        object.__setattr__(
            self,
            "request_sha256",
            _sha256(self.request_sha256, "tool.request_sha256"),
        )
        object.__setattr__(
            self,
            "result_sha256",
            _sha256(self.result_sha256, "tool.result_sha256"),
        )
        try:
            effect_class = RuntimeToolEffectClass(self.effect_class)
        except ValueError as exc:
            raise ReplayContractError("tool.effect_class 无效") from exc
        object.__setattr__(self, "effect_class", effect_class)
        try:
            status = RuntimeToolCallStatus(self.status)
        except ValueError as exc:
            raise ReplayContractError("tool.status 无效") from exc
        if not status.is_terminal:
            raise ReplayContractError("tool.status 必须是终态")
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "receipt_id",
            (
                _identifier(self.receipt_id, "tool.receipt_id")
                if self.receipt_id
                else ""
            ),
        )
        if self.receipt_state in (None, ""):
            receipt_state = None
        else:
            try:
                receipt_state = RuntimeSideEffectState(self.receipt_state)
            except ValueError as exc:
                raise ReplayContractError("tool.receipt_state 无效") from exc
        object.__setattr__(self, "receipt_state", receipt_state)
        if effect_class is RuntimeToolEffectClass.READ_ONLY and (
            self.receipt_id or receipt_state is not None
        ):
            raise ReplayContractError("只读工具不能携带副作用回执")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "request_sha256": self.request_sha256,
            "result_sha256": self.result_sha256,
            "effect_class": self.effect_class.value,
            "status": self.status.value,
        }
        if self.receipt_id:
            payload["receipt_id"] = self.receipt_id
        if self.receipt_state is not None:
            payload["receipt_state"] = self.receipt_state.value
        return payload

    @classmethod
    def from_dict(cls, value: object) -> "FrozenToolOutcome":
        payload = _mapping(value, "tool_outcome")
        _keys(
            payload,
            name="tool_outcome",
            required=frozenset({
                "tool_call_id",
                "tool_name",
                "request_sha256",
                "result_sha256",
                "effect_class",
                "status",
            }),
            optional=frozenset({"receipt_id", "receipt_state"}),
        )
        return cls(
            tool_call_id=payload["tool_call_id"],
            tool_name=payload["tool_name"],
            request_sha256=payload["request_sha256"],
            result_sha256=payload["result_sha256"],
            effect_class=payload["effect_class"],
            status=payload["status"],
            receipt_id=payload.get("receipt_id", ""),
            receipt_state=payload.get("receipt_state"),
        )


@dataclass(frozen=True, slots=True)
class FrozenModelResponse:
    step_id: str
    request_sha256: str
    response_sha256: str
    stream_chunk_sha256s: tuple[str, ...] = ()
    tool_outcomes: tuple[FrozenToolOutcome, ...] = ()
    usage: ReplayUsage = ReplayUsage()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "step_id",
            _identifier(self.step_id, "model.step_id"),
        )
        for name in ("request_sha256", "response_sha256"):
            object.__setattr__(
                self,
                name,
                _sha256(getattr(self, name), f"model.{name}"),
            )
        chunks = tuple(
            _sha256(item, "model.stream_chunk_sha256")
            for item in self.stream_chunk_sha256s
        )
        if len(chunks) > _MAX_STREAM_CHUNKS:
            raise ReplayContractError(
                f"model.stream_chunk_sha256s 不能超过 {_MAX_STREAM_CHUNKS} 项"
            )
        object.__setattr__(self, "stream_chunk_sha256s", chunks)
        outcomes = tuple(self.tool_outcomes)
        if len(outcomes) > _MAX_TOOL_OUTCOMES:
            raise ReplayContractError(
                f"model.tool_outcomes 不能超过 {_MAX_TOOL_OUTCOMES} 项"
            )
        if any(not isinstance(item, FrozenToolOutcome) for item in outcomes):
            raise ReplayContractError("model.tool_outcomes 包含无效结果")
        if len({item.tool_call_id for item in outcomes}) != len(outcomes):
            raise ReplayContractError("model.tool_outcomes tool_call_id 不能重复")
        object.__setattr__(self, "tool_outcomes", outcomes)
        if not isinstance(self.usage, ReplayUsage):
            raise ReplayContractError("model.usage 无效")

    def to_dict(self) -> dict[str, object]:
        return {
            "step_id": self.step_id,
            "request_sha256": self.request_sha256,
            "response_sha256": self.response_sha256,
            "stream_chunk_sha256s": list(self.stream_chunk_sha256s),
            "tool_outcomes": [item.to_dict() for item in self.tool_outcomes],
            "usage": self.usage.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> "FrozenModelResponse":
        payload = _mapping(value, "model_response")
        _keys(
            payload,
            name="model_response",
            required=frozenset({
                "step_id",
                "request_sha256",
                "response_sha256",
            }),
            optional=frozenset({
                "stream_chunk_sha256s",
                "tool_outcomes",
                "usage",
            }),
        )
        return cls(
            step_id=payload["step_id"],
            request_sha256=payload["request_sha256"],
            response_sha256=payload["response_sha256"],
            stream_chunk_sha256s=tuple(
                _sha256(item, "model.stream_chunk_sha256")
                for item in _sequence(
                    payload.get("stream_chunk_sha256s", ()),
                    "model.stream_chunk_sha256s",
                )
            ),
            tool_outcomes=tuple(
                FrozenToolOutcome.from_dict(item)
                for item in _sequence(
                    payload.get("tool_outcomes", ()),
                    "model.tool_outcomes",
                )
            ),
            usage=ReplayUsage.from_dict(payload.get("usage")),
        )


@dataclass(frozen=True, slots=True)
class ReplayScript:
    variant: ReplayVariant
    model_responses: tuple[FrozenModelResponse, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.variant, ReplayVariant):
            raise ReplayContractError("script.variant 无效")
        responses = tuple(self.model_responses)
        if not responses:
            raise ReplayContractError("script.model_responses 不能为空")
        if len(responses) > _MAX_MODEL_RESPONSES:
            raise ReplayContractError(
                f"script.model_responses 不能超过 {_MAX_MODEL_RESPONSES} 项"
            )
        if any(not isinstance(item, FrozenModelResponse) for item in responses):
            raise ReplayContractError("script.model_responses 包含无效响应")
        if len({item.step_id for item in responses}) != len(responses):
            raise ReplayContractError("script.model_responses step_id 不能重复")
        tool_ids = [
            item.tool_call_id
            for response in responses
            for item in response.tool_outcomes
        ]
        if len(tool_ids) != len(set(tool_ids)):
            raise ReplayContractError("script 中 tool_call_id 不能重复")
        object.__setattr__(self, "model_responses", responses)

    @property
    def fingerprint(self) -> str:
        return sha256_json(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": REPLAY_SCHEMA_VERSION,
            "variant": self.variant.to_dict(),
            "model_responses": [
                item.to_dict() for item in self.model_responses
            ],
        }

    @classmethod
    def from_dict(cls, value: object) -> "ReplayScript":
        payload = _mapping(value, "script")
        _keys(
            payload,
            name="script",
            required=frozenset({
                "schema_version",
                "variant",
                "model_responses",
            }),
        )
        if (
            type(payload["schema_version"]) is not int
            or payload["schema_version"] != REPLAY_SCHEMA_VERSION
        ):
            raise ReplayContractError("script.schema_version 不受支持")
        return cls(
            variant=ReplayVariant.from_dict(payload["variant"]),
            model_responses=tuple(
                FrozenModelResponse.from_dict(item)
                for item in _sequence(
                    payload["model_responses"],
                    "script.model_responses",
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class ReplayFault:
    kind: ReplayFaultKind
    target_id: str = ""
    after_count: int = 0
    repeat_count: int = 1

    def __post_init__(self) -> None:
        try:
            kind = ReplayFaultKind(self.kind)
        except ValueError as exc:
            raise ReplayContractError("fault.kind 无效") from exc
        object.__setattr__(self, "kind", kind)
        object.__setattr__(
            self,
            "target_id",
            (
                _identifier(self.target_id, "fault.target_id")
                if self.target_id
                else ""
            ),
        )
        object.__setattr__(
            self,
            "after_count",
            _non_negative_int(self.after_count, "fault.after_count"),
        )
        object.__setattr__(
            self,
            "repeat_count",
            _positive_int(self.repeat_count, "fault.repeat_count"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "target_id": self.target_id,
            "after_count": self.after_count,
            "repeat_count": self.repeat_count,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ReplayFault":
        payload = _mapping(value, "fault")
        _keys(
            payload,
            name="fault",
            required=frozenset({"kind"}),
            optional=frozenset({
                "target_id",
                "after_count",
                "repeat_count",
            }),
        )
        return cls(
            kind=payload["kind"],
            target_id=payload.get("target_id", ""),
            after_count=payload.get("after_count", 0),
            repeat_count=payload.get("repeat_count", 1),
        )


def model_request_sha256(
    fixture: FrozenReplayFixture,
    variant: ReplayVariant,
    *,
    step_id: str,
    state_sha256s: Sequence[str],
) -> str:
    """计算冻结模型响应必须绑定的请求摘要。"""

    normalized_state = [
        _sha256(item, "state_sha256") for item in state_sha256s
    ]
    return sha256_json({
        "schema_version": REPLAY_SCHEMA_VERSION,
        "fixture_sha256": fixture.fingerprint,
        "variant_sha256": variant.fingerprint,
        "step_id": _identifier(step_id, "step_id"),
        "state_sha256s": normalized_state,
    })


def initial_replay_state(fixture: FrozenReplayFixture) -> tuple[str, ...]:
    return tuple(item.payload_sha256 for item in fixture.events)


def parse_faults(value: object) -> tuple[ReplayFault, ...]:
    return tuple(
        ReplayFault.from_dict(item)
        for item in _sequence(value, "faults")
    )


__all__ = [
    "FrozenEvent",
    "FrozenModelResponse",
    "FrozenReplayFixture",
    "FrozenToolOutcome",
    "REPLAY_MODE",
    "REPLAY_SCHEMA_VERSION",
    "REQUIRED_FAULT_KINDS",
    "ReplayComponentRef",
    "ReplayContractError",
    "ReplayFault",
    "ReplayFaultKind",
    "ReplayScript",
    "ReplayStatus",
    "ReplayUsage",
    "ReplayVariant",
    "canonical_json",
    "initial_replay_state",
    "model_request_sha256",
    "parse_faults",
    "sha256_json",
]
