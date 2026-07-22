import asyncio
import os
from collections.abc import Callable
from datetime import datetime
from typing import TypeVar

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    event,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql.dml import Delete, Insert, Update
from sqlalchemy.sql.elements import TextClause

from config import DATABASE_URL
from core.db.base import Base
# 兼容历史 ``from core.database import Model``；新代码应直接依赖子域模型。
from core.db.models import (  # noqa: F401
    Asset,
    ChatLog,
    ChatDeliveryOutbox,
    ConversationTurn,
    InboundMessageClaim,
    MemoryDigest,
    MemoryDigestJob,
    OutboundDeliveryAttempt,
    OutboundDeliveryCircuit,
    OutboundDeliveryControl,
    OutboundDeliveryOutbox,
    OutboundGenerationAttempt,
    OutboundRun,
    Persona,
    PersonaBehavior,
    PersonaFact,
    ProactiveOutreachLease,
    ProactiveOutreachLog,
    SensitiveData,
    RollingSessionSummary,
    SandboxAccessGrant,
    SandboxAdminOperation,
    SandboxProjectSequence,
    SandboxRun,
    SessionSummaryJob,
    ScheduledTask,
    SystemPrompt,
    User,
    Workspace,
    WorkspaceAsset,
    WorkspaceQuotaBinding,
)

# 使用绝对路径确保在 Docker 挂载时路径不飘移
DB_DIR = os.path.abspath("./data")
DB_PATH = os.path.join(DB_DIR, "nanobot.db")
# BUG-20 FIX: DATABASE_URL now imported from config.py (single source of truth)


def _is_sqlite_database_url(database_url: str) -> bool:
    try:
        return make_url(database_url).drivername.startswith("sqlite")
    except Exception:
        return False


def _sqlite_busy_timeout_ms() -> int:
    try:
        return max(1000, int(float(os.environ.get("SQLITE_BUSY_TIMEOUT_MS", "5000"))))
    except (TypeError, ValueError):
        return 5000


def sqlite_connect_args_for_url(database_url: str) -> dict:
    if not _is_sqlite_database_url(database_url):
        return {}
    return {
        "check_same_thread": False,
        "timeout": _sqlite_busy_timeout_ms() / 1000.0,
    }


_SESSION_WRITE_TRANSACTION_IDS = "nanobot_write_transaction_ids"
_SESSION_COMMITTED_NESTED_IDS = "nanobot_committed_nested_ids"


def _current_session_write_transaction(db):
    nested = getattr(db, "get_nested_transaction", None)
    if callable(nested):
        nested_transaction = nested()
        if nested_transaction is not None:
            return nested_transaction
    root = getattr(db, "get_transaction", None)
    return root() if callable(root) else None


def _mark_session_transaction_write(db) -> None:
    transaction = _current_session_write_transaction(db)
    if transaction is None:
        return
    db.info.setdefault(_SESSION_WRITE_TRANSACTION_IDS, set()).add(
        id(transaction)
    )


def _text_sql_is_proven_read_only(raw_sql: str) -> bool:
    """只放行可保守识别的单条 SELECT；其他原始 SQL 一律按可能写入处理。"""

    remaining = str(raw_sql or "")
    if ";" in remaining:
        return False
    while True:
        remaining = remaining.lstrip()
        if remaining.startswith("--"):
            line_ends = [
                position
                for position in (
                    remaining.find("\n", 2),
                    remaining.find("\r", 2),
                )
                if position >= 0
            ]
            if not line_ends:
                return False
            remaining = remaining[min(line_ends) + 1:]
            continue
        if remaining.startswith("/*"):
            comment_end = remaining.find("*/", 2)
            if comment_end < 0:
                return False
            remaining = remaining[comment_end + 2:]
            continue
        break
    first_token = remaining.split(None, 1)
    return bool(first_token) and first_token[0].upper() == "SELECT"


def _orm_execute_may_write(execute_state) -> bool:
    if execute_state.is_insert or execute_state.is_update or execute_state.is_delete:
        return True
    statement = execute_state.statement
    if isinstance(statement, (Insert, Update, Delete)):
        return True
    if isinstance(statement, TextClause):
        return not _text_sql_is_proven_read_only(statement.text)
    return False


@event.listens_for(OrmSession, "do_orm_execute", retval=True)
def _remember_session_execute_writes(execute_state):
    result = execute_state.invoke_statement()
    if _orm_execute_may_write(execute_state):
        _mark_session_transaction_write(execute_state.session)
    return result


