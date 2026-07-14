"""SQLite 只读查询的统一校验与运行时授权边界。"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable


DEFAULT_MAX_SQL_LIMIT = 1000
AGGREGATE_MAX_SQL_LIMIT = 5000
RAW_CONTENT_MAX_SQL_LIMIT = 500

SAFE_READ_ONLY_PRAGMAS = frozenset({
    "table_info",
    "table_xinfo",
    "index_list",
    "index_info",
    "foreign_key_list",
})

_RAW_CONTENT_COLUMNS = frozenset({
    "content",
    "message",
    "text",
    "body",
    "prompt",
    "response",
    "html",
})

_DANGEROUS_FUNCTIONS = frozenset({"load_extension", "readfile", "writefile"})


def strip_sql_comments(sql: str) -> str:
    """移除 SQL 注释，供保守的语句级校验使用。"""
    without_line = re.sub(r"--.*?$", "", str(sql or ""), flags=re.MULTILINE)
    return re.sub(r"/\*.*?\*/", "", without_line, flags=re.DOTALL)


def is_aggregate_query(sql: str) -> bool:
    lowered = str(sql or "").lower()
    return (
        " group by " in lowered
        or any(
            re.search(rf"\b{function_name}\s*\(", lowered)
            for function_name in ("count", "sum", "avg", "min", "max")
        )
    )


def selects_raw_content(sql: str) -> bool:
    lowered = str(sql or "").lower()
    return any(
        re.search(rf"\b{column_name}\b", lowered)
        for column_name in _RAW_CONTENT_COLUMNS
    )


def limit_cap_for_query(sql: str) -> int:
    if is_aggregate_query(sql):
        return AGGREGATE_MAX_SQL_LIMIT
    if selects_raw_content(sql):
        return RAW_CONTENT_MAX_SQL_LIMIT
    return DEFAULT_MAX_SQL_LIMIT


def _validate_pragma(normalized: str) -> tuple[bool, str] | None:
    match = re.fullmatch(
        r"pragma\s+(?:main\.)?([a-z_][a-z0-9_]*)"
        r"(?:\s*\([^;]*\))?\s*;?",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        if re.match(r"^pragma\b", normalized, flags=re.IGNORECASE):
            return False, "Only approved read-only PRAGMA queries are allowed"
        return None
    pragma_name = match.group(1).lower()
    if "=" in normalized or pragma_name not in SAFE_READ_ONLY_PRAGMAS:
        return False, "Only approved read-only PRAGMA queries are allowed"
    return True, ""


def validate_read_only_sql(sql: str) -> tuple[bool, str]:
    """验证单条、有界的只读 SELECT/CTE 或安全 PRAGMA。"""
    normalized = strip_sql_comments(sql).strip()
    if not normalized:
        return False, "SQL is empty"

    if ";" in normalized.rstrip(";"):
        return False, "Only a single SQL statement is allowed"

    pragma_result = _validate_pragma(normalized)
    if pragma_result is not None:
        return pragma_result

    lowered = normalized.lower()
    if re.match(r"^(select|with)\b", lowered) is None:
        return False, "Only SELECT or CTE (WITH) queries are allowed"

    if any(
        re.search(rf"\b{function_name}\s*\(", lowered)
        for function_name in _DANGEROUS_FUNCTIONS
    ):
        return False, "Dangerous SQLite functions are not allowed"

    if re.search(r"\bselect\s+(?:[a-z_][a-z0-9_]*\.)?\*", lowered):
        return False, "SELECT * is not allowed; select specific columns"

    outer_limit = re.search(
        r"\blimit\s+(\d+)"
        r"(?:\s+offset\s+\d+|\s*,\s*\d+)?\s*;?\s*$",
        lowered,
    )
    if not outer_limit:
        return False, "SELECT/CTE queries must end with a numeric LIMIT"

    cap = limit_cap_for_query(lowered)
    if int(outer_limit.group(1)) > cap:
        return False, f"LIMIT must be <= {cap} for this query type"

    for value in re.findall(r"\blimit\s+(\d+)\b", lowered):
        if int(value) > cap:
            return False, f"LIMIT must be <= {cap} for this query type"

    return True, ""


def _sqlite_action_codes(*names: str) -> frozenset[int]:
    return frozenset(
        int(value)
        for name in names
        if isinstance((value := getattr(sqlite3, name, None)), int)
    )


_DENIED_ACTIONS = _sqlite_action_codes(
    "SQLITE_INSERT",
    "SQLITE_UPDATE",
    "SQLITE_DELETE",
    "SQLITE_CREATE_INDEX",
    "SQLITE_CREATE_TABLE",
    "SQLITE_CREATE_TEMP_INDEX",
    "SQLITE_CREATE_TEMP_TABLE",
    "SQLITE_CREATE_TEMP_TRIGGER",
    "SQLITE_CREATE_TEMP_VIEW",
    "SQLITE_CREATE_TRIGGER",
    "SQLITE_CREATE_VIEW",
    "SQLITE_DROP_INDEX",
    "SQLITE_DROP_TABLE",
    "SQLITE_DROP_TEMP_INDEX",
    "SQLITE_DROP_TEMP_TABLE",
    "SQLITE_DROP_TEMP_TRIGGER",
    "SQLITE_DROP_TEMP_VIEW",
    "SQLITE_DROP_TRIGGER",
    "SQLITE_DROP_VIEW",
    "SQLITE_ALTER_TABLE",
    "SQLITE_REINDEX",
    "SQLITE_ANALYZE",
    "SQLITE_ATTACH",
    "SQLITE_DETACH",
    "SQLITE_TRANSACTION",
    "SQLITE_SAVEPOINT",
)


def build_read_only_authorizer() -> Callable[[int, str | None, str | None, str | None, str | None], int]:
    """构造 SQLite authorizer，作为文本校验之外的第二道只读防线。"""

    def authorize(
        action_code: int,
        first: str | None,
        second: str | None,
        _database_name: str | None,
        _trigger_name: str | None,
    ) -> int:
        if action_code in _DENIED_ACTIONS:
            return sqlite3.SQLITE_DENY

        if action_code == getattr(sqlite3, "SQLITE_PRAGMA", -1):
            pragma_name = str(first or "").strip().lower()
            if pragma_name not in SAFE_READ_ONLY_PRAGMAS:
                return sqlite3.SQLITE_DENY

        if action_code == getattr(sqlite3, "SQLITE_FUNCTION", -1):
            function_name = str(second or first or "").strip().lower()
            if function_name in _DANGEROUS_FUNCTIONS:
                return sqlite3.SQLITE_DENY

        return sqlite3.SQLITE_OK

    return authorize
