"""跨入口、业务模块和 Adapter 共用的身份值对象。"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


_PLATFORM_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_OWNER_TYPES = frozenset({"user", "group", "project", "system"})
_RECIPIENT_TYPES = frozenset({"user", "group", "project", "system"})


class ChatStreamIdentityError(ValueError):
    """会话身份无法无歧义规范化。"""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _normalize_platform_value(value: object) -> str:
    platform = str(value or "").strip().lower()
    if not _PLATFORM_RE.fullmatch(platform):
        raise ChatStreamIdentityError(
            "invalid_platform",
            "platform 必须是合法的小写平台标识",
        )
    return platform


def _validate_identity_value(
    value: object,
    *,
    field_name: str,
    empty_code: str = "invalid_identity",
    max_chars: int = 512,
) -> str:
    normalized = str(value or "")
    if not normalized:
        raise ChatStreamIdentityError(
            empty_code,
            f"{field_name} 不能为空",
        )
    if len(normalized) > max_chars:
        raise ChatStreamIdentityError(
            "identity_too_long",
            f"{field_name} 长度超出限制",
        )
    if any(
        unicodedata.category(char) in {"Cc", "Cs"}
        for char in normalized
    ):
        raise ChatStreamIdentityError(
            f"invalid_{field_name}",
            f"{field_name} 不能包含控制字符或代理码点",
        )
    return normalized


@dataclass(frozen=True, slots=True)
class PlatformId:
    value: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "value",
            _normalize_platform_value(self.value),
        )

    @classmethod
    def parse(cls, value: object) -> "PlatformId":
        return cls(str(value or ""))

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ExternalSessionId:
    value: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "value",
            _validate_identity_value(
                self.value,
                field_name="external_session_id",
                empty_code="invalid_external_session_id",
            ),
        )

    @classmethod
    def parse(cls, value: object) -> "ExternalSessionId":
        return cls(str(value or ""))

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ChatStreamIdentity:
    """已校验且可稳定序列化的会话身份。"""

    platform: str
    chat_type: str
    external_session_id: str
    encoded_external_session_id: str
    chat_stream_id: str

    @property
    def platform_id(self) -> PlatformId:
        return PlatformId(self.platform)

    @property
    def external_id(self) -> ExternalSessionId:
        return ExternalSessionId(self.external_session_id)

    @property
    def legacy_runtime_session_id(self) -> str:
        if self.chat_type == "group":
            return f"group_{self.external_session_id}"
        return self.external_session_id

    @property
    def explicit_legacy_alias(self) -> str:
        return f"{self.chat_type}_{self.external_session_id}"


def _coerce_platform(value: PlatformId | str) -> PlatformId:
    return value if isinstance(value, PlatformId) else PlatformId.parse(value)


@dataclass(frozen=True, slots=True)
class Principal:
    platform: PlatformId | str
    owner_type: str
    owner_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "platform",
            _coerce_platform(self.platform).value,
        )
        if self.owner_type not in _OWNER_TYPES:
            raise ValueError("owner_type 无效")
        object.__setattr__(
            self,
            "owner_id",
            _validate_identity_value(
                self.owner_id,
                field_name="owner_id",
            ),
        )

    @property
    def canonical_id(self) -> str:
        return f"{self.platform}:{self.owner_type}:{self.owner_id}"

    @property
    def platform_id(self) -> PlatformId:
        return PlatformId(self.platform)


@dataclass(frozen=True, slots=True)
class ActorIdentity:
    platform: PlatformId | str
    actor_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "platform",
            _coerce_platform(self.platform),
        )
        object.__setattr__(
            self,
            "actor_id",
            _validate_identity_value(
                self.actor_id,
                field_name="actor_id",
            ),
        )

    @property
    def canonical_id(self) -> str:
        return f"{self.platform.value}:actor:{self.actor_id}"


@dataclass(frozen=True, slots=True)
class RecipientIdentity:
    platform: PlatformId | str
    recipient_type: str
    recipient_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "platform",
            _coerce_platform(self.platform),
        )
        if self.recipient_type not in _RECIPIENT_TYPES:
            raise ValueError("recipient_type 无效")
        object.__setattr__(
            self,
            "recipient_id",
            _validate_identity_value(
                self.recipient_id,
                field_name="recipient_id",
            ),
        )

    @property
    def canonical_id(self) -> str:
        return (
            f"{self.platform.value}:{self.recipient_type}:"
            f"{self.recipient_id}"
        )


__all__ = [
    "ActorIdentity",
    "ChatStreamIdentity",
    "ChatStreamIdentityError",
    "ExternalSessionId",
    "PlatformId",
    "Principal",
    "RecipientIdentity",
]