@event.listens_for(OrmSession, "after_flush")
def _remember_flushed_session_writes(db, _flush_context) -> None:
    """记录已发送到数据库、但尚未提交的 ORM 写入。"""

    if db.new or db.dirty or db.deleted:
        _mark_session_transaction_write(db)


@event.listens_for(OrmSession, "after_commit")
def _remember_committed_nested_transaction(db) -> None:
    nested = getattr(db, "get_nested_transaction", None)
    transaction = nested() if callable(nested) else None
    if transaction is not None:
        db.info.setdefault(_SESSION_COMMITTED_NESTED_IDS, set()).add(
            id(transaction)
        )


@event.listens_for(OrmSession, "after_transaction_end")
def _clear_flushed_session_writes(db, transaction) -> None:
    """按事务层级传播或丢弃写标记。"""

    parent = getattr(
        transaction,
        "parent",
        getattr(transaction, "_parent", None),
    )
    if parent is None:
        db.info.pop(_SESSION_WRITE_TRANSACTION_IDS, None)
        db.info.pop(_SESSION_COMMITTED_NESTED_IDS, None)
        return
    if not bool(getattr(transaction, "nested", False)):
        return

    write_ids = db.info.setdefault(_SESSION_WRITE_TRANSACTION_IDS, set())
    committed_ids = db.info.setdefault(_SESSION_COMMITTED_NESTED_IDS, set())
    transaction_id = id(transaction)
    had_writes = transaction_id in write_ids
    committed = transaction_id in committed_ids
    write_ids.discard(transaction_id)
    committed_ids.discard(transaction_id)
    if had_writes and committed:
        write_ids.add(id(parent))


def release_clean_session_transaction(db, *, label: str = "", logger=None) -> bool:
    """释放只读/干净的 Session 事务，避免跨长 await 持有 SQLite 事务。"""
    try:
        in_transaction = getattr(db, "in_transaction", None)
        if not callable(in_transaction) or not in_transaction():
            return False
        new_count = len(getattr(db, "new", ()) or ())
        dirty_count = len(getattr(db, "dirty", ()) or ())
        deleted_count = len(getattr(db, "deleted", ()) or ())
        pending_count = new_count + dirty_count + deleted_count
        write_ids = getattr(db, "info", {}).get(
            _SESSION_WRITE_TRANSACTION_IDS,
            set(),
        )
        root_transaction = getattr(db, "get_transaction", lambda: None)()
        nested_transaction = getattr(
            db,
            "get_nested_transaction",
            lambda: None,
        )()
        flushed_writes = any(
            transaction is not None and id(transaction) in write_ids
            for transaction in (root_transaction, nested_transaction)
        )
        if pending_count or flushed_writes:
            if logger is not None:
                logger.warning(
                    "[DB] skip releasing session transaction label=%s pending=%d "
                    "new=%d dirty=%d deleted=%d flushed=%d",
                    label or "unknown",
                    pending_count,
                    new_count,
                    dirty_count,
                    deleted_count,
                    int(flushed_writes),
                )
            return False
        db.rollback()
        if logger is not None:
            debug = getattr(logger, "debug", None)
            if callable(debug):
                debug("[DB] released clean session transaction before await label=%s", label or "unknown")
        return True
    except Exception as exc:
        if logger is not None:
            warning = getattr(logger, "warning", None)
            if callable(warning):
                warning("[DB] failed to release session transaction label=%s: %s", label or "unknown", exc)
        return False


def configure_sqlite_connection(dbapi_connection, *, database_url: str = DATABASE_URL) -> None:
    if not _is_sqlite_database_url(database_url):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={_sqlite_busy_timeout_ms()}")
        if sqlite_path_from_database_url(database_url):
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


engine = create_engine(DATABASE_URL, connect_args=sqlite_connect_args_for_url(DATABASE_URL))


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    configure_sqlite_connection(dbapi_connection, database_url=DATABASE_URL)


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

_PhaseResultT = TypeVar("_PhaseResultT")


