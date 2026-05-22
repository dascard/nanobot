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
    _DB_GUIDE = (
        "可查询 SQLite 表："
        "chat_logs 原始消息档案(id,user_id=发件人QQ,session_id=private_用户ID/group_群号,"
        "sender_name,session_name,role=user/assistant/ambient/tool/model,content,created_at,message_id,meta_json)；"
        "conversation_turns 精简对话上下文(id,user_id,session_id,role=user/assistant,content,created_at,meta_json)；"
        "users 用户/群聊(id,name,history_clear_at,created_at)；"
        "personas 用户画像(user_id,persona_json,updated_at)。"
        "查“上一句/刚才说过什么/聊天记录/某用户历史发言”时优先查 chat_logs 或 conversation_turns，"
        "不要用 memory_read、read、grep 去找聊天数据库。"
        "常用示例：SELECT id, created_at, content FROM chat_logs "
        "WHERE session_id='private_0000000000' AND role='user' "
        "ORDER BY id DESC LIMIT 5；"
        "SELECT id, created_at, sender_name, content FROM chat_logs "
        "WHERE session_id='group_123456' AND role IN ('user','ambient') "
        "ORDER BY id DESC LIMIT 20；"
        "PRAGMA table_info(chat_logs)。"
        "如果查询被拒绝为缺 LIMIT 或 SELECT *，直接修正同一查询后重试，不要改用 memory_read/read/grep。"
    )
    _SQL_RULES = (
        "必须是单条 SELECT/CTE 或只读 PRAGMA；"
        "SELECT/WITH 必须包含 LIMIT；禁止 SELECT *；"
        "普通查询≤1000，聚合查询(COUNT/GROUP BY)≤5000，"
        "原文内容字段(content/message/text/html)≤500。"
    )

    @property
    def tool_name(self) -> str:
        return "sql_analysis"

    @property
    def description(self) -> str:
        return ("只读 SQL 分析工具，用于用户明确要求查询数据库、审计数据、检查表结构或调试 SQL 时使用。"
                "也用于查询聊天记录、上一句、刚才说过什么、历史发言、会话日志。"
                "不要将本工具作为业务工具的前置步骤。"
                "如果用户要分析群聊、生成群日报、总结某个群的消息，应直接调用 group_analysis，"
                "不要先用 SQL 查询群号、User 表或 ChatLog。"
                f"{self._DB_GUIDE}")

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": (
                        "要执行的只读 SQL 查询语句。"
                        f"{self._SQL_RULES}"
                        "表结构、查询示例和工具边界见 function.description，避免在参数说明中重复整段 schema。"
                    ),
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

    # ── 分级 LIMIT ──
    DEFAULT_MAX_SQL_LIMIT = 1000
    AGGREGATE_MAX_SQL_LIMIT = 5000
    RAW_CONTENT_MAX_SQL_LIMIT = 500

    _RAW_CONTENT_COLS = {"content", "message", "text", "body", "prompt", "response", "html"}

    @classmethod
    def _is_aggregate_query(cls, sql: str) -> bool:
        lowered = sql.lower()
        return (
            " group by " in lowered
            or any(re.search(rf"\b{fn}\s*\(", lowered)
                   for fn in ["count", "sum", "avg", "min", "max"])
        )

    @classmethod
    def _selects_raw_content(cls, sql: str) -> bool:
        lowered = sql.lower()
        return any(re.search(rf"\b{name}\b", lowered) for name in cls._RAW_CONTENT_COLS)

    @classmethod
    def _limit_cap_for_query(cls, sql: str) -> int:
        # 聚合查询优先——COUNT(content) 之类不会被误伤为原文查询
        if cls._is_aggregate_query(sql):
            return cls.AGGREGATE_MAX_SQL_LIMIT
        if cls._selects_raw_content(sql):
            return cls.RAW_CONTENT_MAX_SQL_LIMIT
        return cls.DEFAULT_MAX_SQL_LIMIT

    @classmethod
    def _validate_read_only_sql(cls, sql: str) -> tuple[bool, str]:
        normalized = cls._strip_sql_comments(sql).strip()
        if not normalized:
            return False, "SQL is empty"

        if ";" in normalized.rstrip(";"):
            return False, "Only a single SQL statement is allowed"

        lowered = normalized.lower()
        forbidden = [
            "insert", "update", "delete", "drop", "alter", "create", "attach",
            "detach", "replace", "truncate", "grant", "revoke", "vacuum", "reindex",
            "begin", "commit", "rollback",
        ]
        _readonly_pragmas = {
            "table_info", "table_xinfo", "index_list", "index_info",
            "foreign_key_list", "foreign_keys", "compile_options",
            "database_list", "collation_list", "function_list",
            "schema_version", "application_id", "user_version",
        }
        m = re.match(r"^pragma\s+(\w+)", lowered)
        is_readonly_pragma = m is not None and m.group(1) in _readonly_pragmas

        if is_readonly_pragma and "=" in lowered:
            return False, "PRAGMA assignment is not allowed"

        if not is_readonly_pragma and any(re.search(rf"\b{kw}\b", lowered) for kw in forbidden):
            return False, "Only read-only SELECT/CTE queries are permitted"

        if is_readonly_pragma:
            return True, ""

        if re.match(r"^(select|with)\b", lowered) is None:
            return False, "Only SELECT or CTE (WITH) queries are allowed"

        # 禁止 SELECT *
        if re.search(r"\bselect\s+\*", lowered):
            return False, "SELECT * is not allowed; select specific columns"

        # 分级 LIMIT
        m_limit = re.search(r"\blimit\s+(\d+)\b", lowered)
        if not m_limit:
            return False, "SELECT/CTE queries must include LIMIT"

        cap = cls._limit_cap_for_query(lowered)
        if int(m_limit.group(1)) > cap:
            return False, f"LIMIT must be <= {cap} for this query type"

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
