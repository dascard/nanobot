"""Sandbox 按 canonical session 授权的稳定契约。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from core.chat_stream_identity import ChatStreamIdentity


class SandboxCapability(IntEnum):
    """能力等级只允许单向包含，避免 Sandbox 工具各自维护授权列表。"""

    OFF = 0
    WORKSPACE = 1
    ASSETS = 2
    EXEC = 3

    @classmethod
    def parse(cls, value: object) -> "SandboxCapability":
        normalized = str(value or "off").strip().lower()
        mapping = {
            "off": cls.OFF,
            "workspace": cls.WORKSPACE,
            "assets": cls.ASSETS,
            "exec": cls.EXEC,
        }
        try:
            return mapping[normalized]
        except KeyError as exc:
            raise ValueError("Sandbox capability 无效") from exc

    @property
    def value_name(self) -> str:
        return {
            self.OFF: "off",
            self.WORKSPACE: "workspace",
            self.ASSETS: "assets",
            self.EXEC: "exec",
        }[self]


TOOL_REQUIRED_CAPABILITY: dict[str, SandboxCapability] = {
    "workspace_list": SandboxCapability.WORKSPACE,
    "workspace_read": SandboxCapability.WORKSPACE,
    "workspace_search": SandboxCapability.WORKSPACE,
    "workspace_write": SandboxCapability.WORKSPACE,
    "workspace_edit": SandboxCapability.WORKSPACE,
    "workspace_apply_patch": SandboxCapability.WORKSPACE,
    "asset_import": SandboxCapability.ASSETS,
    "asset_publish": SandboxCapability.ASSETS,
    "sandbox_exec": SandboxCapability.EXEC,
    "sandbox_poll": SandboxCapability.EXEC,
    "sandbox_write_stdin": SandboxCapability.EXEC,
    "sandbox_terminate": SandboxCapability.EXEC,
}

LEASE_PROCESS_TOOL_NAMES = frozenset({
    "sandbox_poll",
    "sandbox_write_stdin",
    "sandbox_terminate",
})


@dataclass(frozen=True, slots=True)
class SandboxAccessDecision:
    """ToolPlan 与执行入口共用的纯授权结论。"""

    allowed: bool
    code: str
    reason: str
    required_capability: SandboxCapability
    granted_capability: SandboxCapability = SandboxCapability.OFF
    identity: ChatStreamIdentity | None = None
    grant_id: str = ""
    workspace_id: str = ""
    quota_bytes: int = 0
    execution_profile: str = "restricted"
    grant_version: int = 0
    quota_generation: int = 0


__all__ = [
    "SandboxAccessDecision",
    "SandboxCapability",
    "LEASE_PROCESS_TOOL_NAMES",
    "TOOL_REQUIRED_CAPABILITY",
]
