"""Admin DB Browser 路由。"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.admin.common import verify_admin
from core.database import get_db

logger = logging.getLogger("nanobot.admin")
router = APIRouter(prefix="/db", tags=["admin-db-browser"])


class DbQuery(BaseModel):
    query: str


DB_TABLE_GROUPS = [
    {
        "key": "core",
        "label": "核心对话",
        "tables": [
            "users", "chat_logs", "conversation_turns", "memory_digests",
            "rolling_session_summaries", "session_summary_jobs",
        ],
    },
    {
        "key": "persona",
        "label": "画像与记忆",
        "tables": [
            "personas", "persona_facts", "persona_behaviors",
            "group_memories", "expression_memories", "jargon_memories",
            "sticker_memories",
        ],
    },
    {
        "key": "rag",
        "label": "向量与知识库",
        "tables": [
            "semantic_index_items", "semantic_index_jobs",
            "knowledge_sources", "knowledge_documents", "knowledge_chunks",
        ],
    },
    {
        "key": "runtime",
        "label": "LLM 与 Agent 调试",
        "tables": [
            "agent_runs", "tool_calls", "llm_api_request_logs",
            "runtime_tool_decisions", "tool_policy_decisions", "tool_overrides",
            "prompt_render_logs", "prompt_file_versions",
            "reply_contract_check_logs",
        ],
    },
    {
        "key": "rules",
        "label": "配置与规则",
        "tables": [
            "chat_stream_configs", "system_prompts", "scheduled_tasks",
            "admin_audit_logs", "user_block_rules", "content_block_rules",
            "system_settings",
        ],
    },
]

READONLY_TABLES = [table for group in DB_TABLE_GROUPS for table in group["tables"]]
READONLY_TABLE_SET = set(READONLY_TABLES)
BLOCKED_DB_TABLES = {
    "sensitive_data",
    "sqlite_master",
    "sqlite_schema",
    "sqlite_temp_master",
}
GLOBAL_REDACT_COLUMNS = {"headers_json"}
GLOBAL_PREVIEW_ONLY_COLUMNS = {"request_json", "response_json", "message_sources_json"}

DEFAULT_DB_TABLE_POLICY = {
    "description": "",
    "default_sort": "rowid DESC",
    "hidden_columns": [],
    "redact_columns": [],
    "preview_only_columns": [],
    "max_text_length": 1000,
}

DB_TABLE_POLICIES = {
    "chat_logs": {"description": "原始消息存档，含 tool 与 ambient。", "default_sort": "id DESC"},
    "conversation_turns": {"description": "精简对话上下文。", "default_sort": "id DESC"},
    "persona_facts": {"description": "用户画像事实与聚类数据。", "default_sort": "id DESC"},
    "persona_behaviors": {"description": "用户行为模式候选。", "default_sort": "id DESC"},
    "semantic_index_items": {
        "description": "统一语义索引条目。",
        "default_sort": "id DESC",
        "preview_only_columns": ["text", "lexical_text", "embedding_text", "meta_json"],
    },
    "semantic_index_jobs": {"description": "语义索引异步任务。", "default_sort": "id DESC"},
    "knowledge_sources": {"description": "外部知识来源。", "default_sort": "id DESC"},
    "knowledge_documents": {
        "description": "知识库文档和 ai_daily 入库记录。",
        "default_sort": "id DESC",
        "preview_only_columns": ["summary", "meta_json"],
    },
    "knowledge_chunks": {
        "description": "知识库文档 chunk。",
        "default_sort": "id DESC",
        "preview_only_columns": ["text", "citation_json", "meta_json"],
    },
    "llm_api_request_logs": {
        "description": "模型网关请求日志；默认仅展示预览字段。",
        "default_sort": "id DESC",
        "hidden_columns": ["headers_json", "request_json", "response_json", "message_sources_json"],
        "redact_columns": ["headers_json"],
        "preview_only_columns": ["request_json", "response_json", "message_sources_json"],
        "max_text_length": 600,
    },
    "prompt_render_logs": {
        "description": "PromptManager 渲染记录。",
        "default_sort": "id DESC",
        "preview_only_columns": ["variables_json", "rendered_preview", "warnings_json"],
        "max_text_length": 600,
    },
    "rolling_session_summaries": {
        "description": "滚动上下文摘要结果。",
        "default_sort": "id DESC",
        "preview_only_columns": ["summary_text", "summary_json", "source_turn_ids_json", "meta_json"],
    },
    "session_summary_jobs": {"description": "滚动摘要异步生成任务。", "default_sort": "id DESC"},
    "reply_contract_check_logs": {"description": "reply/no_reply 合约审核日志。", "default_sort": "id DESC"},
    "content_block_rules": {"description": "内容屏蔽规则。", "default_sort": "id DESC"},
    "agent_runs": {"description": "一次模型/Agent 处理请求。", "default_sort": "started_at DESC"},
    "tool_calls": {"description": "工具调用记录。", "default_sort": "started_at DESC"},
    "runtime_tool_decisions": {"description": "每轮运行时工具决策。", "default_sort": "id DESC"},
    "tool_policy_decisions": {"description": "工具策略决策记录。", "default_sort": "id DESC"},
    "tool_overrides": {"description": "工具权限覆盖。", "default_sort": "id DESC"},
}


def _db_table_policy(table_name: str) -> dict[str, Any]:
    policy = dict(DEFAULT_DB_TABLE_POLICY)
    policy.update(DB_TABLE_POLICIES.get(table_name, {}))
    return policy


def _db_table_meta(table_name: str) -> dict[str, Any]:
    policy = _db_table_policy(table_name)
    return {
        "description": policy["description"] or table_name,
        "default_sort": policy["default_sort"],
        "hidden_columns": list(policy["hidden_columns"]),
        "redact_columns": list(policy["redact_columns"]),
        "preview_only_columns": list(policy["preview_only_columns"]),
        "max_text_length": int(policy["max_text_length"]),
    }


def _quote_identifier(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier or ""):
        raise HTTPException(400, f"Invalid identifier: {identifier}")
    return '"' + identifier.replace('"', '""') + '"'


def _table_columns(db: Session, table_name: str) -> list[str]:
    quoted = _quote_identifier(table_name)
    rows = db.execute(text(f"PRAGMA table_info({quoted})")).fetchall()
    return [str(row[1]) for row in rows]


def _safe_serialize_cell(value: Any, table_name: str, column_name: str) -> tuple[Any, dict[str, Any]]:
    policy = _db_table_policy(table_name)
    redact_columns = set(policy["redact_columns"]) | GLOBAL_REDACT_COLUMNS
    preview_only_columns = set(policy["preview_only_columns"]) | GLOBAL_PREVIEW_ONLY_COLUMNS
    meta: dict[str, Any] = {
        "kind": "null" if value is None else "value",
        "truncated": False,
        "full_length": None,
    }
    if column_name in redact_columns:
        meta["kind"] = "redacted"
        return "<redacted>", meta
    if value is None:
        return None, meta
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        size = len(value)
        meta.update({"kind": "binary", "truncated": True, "full_length": size})
        return f"<binary {size} bytes>", meta

    if isinstance(value, (str, int, float, bool)):
        display = value
    else:
        display = str(value)

    if isinstance(display, str):
        max_len = int(policy["max_text_length"])
        if column_name in preview_only_columns:
            max_len = min(max_len, 300)
        meta.update({"kind": "text", "full_length": len(display)})
        if len(display) > max_len:
            meta["truncated"] = True
            return display[:max_len] + "...", meta
    return display, meta


def _serialize_db_rows(
    table_name: str,
    columns: list[str],
    fetched_rows: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    cell_meta: list[dict[str, Any]] = []
    for raw_row in fetched_rows:
        values = list(raw_row)
        row_dict: dict[str, Any] = {}
        meta_dict: dict[str, Any] = {}
        for column, value in zip(columns, values):
            display, meta = _safe_serialize_cell(value, table_name, column)
            row_dict[column] = display
            meta_dict[column] = meta
        rows.append(row_dict)
        cell_meta.append(meta_dict)
    return rows, cell_meta


def _extract_query_table_names(query: str) -> list[str]:
    """提取 SELECT 中 FROM/JOIN 后的表名，避免使用连接级 SQLite authorizer。"""
    tables: list[str] = []
    pattern = re.compile(
        r"\b(?:FROM|JOIN)\s+(?:main\.)?(?:\"([A-Za-z_][A-Za-z0-9_]*)\"|([A-Za-z_][A-Za-z0-9_]*))",
        re.IGNORECASE,
    )
    for match in pattern.finditer(query):
        name = (match.group(1) or match.group(2) or "").strip()
        if name:
            tables.append(name)
    return tables


def _validate_query_tables_allowed(query: str) -> None:
    sqlite_table = re.search(r"\bsqlite_[A-Za-z0-9_]*\b", query, re.IGNORECASE)
    if sqlite_table:
        raise HTTPException(400, f"Forbidden table: {sqlite_table.group(0)}")
    for table in _extract_query_table_names(query):
        normalized = table.lower()
        if normalized in BLOCKED_DB_TABLES or normalized.startswith("sqlite_") or table not in READONLY_TABLE_SET:
            raise HTTPException(400, f"Forbidden table: {table}")


def _validate_readonly_query(query: str) -> str:
    q = query.strip()
    q_no_trailing_semicolon = q[:-1].strip() if q.endswith(";") else q
    if not q_no_trailing_semicolon:
        raise HTTPException(400, "Empty query")
    if ";" in q_no_trailing_semicolon:
        raise HTTPException(400, "Multi-statement forbidden")
    if not re.match(r"^\s*SELECT\b", q_no_trailing_semicolon, re.IGNORECASE):
        raise HTTPException(400, "Only SELECT allowed")

    forbidden = (
        "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
        "PRAGMA", "ATTACH", "DETACH", "VACUUM", "REINDEX", "LOAD_EXTENSION",
        "REPLACE",
    )
    for word in forbidden:
        if re.search(rf"\b{word}\b", q_no_trailing_semicolon, re.IGNORECASE):
            raise HTTPException(400, f"Forbidden: {word}")
    for table in BLOCKED_DB_TABLES:
        if re.search(rf"\b{re.escape(table)}\b", q_no_trailing_semicolon, re.IGNORECASE):
            raise HTTPException(400, f"Forbidden table: {table}")
    _validate_query_tables_allowed(q_no_trailing_semicolon)
    return q_no_trailing_semicolon


def _available_readonly_tables(db: Session) -> list[str]:
    existing = {
        str(row[0])
        for row in db.execute(
            text("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
        ).fetchall()
    }
    return [table for table in READONLY_TABLES if table in existing]


def _available_db_groups(db: Session) -> list[dict[str, Any]]:
    available = set(_available_readonly_tables(db))
    groups: list[dict[str, Any]] = []
    for group in DB_TABLE_GROUPS:
        tables = [table for table in group["tables"] if table in available]
        if tables:
            groups.append({**group, "tables": tables})
    return groups


@router.get("/tables")
def list_tables(db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    tables = _available_readonly_tables(db)
    return {
        "tables": tables,
        "groups": _available_db_groups(db),
        "table_meta": {table: _db_table_meta(table) for table in tables},
    }


@router.get("/tables/{table_name}")
def query_table(
    table_name: str,
    page: int = 1,
    limit: int = 50,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    if table_name not in READONLY_TABLES:
        raise HTTPException(400, f"Unknown table: {table_name}")
    page = max(int(page), 1)
    limit = max(1, min(int(limit), 200))
    try:
        all_columns = _table_columns(db, table_name)
        hidden_columns = set(_db_table_policy(table_name)["hidden_columns"])
        columns = [column for column in all_columns if column not in hidden_columns]
        if not columns:
            raise HTTPException(400, f"No visible columns: {table_name}")
        quoted_table = _quote_identifier(table_name)
        select_columns = ", ".join(_quote_identifier(column) for column in columns)
        default_sort = _db_table_policy(table_name)["default_sort"]
        result = db.execute(
            text(f"SELECT {select_columns} FROM {quoted_table} ORDER BY {default_sort} LIMIT :limit OFFSET :offset"),
            {"limit": limit, "offset": (page - 1) * limit},
        )
        result_columns = list(result.keys())
        fetched = result.fetchall()
        rows, cell_meta = _serialize_db_rows(table_name, result_columns, fetched)
        total = db.execute(text(f"SELECT COUNT(*) FROM {quoted_table}")).scalar()
        return {
            "total": total,
            "page": page,
            "limit": limit,
            "has_next": (page * limit) < int(total or 0),
            "columns": result_columns,
            "rows": rows,
            "cell_meta": cell_meta,
            "table_meta": _db_table_meta(table_name),
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("admin db table query failed: table=%s", table_name)
        raise HTTPException(500, "内部错误")


@router.post("/query")
def execute_readonly_query(
    body: DbQuery,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    q = _validate_readonly_query(body.query)
    try:
        query_with_limit = f"SELECT * FROM ({q}) LIMIT 500"
        result = db.execute(text(query_with_limit))
        columns = list(result.keys()) if result.returns_rows else []
        fetched = result.fetchall() if result.returns_rows else []
        rows, cell_meta = _serialize_db_rows("", columns, fetched)
        return {"columns": columns, "rows": rows, "cell_meta": cell_meta, "row_count": len(rows)}
    except HTTPException:
        raise
    except Exception:
        logger.exception("admin readonly db query failed")
        raise HTTPException(500, "内部错误")
