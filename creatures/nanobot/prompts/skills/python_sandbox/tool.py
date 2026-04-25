"""
Python Sandbox tool — KohakuTerrarium BaseTool adapter.

Wraps AnalysisSandbox.execute_python_analysis() for safe code execution.
"""

from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

from sandbox import AnalysisSandbox


class PythonSandboxTool(BaseTool):
    """Execute Python analysis scripts in a security-restricted sandbox."""

    _sandbox: AnalysisSandbox | None = None

    @property
    def tool_name(self) -> str:
        return "python_sandbox"

    @property
    def description(self) -> str:
        return (
            "在安全沙箱中执行 Python 数据分析脚本。"
            "用于 SQL 难以表达的统计/清洗/聚合逻辑（如分位数、复杂分桶、规则匹配），"
            "不是通用编程执行环境。"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def _get_sandbox(self) -> AnalysisSandbox:
        if self._sandbox is None:
            self._sandbox = AnalysisSandbox()
        return self._sandbox

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        code = args.get("code", "")
        if not code.strip():
            return ToolResult(error="Missing 'code' argument")
        result = self._get_sandbox().execute_python_analysis(code)
        return ToolResult(output=result, exit_code=0)
