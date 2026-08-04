"""框架无关的应用工具执行结果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ToolServiceResult:
    """应用工具服务的最小结果，不暴露任何 Agent 框架类型。"""

    output: object = ""
    exit_code: int | None = None
    error: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.exit_code is not None and type(self.exit_code) is not int:
            raise ValueError("tool service exit_code 必须是整数或空")
        if self.error is not None:
            error = str(self.error).strip()
            if not error:
                raise ValueError("tool service error 不能为空字符串")
            object.__setattr__(self, "error", error)
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )

    @property
    def success(self) -> bool:
        return self.error is None and self.exit_code in {None, 0}


__all__ = ["ToolServiceResult"]
