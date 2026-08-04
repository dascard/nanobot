"""Agent Manifest 值对象的共享校验。"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
import re
from typing import TypeVar


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_VERSION_PATTERN = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_MEDIA_TYPE_PATTERN = re.compile(
    r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$"
)
_INLINE_SECRET_PATTERNS = (
    re.compile(r"^sk-[A-Za-z0-9_-]{16,}$"),
    re.compile(r"^(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{16,}$"),
    re.compile(r"^AIza[0-9A-Za-z_-]{20,}$"),
    re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$"),
    re.compile(r"^Bearer\s+\S+$", re.IGNORECASE),
)

EnumT = TypeVar("EnumT", bound=Enum)


def required_text(value: object, name: str, *, max_length: int = 1024) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} 不能为空")
    if len(normalized) > max_length:
        raise ValueError(f"{name} 长度不能超过 {max_length}")
    if any(ord(char) < 32 and char not in {"\t", "\n"} for char in normalized):
        raise ValueError(f"{name} 包含控制字符")
    return normalized

def identifier(value: object, name: str) -> str:
    normalized = required_text(value, name, max_length=128)
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError(f"{name} 不是合法标识符：{normalized!r}")
    if "//" in normalized or "/../" in f"/{normalized}/":
        raise ValueError(f"{name} 不是规范标识符：{normalized!r}")
    return normalized


def version(value: object, name: str = "version") -> str:
    normalized = required_text(value, name, max_length=128)
    if not _VERSION_PATTERN.fullmatch(normalized):
        raise ValueError(f"{name} 必须是 SemVer")
    return normalized


def sha256(value: object, name: str, *, allow_empty: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized and allow_empty:
        return ""
    if len(normalized) != 64 or any(
        char not in "0123456789abcdef" for char in normalized
    ):
        raise ValueError(f"{name} 必须是 64 位十六进制摘要")
    return normalized


def media_type(value: object, name: str = "media_type") -> str:
    normalized = required_text(value, name, max_length=128).lower()
    if not _MEDIA_TYPE_PATTERN.fullmatch(normalized):
        raise ValueError(f"{name} 无效")
    return normalized


def enum_value(value: object, enum_type: type[EnumT], name: str) -> EnumT:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        raise ValueError(f"{name} 无效") from exc


def unique_identifiers(
    values: Iterable[object],
    name: str,
    *,
    require_one: bool = False,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{name} 必须是标识符集合")
    normalized = tuple(sorted(identifier(item, name) for item in values))
    if require_one and not normalized:
        raise ValueError(f"{name} 至少需要一项")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{name} 不能重复")
    return normalized


def reject_inline_secret(value: object, name: str) -> str:
    normalized = identifier(value, name)
    if any(pattern.fullmatch(normalized) for pattern in _INLINE_SECRET_PATTERNS):
        raise ValueError(f"{name} 疑似凭据明文；Manifest 只允许秘密引用 ID")
    return normalized
