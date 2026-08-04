"""Python 分析工具的应用服务；通用代码执行保持硬禁用。"""

from __future__ import annotations

from sandbox import PYTHON_ANALYSIS_DISABLED_MESSAGE

from core.tool_contracts.result import ToolServiceResult


def execute_python_sandbox(code: object) -> ToolServiceResult:
    if not str(code or "").strip():
        return ToolServiceResult(error="Missing 'code' argument")
    return ToolServiceResult(error=PYTHON_ANALYSIS_DISABLED_MESSAGE)


__all__ = ["execute_python_sandbox"]