def session_factory_from_session(
    session: OrmSession,
) -> Callable[[], OrmSession]:
    """基于请求 Session 的绑定创建 fresh Session 工厂。

    异步入口不能把同一个同步 SQLAlchemy Session 传到工作线程，也不能退回
    隐藏的全局 ``SessionLocal``，否则 FastAPI 注入的数据库绑定会被绕过。
    """

    if not isinstance(session, OrmSession):
        raise TypeError("session 必须是 SQLAlchemy Session")
    bind = session.get_bind()
    if bind is None:
        raise RuntimeError("请求 Session 缺少数据库绑定")
    return sessionmaker(
        bind=bind,
        autocommit=False,
        autoflush=session.autoflush,
        expire_on_commit=session.expire_on_commit,
    )


def run_session_phase(
    operation: Callable[[OrmSession], _PhaseResultT],
    *,
    session_factory: Callable[[], OrmSession] | None = None,
) -> _PhaseResultT:
    """在同一线程内创建、使用并关闭一个短生命周期 Session。"""

    factory = session_factory or SessionLocal
    db = factory()
    try:
        return operation(db)
    except BaseException:
        try:
            db.rollback()
        except BaseException:
            pass
        raise
    finally:
        db.close()


async def run_session_phase_async(
    operation: Callable[[OrmSession], _PhaseResultT],
    *,
    session_factory: Callable[[], OrmSession] | None = None,
) -> _PhaseResultT:
    """把完整同步数据库阶段移出事件循环，且不跨线程传递 Session。"""

    return await asyncio.to_thread(
        run_session_phase,
        operation,
        session_factory=session_factory,
    )

def sqlite_path_from_database_url(database_url: str) -> str | None:
    """从 SQLite DATABASE_URL 解析真实文件路径；非文件型 SQLite 返回 None。"""
    try:
        url = make_url(database_url)
    except Exception:
        return None
    if not url.drivername.startswith("sqlite"):
        return None
    database = url.database
    if not database or database == ":memory:":
        return None
    return os.path.abspath(database)


class GroupMemory(Base):
    """群体记忆——群聊中长期稳定的群体认知。
    memory_type: topic/slang/relationship/style/event/preference
    不复用 PersonaFact——群记忆和用户画像是不同维度。
    """

    __tablename__ = "group_memories"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    group_id = Column(String, index=True, nullable=False)
    memory_type = Column(String, index=True, nullable=False)
    content = Column(Text, nullable=False)
    content_hash = Column(String, default="")  # SHA256 前 32 位，快速去重
    cluster_key = Column(String, nullable=True)  # 语义聚类稳定 key
    evidence_log_ids_json = Column(Text, default="[]")
    confidence = Column(Float, default=0.5)
    evidence_count = Column(Integer, default=1)
    first_seen = Column(DateTime, default=datetime.now)
    last_seen = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    decay_score = Column(Float, default=1.0)
    status = Column(String, default="active")  # review/active/disabled/archived/rejected
    inject_policy = Column(String, default="auto")  # auto/manual_only/never
    disabled_reason = Column(Text, default="")
    rejected_reason = Column(Text, default="")
    merged_into_id = Column(Integer, nullable=True)
    last_injected_at = Column(DateTime, nullable=True)
    injected_count = Column(Integer, default=0)
    source = Column(String, default="group_analysis")  # group_analysis/slang_miner/manual/profile_feedback
    meta_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("group_id", "memory_type", "content_hash", name="uq_group_memory_hash"),
    )


class ExpressionMemory(Base):
    """群聊表达学习——短句、语气词、句式等说话方式。"""
    __tablename__ = "expression_memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_stream_id = Column(String, index=True, nullable=False)
    expression = Column(Text, nullable=False)
    expression_type = Column(String, default="phrase")
    scene = Column(String, default="")
    example_json = Column(Text, default="[]")
    source_count = Column(Integer, default=1)
    confidence = Column(Float, default=0.5)
    checked = Column(Integer, default=0)
    status = Column(String, default="candidate")
    weight = Column(Float, default=0.5)
    first_seen = Column(DateTime, default=datetime.now)
    last_seen = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (UniqueConstraint("chat_stream_id", "expression", name="uq_expr_stream_expr"),)

class JargonMemory(Base):
    """群聊黑话/术语学习——词义解释和使用条件。"""
    __tablename__ = "jargon_memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_stream_id = Column(String, index=True, nullable=False)
    term = Column(String, index=True, nullable=False)
    meaning = Column(Text, default="")
    examples_json = Column(Text, default="[]")
    confidence = Column(Float, default=0.5)
    checked = Column(Integer, default=0)
    status = Column(String, default="candidate")
    first_seen = Column(DateTime, default=datetime.now)
    last_seen = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (UniqueConstraint("chat_stream_id", "term", name="uq_jargon_stream_term"),)


