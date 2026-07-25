"""群学习规则启停的类型化配置与执行策略。"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from types import MappingProxyType
from typing import Mapping

from core.chat_stream_identity import parse_canonical_chat_stream_id
from core.group_learning.rules import LEARNING_SIGNAL_RULE_REGISTRY
from core.settings_service import settings


GROUP_LEARNING_RULE_CONTROLS_SETTING = "group_learning.rule_controls"
GROUP_LEARNING_RULE_CONTROLS_VERSION = 1
MAX_SESSION_RULE_CONTROLS = 2000


def _canonical_group(value: object) -> str:
    identity = parse_canonical_chat_stream_id(str(value or "").strip())
    if identity.chat_type != "group":
        raise ValueError("规则启停只接受 canonical group chat_stream_id")
    return identity.chat_stream_id


def _normalize_rule_ids(
    values: object,
    *,
    field_name: str,
) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{field_name} 必须是规则 ID 数组")
    normalized = tuple(str(item or "").strip() for item in values)
    if any(not item for item in normalized):
        raise ValueError(f"{field_name} 不能包含空规则 ID")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} 不能包含重复规则 ID")
    unknown = set(normalized) - set(
        LEARNING_SIGNAL_RULE_REGISTRY.ordered_ids
    )
    if unknown:
        raise ValueError(f"{field_name} 包含未登记规则")
    order = {
        rule_id: index
        for index, rule_id in enumerate(
            LEARNING_SIGNAL_RULE_REGISTRY.ordered_ids
        )
    }
    return tuple(sorted(normalized, key=order.__getitem__))


@dataclass(frozen=True, slots=True)
class GroupLearningRuleControls:
    """代码所有规则 Registry 的持久化启停投影。"""

    global_disabled: tuple[str, ...] = ()
    session_disabled: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    version: int = GROUP_LEARNING_RULE_CONTROLS_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != (
            GROUP_LEARNING_RULE_CONTROLS_VERSION
        ):
            raise ValueError("群学习规则启停配置版本不受支持")
        global_disabled = _normalize_rule_ids(
            self.global_disabled,
            field_name="global_disabled",
        )
        if not isinstance(self.session_disabled, Mapping):
            raise ValueError("session_disabled 必须是对象")
        if len(self.session_disabled) > MAX_SESSION_RULE_CONTROLS:
            raise ValueError("session_disabled 会话数量超限")
        sessions: dict[str, tuple[str, ...]] = {}
        for raw_session_id, raw_rule_ids in self.session_disabled.items():
            chat_stream_id = _canonical_group(raw_session_id)
            if chat_stream_id in sessions:
                raise ValueError("session_disabled canonical 会话重复")
            rule_ids = _normalize_rule_ids(
                raw_rule_ids,
                field_name=f"session_disabled[{chat_stream_id}]",
            )
            if rule_ids:
                sessions[chat_stream_id] = rule_ids
        object.__setattr__(self, "global_disabled", global_disabled)
        object.__setattr__(
            self,
            "session_disabled",
            MappingProxyType(dict(sorted(sessions.items()))),
        )

    @classmethod
    def from_json(cls, raw: object) -> "GroupLearningRuleControls":
        text = str(raw or "").strip()
        if not text:
            return cls()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("群学习规则启停配置不是合法 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("群学习规则启停配置必须是对象")
        allowed = {"version", "global_disabled", "session_disabled"}
        if set(payload) - allowed:
            raise ValueError("群学习规则启停配置包含未知字段")
        return cls(
            version=payload.get(
                "version",
                GROUP_LEARNING_RULE_CONTROLS_VERSION,
            ),
            global_disabled=tuple(payload.get("global_disabled", ())),
            session_disabled=payload.get("session_disabled", {}),
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "global_disabled": list(self.global_disabled),
                "session_disabled": {
                    chat_stream_id: list(rule_ids)
                    for chat_stream_id, rule_ids in (
                        self.session_disabled.items()
                    )
                },
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def disabled_rule_ids(
        self,
        chat_stream_id: str,
    ) -> tuple[str, ...]:
        canonical_id = _canonical_group(chat_stream_id)
        disabled = set(self.global_disabled)
        disabled.update(self.session_disabled.get(canonical_id, ()))
        return tuple(
            rule_id
            for rule_id in LEARNING_SIGNAL_RULE_REGISTRY.ordered_ids
            if rule_id in disabled
        )

    def enabled_rule_ids(
        self,
        chat_stream_id: str,
    ) -> tuple[str, ...]:
        disabled = set(self.disabled_rule_ids(chat_stream_id))
        return tuple(
            rule_id
            for rule_id in LEARNING_SIGNAL_RULE_REGISTRY.ordered_ids
            if rule_id not in disabled
        )

    def with_rule_enabled(
        self,
        *,
        rule_id: str,
        enabled: bool,
        chat_stream_id: str = "",
    ) -> "GroupLearningRuleControls":
        normalized_rule_id = _normalize_rule_ids(
            (rule_id,),
            field_name="rule_id",
        )[0]
        if chat_stream_id:
            canonical_id = _canonical_group(chat_stream_id)
            sessions = {
                key: tuple(value)
                for key, value in self.session_disabled.items()
            }
            disabled = set(sessions.get(canonical_id, ()))
            if enabled:
                disabled.discard(normalized_rule_id)
            else:
                disabled.add(normalized_rule_id)
            if disabled:
                sessions[canonical_id] = tuple(disabled)
            else:
                sessions.pop(canonical_id, None)
            return GroupLearningRuleControls(
                version=self.version,
                global_disabled=self.global_disabled,
                session_disabled=sessions,
            )
        global_disabled = set(self.global_disabled)
        if enabled:
            global_disabled.discard(normalized_rule_id)
        else:
            global_disabled.add(normalized_rule_id)
        return GroupLearningRuleControls(
            version=self.version,
            global_disabled=tuple(global_disabled),
            session_disabled=self.session_disabled,
        )


def load_group_learning_rule_controls(
    raw: object | None = None,
) -> GroupLearningRuleControls:
    """读取类型化规则控制；显式传入 ``raw`` 便于同事务管理。"""

    value = (
        settings.get_str(GROUP_LEARNING_RULE_CONTROLS_SETTING, "")
        if raw is None
        else raw
    )
    return GroupLearningRuleControls.from_json(value)


def effective_group_learning_rule_ids(
    chat_stream_id: str,
) -> tuple[str, ...]:
    """运行时配置损坏时抛错，阻止规则候选继续生成。"""

    return load_group_learning_rule_controls().enabled_rule_ids(
        chat_stream_id
    )


__all__ = [
    "GROUP_LEARNING_RULE_CONTROLS_SETTING",
    "GROUP_LEARNING_RULE_CONTROLS_VERSION",
    "GroupLearningRuleControls",
    "effective_group_learning_rule_ids",
    "load_group_learning_rule_controls",
]
