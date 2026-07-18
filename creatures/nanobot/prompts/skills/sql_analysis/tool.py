"""
SQL Analysis tool — KohakuTerrarium BaseTool adapter.

Wraps the existing AnalysisSandbox.run_query() for use within KT's controller.
"""

from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

from sandbox import AnalysisSandbox
from core.sql_readonly import (
    AGGREGATE_MAX_SQL_LIMIT as SQL_AGGREGATE_MAX_LIMIT,
    DEFAULT_MAX_SQL_LIMIT as SQL_DEFAULT_MAX_LIMIT,
    RAW_CONTENT_MAX_SQL_LIMIT as SQL_RAW_CONTENT_MAX_LIMIT,
    is_aggregate_query,
    limit_cap_for_query,
    selects_raw_content,
    strip_sql_comments,
    validate_read_only_sql,
)


class SQLAnalysisTool(BaseTool):
    """Execute read-only SQL queries against the nanobot chat log database."""

    _sandbox: AnalysisSandbox | None = None
    _DB_GUIDE = (
        "可查询 SQLite 表："
        "chat_logs 原始消息档案(id,user_id=发件人QQ,session_id=private_用户ID/group_群号,"
        "sender_name,session_name,role=user/assistant/ambient/tool/model,content,created_at,message_id,meta_json)；"
        "conversation_turns 精简对话上下文(id,user_id,session_id,role=user/assistant,content,created_at,meta_json)；"
        "users 用户/群聊(id,name,history_clear_at,created_at)；"
        "personas 用户画像(user_id,persona_json,status,updated_at；仅 active 可消费)。"
        "私聊查“上一句/刚才说过什么/聊天记录/某用户历史发言”时优先查 chat_logs 或 conversation_turns。"
        "群聊现场消息主要是 role='ambient'；当前群消息在工具执行前通常已入库，"
        "查上一条群消息要排除 runtime_context.current_message_id，或使用 ORDER BY id DESC LIMIT 1 OFFSET 1。"
        "不要用 memory_read、read、grep 去找聊天数据库。"
        "常用示例：SELECT id, created_at, content FROM chat_logs "
        "WHERE session_id='private_0000000000' AND role='user' "
        "ORDER BY id DESC LIMIT 5；"
        "SELECT id, created_at, sender_name, content FROM chat_logs "
        "WHERE session_id='group_123456' AND role='ambient' "
        "AND message_id!='当前消息ID' ORDER BY id DESC LIMIT 1；"
        "SELECT id, created_at, sender_name, content FROM chat_logs "
        "WHERE session_id='group_123456' AND role='ambient' "
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
        return strip_sql_comments(sql)

    # ── 分级 LIMIT ──
    DEFAULT_MAX_SQL_LIMIT = SQL_DEFAULT_MAX_LIMIT
    AGGREGATE_MAX_SQL_LIMIT = SQL_AGGREGATE_MAX_LIMIT
    RAW_CONTENT_MAX_SQL_LIMIT = SQL_RAW_CONTENT_MAX_LIMIT

    @classmethod
    def _is_aggregate_query(cls, sql: str) -> bool:
        return is_aggregate_query(sql)

    @classmethod
    def _selects_raw_content(cls, sql: str) -> bool:
        return selects_raw_content(sql)

    @classmethod
    def _limit_cap_for_query(cls, sql: str) -> int:
        return limit_cap_for_query(sql)

    @classmethod
    def _validate_read_only_sql(cls, sql: str) -> tuple[bool, str]:
        return validate_read_only_sql(sql)

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        sql = args.get("sql", "")
        if not sql.strip():
            return ToolResult(error="Missing 'sql' argument")

        ok, reason = self._validate_read_only_sql(sql)
        if not ok:
            return ToolResult(error=f"Invalid SQL for sql_analysis: {reason}")

        result = self._get_sandbox().run_query(sql)
        if result.startswith("SQL Error:"):
            return ToolResult(error=result)
        return ToolResult(output=result, exit_code=0)