class StickerMemory(Base):
    """群聊表情包记忆——按群/全局作用域存储可发送图片引用。"""
    __tablename__ = "sticker_memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_stream_id = Column(String, index=True, nullable=False)
    sticker_hash = Column(String, index=True, nullable=False)
    file_ref = Column(Text, nullable=False)
    send_code = Column(Text, default="")
    name = Column(String, default="")
    description = Column(Text, default="")
    tags_json = Column(Text, default="[]")
    emotions_json = Column(Text, default="[]")
    source_type = Column(String, default="manual")
    source_count = Column(Integer, default=1)
    status = Column(String, default="active")
    usage_count = Column(Integer, default=0)
    first_seen = Column(DateTime, default=datetime.now)
    last_seen = Column(DateTime, default=datetime.now)
    last_used = Column(DateTime, nullable=True)
    meta_json = Column(Text, default="{}")
    local_path = Column(Text, default="")
    preview_status = Column(String, default="pending")
    content_hash = Column(String, index=True, default="")
    byte_size = Column(Integer, default=0)
    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    phash = Column(String(32), default="")
    dhash = Column(String(32), default="")
    ahash = Column(String(32), default="")
    duplicate_of_id = Column(Integer, nullable=True, index=True)
    dedupe_status = Column(String, default="unique")
    describe_status = Column(String, default="pending")
    describe_attempts = Column(Integer, default=0)
    describe_last_error = Column(Text, default="")
    described_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("chat_stream_id", "sticker_hash", name="uq_sticker_stream_hash"),
    )


class KnowledgeSource(Base):
    """外部知识来源。"""

    __tablename__ = "knowledge_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_key = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, default="")
    source_type = Column(String, default="manual")
    domain = Column(String, index=True, default="")
    base_url = Column(Text, default="")
    status = Column(String, index=True, default="active")
    trust_level = Column(String, index=True, default="medium")
    meta_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class KnowledgeDocument(Base):
    """知识库文档，支持手工文件、URL 元数据和 ai_daily 摘要。"""

    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(Integer, index=True, nullable=True)
    document_kind = Column(String, index=True, default="manual_file")
    title = Column(Text, default="")
    url = Column(Text, default="")
    domain = Column(String, index=True, default="")
    author = Column(String, default="")
    published_at = Column(String, index=True, default="")
    summary = Column(Text, default="")
    status = Column(String, index=True, default="active")
    trust_level = Column(String, index=True, default="medium")
    created_by = Column(String, default="")
    updated_by = Column(String, default="")
    disabled_reason = Column(Text, default="")
    disabled_by = Column(String, default="")
    disabled_at = Column(DateTime, nullable=True)
    latest_seen = Column(DateTime, nullable=True)
    meta_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class KnowledgeChunk(Base):
    """知识库文档的可检索 chunk。"""

    __tablename__ = "knowledge_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, index=True, nullable=False)
    chunk_id = Column(String, index=True, nullable=False)
    order_index = Column(Integer, default=0)
    title = Column(Text, default="")
    text = Column(Text, default="")
    citation_json = Column(Text, default="{}")
    status = Column(String, index=True, default="active")
    trust_level = Column(String, index=True, default="medium")
    meta_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_id", name="uq_knowledge_doc_chunk"),
    )


class ChatStreamConfig(Base):
    """群聊/私聊流配置——talk_value、表达开关等。"""
    __tablename__ = "chat_stream_configs"

    chat_stream_id = Column(String, primary_key=True)
    talk_value = Column(Float, default=0.5)
    mentioned_bot_reply = Column(Integer, default=1)
    use_expression = Column(Integer, default=1)
    enable_expression_learning = Column(Integer, default=1)
    enable_jargon_learning = Column(Integer, default=1)
    group_profile_mode = Column(String, default="off")  # off/preview/on
    planner_smooth = Column(Integer, default=3)
    session_guidance = Column(
        Text,
        default="",
        server_default=text("''"),
        nullable=False,
    )
    session_guidance_updated_at = Column(DateTime, nullable=True)
    meta_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.now)


