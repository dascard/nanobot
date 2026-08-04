"""只读 SQL 分析工具的应用服务。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from core.sql_readonly import validate_read_only_sql
from core.tool_contracts.result import ToolServiceResult


class ReadOnlyQuerySandbox(Protocol):
    def run_query(self, sql: str) -> str: ...


def execute_sql_analysis(
    sql: object,
    *,
    sandbox_factory: Callable[[], ReadOnlyQuerySandbox],
) -> ToolServiceResult:
    query = str(sql or "")
    if not query.strip():
        return ToolServiceResult(error="Missing 'sql' argument")

    ok, reason = validate_read_only_sql(query)
    if not ok:
        return ToolServiceResult(
            error=f"Invalid SQL for sql_analysis: {reason}"
        )

    output = sandbox_factory().run_query(query)
    if output.startswith("SQL Error:"):
        return ToolServiceResult(error=output)
    return ToolServiceResult(output=output, exit_code=0)


__all__ = ["ReadOnlyQuerySandbox", "execute_sql_analysis"]
