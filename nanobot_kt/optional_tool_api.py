"""KT 工具公开类型的可选导入边界。

Native Runtime 不通过这些类型执行工具；本模块只让 KT Adapter 在依赖未安装时
仍可被静态导入和测试。真正选择 KT Runtime 时仍会在启动阶段显式报错。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


try:
    from kohakuterrarium.modules.tool.base import (
        BaseTool as BaseTool,
        ExecutionMode as ExecutionMode,
        ToolConfig as ToolConfig,
        ToolResult as ToolResult,
    )
    KT_TOOL_API_AVAILABLE = True
except ModuleNotFoundError as exc:
    if not str(exc.name or "").startswith("kohakuterrarium"):
        raise
    KT_TOOL_API_AVAILABLE = False

    class ExecutionMode(Enum):
        DIRECT = "direct"
        BACKGROUND = "background"
        STATEFUL = "stateful"

    @dataclass
    class ToolConfig:
        timeout: float = 60.0
        max_output: int = 64 * 1024
        working_dir: str | None = None
        env: dict[str, str] = field(default_factory=dict)
        notify_controller_on_background_complete: bool = True
        extra: dict[str, Any] = field(default_factory=dict)

    @dataclass
    class ToolResult:
        output: object = ""
        exit_code: int | None = None
        error: str | None = None
        metadata: dict[str, Any] = field(default_factory=dict)

        @property
        def success(self) -> bool:
            return self.error is None and self.exit_code in {None, 0}

        def get_text_output(self) -> str:
            if isinstance(self.output, str):
                return self.output
            if isinstance(self.output, list):
                return "\n".join(
                    str(getattr(part, "text", ""))
                    for part in self.output
                    if getattr(part, "type", None) == "text"
                )
            return str(self.output or "")

    class BaseTool:
        needs_context = False
        supports_background = False
        is_concurrency_safe = True

        def __init__(self, config: ToolConfig | None = None, **_: Any) -> None:
            self.config = config or ToolConfig()

        async def execute(
            self,
            args: dict[str, Any],
            context: object | None = None,
        ) -> ToolResult:
            return await self._execute(args, context=context)


__all__ = [
    "BaseTool",
    "ExecutionMode",
    "KT_TOOL_API_AVAILABLE",
    "ToolConfig",
    "ToolResult",
]