class AdminAuditLog(Base):
    """WebUI 管理操作审计日志。"""
    __tablename__ = "admin_audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    admin_user = Column(String, default="admin")
    action = Column(String, nullable=False)
    target_type = Column(String, default="")
    target_id = Column(String, default="")
    detail_json = Column(Text, default="{}")
    ip_address = Column(String, default="")
    created_at = Column(DateTime, default=datetime.now)


class MemoryCleanupRun(Base):
    """生产记忆清洗的一次性、可审计执行记录。"""

    __tablename__ = "memory_cleanup_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cleanup_version = Column(String(64), nullable=False, default="")
    bundle_sha256 = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="applying")
    actor = Column(String(255), nullable=False, default="cli")
    audit_log_id = Column(Integer, nullable=True)
    target_counts_json = Column(Text, nullable=False, default="{}")
    result_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=datetime.now)
    applied_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "bundle_sha256",
            name="uq_memory_cleanup_run_bundle_sha256",
        ),
        Index("idx_memory_cleanup_run_status", "status", "id"),
    )


class UserBlockRule(Base):
    """用户屏蔽规则——命中后消息只写 ChatLog，不触发回复。"""
    __tablename__ = "user_block_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, index=True, nullable=False)
    target_type = Column(String, default="private")  # private / group
    group_id = Column(String, default="")             # 仅 group 类型时生效
    rule_mode = Column(String, default="log_only")    # log_only / silent
    reason = Column(Text, default="")
    enabled = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ContentBlockRule(Base):
    """内容屏蔽规则——匹配消息正文，支持 no_reply/no_learn/no_context 控制。"""
    __tablename__ = "content_block_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pattern = Column(String, nullable=False)
    match_type = Column(String, default="contains")  # contains / exact / regex
    scope_type = Column(String, default="session")   # session / global
    chat_stream_id = Column(String, default="")
    no_reply = Column(Integer, default=0)
    no_learn = Column(Integer, default=1)
    no_context = Column(Integer, default=0)
    category = Column(String, default="no_learn")
    enabled = Column(Integer, default=1)
    reason = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ToolOverride(Base):
    """工具权限覆盖——per-chat_type/per-platform/per-group/per-user 启用/禁用。"""
    __tablename__ = "tool_overrides"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tool_name = Column(String, nullable=False, index=True)
    scope_type = Column(String, nullable=False)  # "chat_type" | "platform" | "group" | "user"
    scope_id = Column(String, nullable=False)    # chat_type / platform / group_id / user_id
    enabled = Column(Integer, nullable=False, default=1)
    reason = Column(Text, default="")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("tool_name", "scope_type", "scope_id", name="uq_tool_override"),
    )


class RuntimeToolDecision(Base):
    """每轮运行时工具决策记录——供 WebUI 排查'为什么某工具不可用'。"""
    __tablename__ = "runtime_tool_decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, index=True)
    message_id = Column(String, default="")
    chat_type = Column(String, default="group")  # "group" | "private"
    platform = Column(String, default="")
    group_id = Column(String, default="")
    user_id = Column(String, default="")
    runtime_preset = Column(String, default="full")  # "none" | "lightweight" | "full"
    enabled_tools_json = Column(Text, default="[]")
    disabled_tools_json = Column(Text, default="[]")
    disabled_reasons_json = Column(Text, default="{}")
    effective_tools_json = Column(Text, default="[]")
    created_at = Column(DateTime, default=datetime.now)


