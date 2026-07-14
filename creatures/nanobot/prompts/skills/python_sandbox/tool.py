"""
Python Sandbox tool — KohakuTerrarium BaseTool adapter.

Wraps AnalysisSandbox.execute_python_analysis() for safe code execution.
"""

from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

from sandbox import AnalysisSandbox, PYTHON_ANALYSIS_DISABLED_MESSAGE


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
            "不是通用编程执行环境，也不是简单聊天记录查询的首选工具。"
            "查询上一句、历史发言、表结构或简单 SELECT 时先使用 sql_analysis；"
            "只有需要对 SQL 结果继续做复杂计算时才使用本工具。"
            "沙箱内预置了数据库连接变量：_conn（只读 sqlite3.Connection，已连接到 nanobot.db）和 _db_path（数据库文件路径）。"
            "可直接使用 _conn.execute('SELECT ...').fetchall() 查询数据，也可通过 pd.read_sql_query(sql, _conn) 获取 DataFrame。"
            "沙箱禁止了 open/os/sys 等模块，不要尝试文件操作。"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "要执行的 Python 数据分析脚本。仅用于复杂统计/清洗/聚合；"
                        "简单聊天记录查询、上一句、表结构检查请改用 sql_analysis。"
                        "如需读库，使用预置只读 _conn，并给 SQL 加 LIMIT。最后用 print() 输出结论。"
                    ),
                }
            },
            "required": ["code"],
        }

    def _get_sandbox(self) -> AnalysisSandbox:
        if self._sandbox is None:
            self._sandbox = AnalysisSandbox()
        return self._sandbox

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        code = args.get("code", "")
        if not code.strip():
            return ToolResult(error="Missing 'code' argument")
        return ToolResult(error=PYTHON_ANALYSIS_DISABLED_MESSAGE)
