"""Native／KT 双 Runtime 的确定性选择策略。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum


class AgentRuntimeKind(StrEnum):
    NATIVE = "native"
    KT = "kt"


def parse_runtime_scope_ids(value: object) -> frozenset[str]:
    """解析受信启动配置中的精确会话 ID，不接受通配符。"""

    if isinstance(value, (set, frozenset, list, tuple)):
        raw_items = value
    else:
        raw_items = str(value or "").replace("，", ",").replace("\n", ",").split(",")
    normalized = frozenset(
        str(item or "").strip() for item in raw_items if str(item or "").strip()
    )
    if any("*" in item or "?" in item for item in normalized):
        raise ValueError("Runtime 灰度会话只允许精确 ID，不能使用通配符")
    return normalized


@dataclass(frozen=True, slots=True)
class AgentRuntimeSelection:
    kind: AgentRuntimeKind
    reason: str
    scope_sha256: str
    bucket: int
    policy_sha256: str


@dataclass(frozen=True, slots=True)
class AgentRuntimeSelectionPolicy:
    """启动期冻结的 Runtime 灰度策略。

    每个 canonical session 始终映射到同一 bucket。策略只返回一个 Runtime，
    不包含异常后的 fallback，因此调用方不能借选择器跨 Runtime 重放 Turn。
    """

    default_kind: AgentRuntimeKind = AgentRuntimeKind.NATIVE
    kt_enabled: bool = False
    kt_session_ids: frozenset[str] = field(default_factory=frozenset)
    kt_percentage_basis_points: int = 0
    _policy_sha256: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            default_kind = AgentRuntimeKind(self.default_kind)
        except ValueError as exc:
            raise ValueError("default_kind 必须是 native 或 kt") from exc
        if not isinstance(self.kt_enabled, bool):
            raise ValueError("kt_enabled 必须是 bool")
        basis_points = self.kt_percentage_basis_points
        if type(basis_points) is not int or not 0 <= basis_points <= 10_000:
            raise ValueError("kt_percentage_basis_points 必须在 0..10000")
        session_ids = parse_runtime_scope_ids(self.kt_session_ids)
        if not self.kt_enabled and (
            default_kind is AgentRuntimeKind.KT or basis_points > 0 or session_ids
        ):
            raise ValueError("KT 未启用时不能配置 KT 默认值或灰度范围")

        canonical = {
            "default_kind": default_kind.value,
            "kt_enabled": self.kt_enabled,
            "kt_percentage_basis_points": basis_points,
            "kt_session_ids": sorted(session_ids),
        }
        encoded = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        object.__setattr__(self, "default_kind", default_kind)
        object.__setattr__(self, "kt_session_ids", session_ids)
        object.__setattr__(
            self,
            "_policy_sha256",
            hashlib.sha256(encoded).hexdigest(),
        )

    @property
    def policy_sha256(self) -> str:
        return self._policy_sha256

    def select(
        self,
        *,
        session_id: str,
        user_id: str = "",
    ) -> AgentRuntimeSelection:
        canonical_session = str(session_id or "").strip()
        canonical_user = str(user_id or "").strip()
        if canonical_session:
            scope_key = f"session:{canonical_session}"
        elif canonical_user:
            scope_key = f"user:{canonical_user}"
        else:
            raise ValueError("Runtime 选择至少需要 session_id 或 user_id")

        digest = hashlib.sha256(scope_key.encode("utf-8")).hexdigest()
        bucket = int(digest[:16], 16) % 10_000
        if (
            self.kt_enabled
            and canonical_session
            and canonical_session in self.kt_session_ids
        ):
            kind = AgentRuntimeKind.KT
            reason = "kt_session_allowlist"
        elif (
            self.kt_enabled
            and self.kt_percentage_basis_points > 0
            and bucket < self.kt_percentage_basis_points
        ):
            kind = AgentRuntimeKind.KT
            reason = "kt_percentage_rollout"
        else:
            kind = self.default_kind
            reason = "default"
        return AgentRuntimeSelection(
            kind=kind,
            reason=reason,
            scope_sha256=digest,
            bucket=bucket,
            policy_sha256=self.policy_sha256,
        )


__all__ = [
    "AgentRuntimeKind",
    "AgentRuntimeSelection",
    "AgentRuntimeSelectionPolicy",
    "parse_runtime_scope_ids",
]