class SystemSetting(Base):
    """WebUI 系统设置——KV 存储。"""
    __tablename__ = "system_settings"

    key = Column(String, primary_key=True)
    value = Column(Text, default="")
    description = Column(Text, default="")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class WebSearchProviderUsage(Base):
    """Web Search provider 聚合调用统计。"""
    __tablename__ = "web_search_provider_usage"

    provider_id = Column(String, primary_key=True, index=True)
    total_calls = Column(Integer, default=0)
    success_calls = Column(Integer, default=0)
    failure_calls = Column(Integer, default=0)
    last_called_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    last_error_at = Column(DateTime, nullable=True)
    last_error_code = Column(String, default="")
    last_duration_ms = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class AgentRun(Base):
    """一次模型/Agent 处理请求的运行记录。"""
    __tablename__ = "agent_runs"

    run_id = Column(String, primary_key=True, index=True)
    trace_id = Column(String, index=True, default="")
    session_id = Column(String, index=True, default="")
    user_id = Column(String, index=True, default="")
    chat_type = Column(String, index=True, default="")
    group_id = Column(String, index=True, default="")
    run_type = Column(String, index=True, default="chat")
    prompt_mode = Column(String, index=True, default="legacy")
    prompt_key = Column(String, index=True, default="")
    prompt_source = Column(String, index=True, default="")
    prompt_runtime_path = Column(Text, default="")
    prompt_default_path = Column(Text, default="")
    prompt_sha256 = Column(String, index=True, default="")
    prompt_template_resolutions_json = Column(
        Text,
        nullable=False,
        default="{}",
        server_default=text("'{}'"),
    )
    model = Column(String, index=True, default="")
    status = Column(String, index=True, default="running")
    input_preview = Column(Text, default="")
    output_preview = Column(Text, default="")
    error = Column(Text, default="")
    latency_ms = Column(Integer, default=0)
    meta_json = Column(Text, default="{}")
    started_at = Column(DateTime, default=datetime.now, index=True)
    finished_at = Column(DateTime, nullable=True)


class ToolCall(Base):
    """工具调用记录——只保存脱敏参数和截断结果预览。"""
    __tablename__ = "tool_calls"

    tool_call_id = Column(String, primary_key=True, index=True)
    trace_id = Column(String, index=True, default="")
    run_id = Column(String, index=True, default="")
    tool_name = Column(String, index=True, default="")
    args_json = Column(Text, default="{}")
    result_preview = Column(Text, default="")
    status = Column(String, index=True, default="running")
    latency_ms = Column(Integer, default=0)
    error = Column(Text, default="")
    started_at = Column(DateTime, default=datetime.now, index=True)
    finished_at = Column(DateTime, nullable=True)


class PromptRenderLog(Base):
    """PromptManager 渲染记录——默认不存完整 prompt，只存预览。"""
    __tablename__ = "prompt_render_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trace_id = Column(String, index=True, default="")
    run_id = Column(String, index=True, default="")
    prompt_key = Column(String, index=True, default="")
    mode = Column(String, index=True, default="preview")
    prompt_source = Column(String, index=True, default="")
    prompt_runtime_path = Column(Text, default="")
    prompt_default_path = Column(Text, default="")
    prompt_sha256 = Column(String, index=True, default="")
    prompt_template_resolutions_json = Column(
        Text,
        nullable=False,
        default="{}",
        server_default=text("'{}'"),
    )
    variables_json = Column(Text, default="{}")
    rendered_preview = Column(Text, default="")
    token_estimate = Column(Integer, default=0)
    warnings_json = Column(Text, default="[]")
    error = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now, index=True)


class PromptFileVersion(Base):
    """Prompt 文件版本元数据（实际备份文件仍在 data/prompt_template_backups）。"""
    __tablename__ = "prompt_file_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    prompt_key = Column(String, index=True, default="")
    backup_name = Column(String, index=True, default="")
    content_hash = Column(String, index=True, default="")
    operator = Column(String, default="")
    note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now, index=True)


class LLMApiRequestLog(Base):
    """LLM API 请求记录——保存发往模型网关的完整请求体。"""
    __tablename__ = "llm_api_request_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trace_id = Column(String, index=True, default="")
    run_id = Column(String, index=True, default="")
    source = Column(String, index=True, default="")
    provider = Column(String, index=True, default="")
    model = Column(String, index=True, default="")
    url = Column(Text, default="")
    method = Column(String, default="POST")
    request_json = Column(Text, default="{}")
    request_preview = Column(Text, default="")
    headers_json = Column(Text, default="{}")
    status = Column(String, index=True, default="created")
    response_status = Column(Integer, default=0)
    response_json = Column(Text, default="{}")
    response_preview = Column(Text, default="")
    latency_ms = Column(Integer, default=0)
    finished_at = Column(DateTime, nullable=True)
    error = Column(Text, default="")
    message_sources_json = Column(Text, default="[]")
    request_lint_json = Column(Text, default="{}")
    actual_sent_tools_json = Column(Text, default="[]")
    runtime_enabled_tools_json = Column(Text, default="[]")
    runtime_disabled_tools_json = Column(Text, default="[]")
    framework_injected_tools_json = Column(Text, default="[]")
    created_at = Column(DateTime, default=datetime.now, index=True)


