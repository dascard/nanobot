"""Nanobot 本地只读 SQL 分析入口。

任意 Python 执行在没有 OS 级隔离前保持硬禁用。数据库查询使用只读 URI、
query_only 和 SQLite authorizer 三层约束。
"""

from __future__ import annotations

import logging
import os
import sqlite3
from urllib.parse import quote

try:
    import pandas as pd
except ImportError:
    pd = None

from config import DATABASE_URL
from core.sql_readonly import build_read_only_authorizer, validate_read_only_sql


logger = logging.getLogger("nanobot.sandbox")

PYTHON_ANALYSIS_DISABLED_MESSAGE = (
    "Python analysis is disabled until an OS-isolated sandbox is available."
)


class AnalysisSandbox:
    def __init__(self, db_path: str = ""):
        raw = db_path or DATABASE_URL
        normalized = raw.replace("sqlite:///", "")
        self.db_path = ":memory:" if normalized == ":memory:" else os.path.abspath(normalized)

    def _connect_read_only(self) -> sqlite3.Connection:
        if self.db_path == ":memory:":
            connection = sqlite3.connect(":memory:")
        else:
            encoded_path = quote(self.db_path, safe="/:")
            connection = sqlite3.connect(
                f"file:{encoded_path}?mode=ro",
                uri=True,
            )
        connection.execute("PRAGMA query_only = ON")
        connection.set_authorizer(build_read_only_authorizer())
        return connection

    def _redact_database_path(self, message: object) -> str:
        text = str(message or "")
        for value in {self.db_path, os.path.realpath(self.db_path)}:
            if value:
                text = text.replace(value, "[database]")
        return text

    def run_query(self, sql: str) -> str:
        """执行单条、有界的只读 SQLite 查询并返回 Markdown 表格。"""
        allowed, reason = validate_read_only_sql(sql)
        if not allowed:
            return f"SQL Error: {reason}"

        try:
            with self._connect_read_only() as connection:
                if pd is not None:
                    frame = pd.read_sql_query(sql, connection)
                    result = frame.to_markdown()
                else:
                    cursor = connection.execute(sql)
                    columns = [item[0] for item in cursor.description] if cursor.description else []
                    rows = cursor.fetchall()
                    if not columns:
                        result = "(no results)"
                    else:
                        header = " | ".join(columns)
                        separator = " | ".join(["---"] * len(columns))
                        body = "\n".join(
                            " | ".join(str(value) for value in row)
                            for row in rows
                        )
                        result = f"{header}\n{separator}\n{body}"
                return result
        except Exception as exc:
            return f"SQL Error: {self._redact_database_path(exc)}"

    def execute_python_analysis(self, code: str) -> str:
        """Fail closed：当前不执行任何不可信 Python 代码。"""
        del code
        logger.warning("[PythonSandbox] blocked disabled Python analysis request")
        return PYTHON_ANALYSIS_DISABLED_MESSAGE
