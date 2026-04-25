"""
SQL Analysis tool — KohakuTerrarium BaseTool adapter.

Wraps the existing AnalysisSandbox.run_query() for use within KT's controller.
"""

from typing import Any
import re

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

from sandbox import AnalysisSandbox


class SQLAnalysisTool(BaseTool):
    """Execute read-only SQL queries against the nanobot chat log database."""

    _sandbox: AnalysisSandbox | None = None

    @property
    def tool_name(self) -> str:
        return "sql_analysis"

    @property
    def description(self) -> str:
        return "在对话日志 SQLite 库中执行只读 SQL 查询进行数据分析"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "要执行的只读 SQL 查询语句",
                }
            },
            "required": ["sql"],
        }

    def _get_sandbox(self) -> AnalysisSandbox:
        if self._sandbox is None:
            self._sandbox = AnalysisSandbox()
        return self._sandbox

    @staticmethod
    def _strip_sql_comments(sql: str) -> str:
        # Remove -- line comments and /* */ block comments for safer keyword checks.
        without_line = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
        return re.sub(r"/\*.*?\*/", "", without_line, flags=re.DOTALL)

    @classmethod
    def _validate_read_only_sql(cls, sql: str) -> tuple[bool, str]:
        normalized = cls._strip_sql_comments(sql).strip()
        if not normalized:
            return False, "SQL is empty"

        # Block stacked statements and obvious mutation keywords.
        if ";" in normalized.rstrip(";"):
            return False, "Only a single SQL statement is allowed"

        lowered = normalized.lower()
        forbidden = [
            "insert", "update", "delete", "drop", "alter", "create", "attach",
            "detach", "replace", "truncate", "grant", "revoke", "vacuum", "reindex",
            "pragma", "begin", "commit", "rollback",
        ]
        if any(re.search(rf"\b{kw}\b", lowered) for kw in forbidden):
            return False, "Only read-only SELECT/CTE queries are permitted"

        # Allow SELECT and WITH ... SELECT
        if re.match(r"^(select|with)\b", lowered) is None:
            return False, "Query must start with SELECT or WITH"

        return True, ""

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        sql = args.get("sql", "")
        if not sql.strip():
            return ToolResult(error="Missing 'sql' argument")

        ok, reason = self._validate_read_only_sql(sql)
        if not ok:
            return ToolResult(error=f"Invalid SQL for sql_analysis: {reason}")

        result = self._get_sandbox().run_query(sql)
        return ToolResult(output=result, exit_code=0)