class ReplyContractCheckLog(Base):
    """reply/no_reply 调用合约审核日志。"""
    __tablename__ = "reply_contract_check_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trace_id = Column(String, index=True, default="")
    run_id = Column(String, index=True, default="")
    session_id = Column(String, index=True, default="")
    attempt = Column(Integer, default=0)
    raw_output_preview = Column(Text, default="")
    has_reply_tool = Column(Integer, default=0)
    has_no_reply_tool = Column(Integer, default=0)
    has_structured_fallback = Column(Integer, default=0)
    reply_tool_call_count = Column(Integer, default=0)
    no_reply_tool_call_count = Column(Integer, default=0)
    structured_fallback_count = Column(Integer, default=0)
    total_final_action_count = Column(Integer, default=0)
    result = Column(String, index=True, default="")
    created_at = Column(DateTime, default=datetime.now, index=True)


class ReplyEvalCase(Base):
    """Reply 合约评估用例。"""
    __tablename__ = "reply_eval_cases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String, unique=True, index=True)
    title = Column(String, default="")
    chat_type = Column(String, default="group")
    input_text = Column(Text, default="")
    context_json = Column(Text, default="{}")
    expected_action = Column(String, default="any")
    expected_keywords_json = Column(Text, default="[]")
    forbidden_keywords_json = Column(Text, default="[]")
    source = Column(String, default="manual")
    tags_json = Column(Text, default="[]")
    enabled = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ReplyEvalRun(Base):
    """Reply 合约评估批次。"""
    __tablename__ = "reply_eval_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, default="")
    variant = Column(String, default="")
    total = Column(Integer, default=0)
    reply_contract_ok = Column(Integer, default=0)
    retry_used = Column(Integer, default=0)
    passed = Column(Integer, default=0)
    failed = Column(Integer, default=0)
    summary_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.now, index=True)


class ReplyEvalResult(Base):
    """Reply 合约评估单条结果。"""
    __tablename__ = "reply_eval_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, index=True)
    agent_run_id = Column(String, index=True, default="")
    trace_id = Column(String, index=True, default="")
    prompt_sha256 = Column(String, index=True, default="")
    case_id = Column(String, index=True)
    variant = Column(String, default="")
    expected_action = Column(String, default="")
    actual_action = Column(String, default="")
    called_reply_or_no_reply = Column(Integer, default=0)
    retry_used = Column(Integer, default=0)
    passed = Column(Integer, default=0)
    raw_output_preview = Column(Text, default="")
    final_content_preview = Column(Text, default="")
    error = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now, index=True)


# ── Eval tables ──

class EvalCandidate(Base):
    __tablename__ = "eval_candidates"
    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String, unique=True, nullable=False)
    suite = Column(String, nullable=False)
    source = Column(String, default="log")
    source_ref = Column(String, default="")
    description = Column(Text, default="")
    input_json = Column(Text, default="{}")
    expected_json = Column(Text, default='{"needs_label": true}')
    tags_json = Column(Text, default="[]")
    status = Column(String, default="candidate")
    priority = Column(Integer, default=0)
    fingerprint = Column(String, index=True, default="")
    note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class EvalSampleCursor(Base):
    __tablename__ = "eval_sample_cursors"
    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(String, nullable=False)
    source_key = Column(String, nullable=False)
    cursor_json = Column(Text, default="{}")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class EvalRun(Base):
    __tablename__ = "eval_runs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    suite = Column(String)
    git_sha = Column(String, default="")
    status = Column(String, default="running")
    total = Column(Integer, default=0)
    passed = Column(Integer, default=0)
    failed = Column(Integer, default=0)
    pass_rate = Column(Float, default=0.0)
    summary_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.now)


class EvalRunResult(Base):
    __tablename__ = "eval_run_results"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, index=True)
    case_id = Column(String)
    suite = Column(String)
    passed = Column(Integer)
    score = Column(Float)
    errors_json = Column(Text, default="[]")
    output_json = Column(Text, default="{}")


class StickerDuplicateCandidate(Base):
    """感知哈希近邻重复候选——手工确认后执行精确去重。"""
    __tablename__ = "sticker_duplicate_candidates"
    id = Column(Integer, primary_key=True, autoincrement=True)
    sticker_a_id = Column(Integer, index=True, nullable=False)
    sticker_b_id = Column(Integer, index=True, nullable=False)
    content_hash = Column(String(64), default="")  # for grouping by same image
    phash_dist = Column(Integer, default=0)
    dhash_dist = Column(Integer, default=0)
    ahash_dist = Column(Integer, default=0)
    status = Column(String, default="pending")  # pending/confirmed/ignored
    created_at = Column(DateTime, default=datetime.now)
    __table_args__ = (UniqueConstraint("sticker_a_id", "sticker_b_id"),)


