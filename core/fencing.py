"""跨子域复用的租约与 fencing 值对象；领域状态机仍由各子域拥有。"""

from __future__ import annotations

import math
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


def positive_seconds(value: Any, *, field_name: str) -> float:
    """统一校验有限正秒数，拒绝 bool、NaN 与 Infinity。"""

    if type(value) not in {int, float}:
        raise TypeError(f"{field_name} 必须是数字")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{field_name} 必须是有限正数")
    return normalized


def lease_deadline(
    now: datetime,
    lease_seconds: int | float,
    *,
    field_name: str = "lease_seconds",
) -> datetime:
    if not isinstance(now, datetime):
        raise TypeError("now 必须是 datetime")
    return now + timedelta(
        seconds=positive_seconds(lease_seconds, field_name=field_name)
    )


def require_lease_exceeds_operation(
    *,
    operation_timeout_seconds: int | float,
    lease_seconds: int | float,
    operation_field_name: str = "operation_timeout_seconds",
    lease_field_name: str = "lease_seconds",
) -> tuple[float, float]:
    operation_timeout = positive_seconds(
        operation_timeout_seconds,
        field_name=operation_field_name,
    )
    lease = positive_seconds(lease_seconds, field_name=lease_field_name)
    if lease <= operation_timeout:
        raise ValueError(f"{lease_field_name} 必须严格大于操作超时")
    return operation_timeout, lease


def new_fencing_token(*, entropy_bytes: int = 32) -> str:
    if type(entropy_bytes) is not int or entropy_bytes < 16:
        raise ValueError("entropy_bytes 必须是不小于 16 的整数")
    return secrets.token_hex(entropy_bytes)


@dataclass(frozen=True, slots=True)
class FencingIdentity:
    owner: str
    token: str = field(repr=False)

    def __post_init__(self) -> None:
        owner = str(self.owner or "").strip()
        token = str(self.token or "").strip()
        if not owner or len(owner) > 128:
            raise ValueError("owner 必须是 1-128 字符")
        if not token or len(token) > 128:
            raise ValueError("token 必须是 1-128 字符")
        if any(ord(char) < 32 for char in owner + token):
            raise ValueError("owner/token 不能包含控制字符")
        object.__setattr__(self, "owner", owner)
        object.__setattr__(self, "token", token)

    def matches(self, *, owner: Any, token: Any) -> bool:
        return str(owner or "") == self.owner and secrets.compare_digest(
            str(token or ""),
            self.token,
        )


@dataclass(frozen=True, slots=True)
class LeaseFence:
    identity: FencingIdentity
    expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.expires_at, datetime):
            raise TypeError("expires_at 必须是 datetime")

    def is_active(self, *, now: datetime) -> bool:
        if not isinstance(now, datetime):
            raise TypeError("now 必须是 datetime")
        return self.expires_at > now

    def authorizes(self, *, owner: Any, token: Any, now: datetime) -> bool:
        return self.is_active(now=now) and self.identity.matches(
            owner=owner,
            token=token,
        )