class SemanticIndexItem(Base):
    """统一语义索引条目。"""

    __tablename__ = "semantic_index_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    source_type = Column(String, index=True, nullable=False, default="")
    source_id = Column(String, index=True, nullable=False, default="")
    source_sub_id = Column(String, index=True, nullable=False, default="")
    document_id = Column(String, index=True, default="")
    chunk_id = Column(String, index=True, default="")
    user_id = Column(String, index=True, default="")
    session_id = Column(String, index=True, default="")
    group_id = Column(String, index=True, default="")
    chat_stream_id = Column(String, index=True, default="")
    visibility = Column(String, index=True, default="recall")
    status = Column(String, index=True, default="active")
    title = Column(Text, default="")
    text = Column(Text, default="")
    lexical_text = Column(Text, default="")
    embedding_text = Column(Text, default="")
    text_hash = Column(String, index=True, default="")
    source_hash = Column(String, index=True, default="")
    source_updated_at = Column(DateTime, nullable=True)
    embedding = Column(LargeBinary, nullable=True)
    embedding_dim = Column(Integer, default=0)
    embedding_model = Column(String, default="")
    embedding_status = Column(String, index=True, default="pending")
    index_version = Column(String, index=True, default="")
    source_revision = Column(String, nullable=False, default="", server_default="")
    quality_score = Column(Float, default=0.0)
    trust_level = Column(String, index=True, default="medium")
    source_prior = Column(Float, default=0.5)
    meta_json = Column(Text, default="{}")
    indexed_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    deleted_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "source_id",
            "source_sub_id",
            "index_version",
            name="uq_semantic_source_sub_version",
        ),
        Index(
            "idx_semantic_item_source_revision_v2",
            "source_type",
            "source_id",
            "source_revision",
            "status",
        ),
    )


class SemanticIndexJob(Base):
    """语义索引异步任务。"""

    __tablename__ = "semantic_index_jobs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    source_type = Column(String, index=True, nullable=False, default="")
    source_id = Column(String, index=True, nullable=False, default="")
    source_sub_id = Column(String, index=True, default="")
    job_type = Column(String, index=True, default="upsert")
    index_version = Column(String, index=True, default="")
    source_revision = Column(String, nullable=False, default="", server_default="")
    status = Column(String, index=True, default="pending")
    retry_count = Column(Integer, default=0)
    max_retry = Column(Integer, default=3)
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    manual_retry_count = Column(Integer, nullable=False, default=0, server_default="0")
    next_retry_at = Column(DateTime, nullable=True)
    locked_by = Column(String, default="")
    locked_at = Column(DateTime, nullable=True)
    lease_token = Column(String(64), nullable=False, default="", server_default="")
    lease_expires_at = Column(DateTime, nullable=True)
    error = Column(Text, default="")
    meta_json = Column(Text, nullable=False, default="{}", server_default="{}")
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index(
            "idx_semantic_job_claim_v2",
            "status",
            "next_retry_at",
            "id",
        ),
        Index(
            "idx_semantic_job_lease_v2",
            "status",
            "lease_expires_at",
            "id",
        ),
        Index(
            "idx_semantic_job_source_revision_v2",
            "source_type",
            "source_id",
            "index_version",
            "source_revision",
            "status",
        ),
    )


class RagDebugRun(Base):
    """RAG 调试运行记录。"""

    __tablename__ = "rag_debug_runs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    trace_id = Column(String, index=True, default="")
    source_type = Column(String, index=True, default="")
    query = Column(Text, default="")
    request_json = Column(Text, default="{}")
    response_json = Column(Text, default="{}")
    degraded = Column(Integer, index=True, default=0)
    fallback_reason = Column(Text, default="")
    latency_ms = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now, index=True)


def init_db():
    db_path = sqlite_path_from_database_url(DATABASE_URL)
    if db_path:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    else:
        os.makedirs(DB_DIR, exist_ok=True)

    from core.schema_migrations import run_schema_migrations

    run_schema_migrations(engine, db_path=db_path)
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
