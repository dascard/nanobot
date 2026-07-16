import os
from datetime import datetime

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
from sqlalchemy.orm import declarative_base, sessionmaker

from config import DATABASE_URL
from core.outbound_delivery_schema import (
    OUTBOUND_CIRCUIT_CHECKS,
    OUTBOUND_CONTROL_CHECKS,
    OUTBOUND_DELIVERY_ATTEMPT_CHECKS,
    OUTBOUND_GENERATION_ATTEMPT_CHECKS,
    OUTBOUND_OUTBOX_CHECKS,
    OUTBOUND_RUN_CHECKS,
    SCHEDULED_TASK_ERROR_SUMMARY_CHECK,
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
        return max(1000, int(float(os.environ.get("SQLITE_BUSY_TIMEOUT_MS", "1000"))))
    except (TypeError, ValueError):
        return 1000


def sqlite_connect_args_for_url(database_url: str) -> dict:
    if not _is_sqlite_database_url(database_url):
        return {}
    return {
        "check_same_thread": False,
        "timeout": _sqlite_busy_timeout_ms() / 1000.0,
    }


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
        if pending_count:
            if logger is not None:
                logger.warning(
                    "[DB] skip releasing session transaction label=%s pending=%d new=%d dirty=%d deleted=%d",
                    label or "unknown",
                    pending_count,
                    new_count,
                    dirty_count,
                    deleted_count,
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

Base = declarative_base()


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


class User(Base):
    """用户/群聊统一实体。

    id 支持三种格式:
      - "0000000000"     QQ 用户（提交日志或私聊时自动注册）
      - "group_1027790249" 群聊（ambient log 首次收到时自动注册）
      - 不再创建 "private_xxx" 格式（proxy_chat 只注册 user_id）

    name 由消息入口自动刷新:
      - 群名: submit_ambient_log 从 session_name 更新
      - 用户名: proxy_chat 从 sender_name 更新
    """
    __tablename__ = "users"
    id = Column(String, primary_key=True, index=True)
    name = Column(String, default="")           # 群名或用户名（由消息入口自动刷新）
    history_clear_at = Column(DateTime, nullable=True)  # 清除标记
    created_at = Column(DateTime, default=datetime.now)


class Persona(Base):
    __tablename__ = "personas"
    user_id = Column(String, primary_key=True, index=True)
    persona_json = Column(Text, default="{}")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class SystemPrompt(Base):
    __tablename__ = "system_prompts"
    user_id = Column(String, primary_key=True, index=True)
    prompt_text = Column(Text, default="")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ChatLog(Base):
    __tablename__ = "chat_logs"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String, index=True)  # 物理发件人 ID (QQ号)
    session_id = Column(String, index=True)  # 场景 ID (群号/私聊号)
    sender_name = Column(String, nullable=True)  # 发件人昵称/名片
    session_name = Column(String, nullable=True)  # 场景名 (群名/私聊对象名)
    role = Column(String)  # 'user', 'model', or 'ambient'
    content = Column(Text)
    processed = Column(
        Integer, default=0
    )  # 0: unprocessed, 1: processed by evolution task
    created_at = Column(DateTime, default=datetime.now)
    message_id = Column(String, nullable=True)          # QQ 原始消息 ID（去重用）
    source_message_ids_json = Column(Text, default="[]")  # 合并消息源 ID 列表
    meta_json = Column(Text, default="{}")               # 通用元信息


class ConversationTurn(Base):
    """对话上下文专用表——仅存 user/assistant 消息，不含工具噪声和 ambient 消息。
    与 ChatLog 分离：ChatLog 是原始存档（进化素材），本表是精简上下文（历史注入）。
    """

    __tablename__ = "conversation_turns"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String, index=True)
    session_id = Column(String, index=True)
    role = Column(String)  # 'user' | 'assistant'（tool 结果已合并到 assistant）
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    source_message_ids_json = Column(Text, default="[]")  # 合并消息的源 ID 列表
    meta_json = Column(Text, default="{}")


class InboundMessageClaim(Base):
    """入站消息幂等 claim。"""

    __tablename__ = "inbound_message_claims"
    __table_args__ = (
        CheckConstraint(
            "status IN ('processing', 'completed', 'failed')",
            name="ck_inbound_message_claim_status",
        ),
        CheckConstraint(
            "attempt_count >= 1",
            name="ck_inbound_message_claim_attempt_count",
        ),
        Index(
            "uq_inbound_message_claim_identity",
            "platform",
            "chat_type",
            "session_id",
            "message_id",
            unique=True,
        ),
        Index(
            "ix_inbound_message_claim_status_lease",
            "status",
            "lease_expires_at",
        ),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False)
    chat_type = Column(String(16), nullable=False)
    session_id = Column(String(255), nullable=False)
    message_id = Column(String(255), nullable=False)
    status = Column(
        String(16),
        nullable=False,
        default="processing",
        server_default=text("'processing'"),
    )
    owner_token = Column(String(64), nullable=False)
    lease_expires_at = Column(DateTime, nullable=True)
    response_json = Column(Text, nullable=False, default="", server_default=text("''"))
    error_summary = Column(Text, nullable=False, default="", server_default=text("''"))
    attempt_count = Column(Integer, nullable=False, default=1, server_default=text("1"))
    created_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    completed_at = Column(DateTime, nullable=True)


class ChatDeliveryOutbox(Base):
    """私聊断连后的持久投递任务。"""

    __tablename__ = "chat_delivery_outbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'sending', 'ambiguous', 'delivered', 'failed')",
            name="ck_chat_delivery_outbox_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_chat_delivery_outbox_attempt_count",
        ),
        Index(
            "uq_chat_delivery_outbox_delivery_key",
            "delivery_key",
            unique=True,
        ),
        Index(
            "uq_chat_delivery_outbox_claim_identity",
            "platform",
            "chat_type",
            "session_id",
            "message_id",
            unique=True,
        ),
        Index(
            "ix_chat_delivery_outbox_due",
            "status",
            "next_attempt_at",
        ),
        Index(
            "ix_chat_delivery_outbox_status_lease",
            "status",
            "lease_expires_at",
        ),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    delivery_key = Column(String(64), nullable=False)
    platform = Column(String(32), nullable=False)
    chat_type = Column(String(16), nullable=False)
    session_id = Column(String(255), nullable=False)
    message_id = Column(String(255), nullable=False)
    target_type = Column(String(16), nullable=False)
    target_id = Column(String(255), nullable=False)
    envelope_json = Column(
        Text,
        nullable=False,
        default="{}",
        server_default=text("'{}'"),
    )
    status = Column(
        String(16),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    owner_token = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    lease_expires_at = Column(DateTime, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    next_attempt_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=False, default="", server_default=text("''"))
    created_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    delivered_at = Column(DateTime, nullable=True)


class ProactiveOutreachLease(Base):
    """主动外呼按用户串行评估的短期租约。"""

    __tablename__ = "proactive_outreach_leases"
    __table_args__ = (
        Index(
            "ix_proactive_outreach_lease_expires_at",
            "lease_expires_at",
        ),
    )

    user_id = Column(String(255), primary_key=True)
    owner_token = Column(String(64), nullable=False)
    lease_expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class RollingSessionSummary(Base):
    """当前 session 的滚动上下文摘要。

    只覆盖已被 recent raw ConversationTurn window 挤出的旧上下文，不承载
    daily digest、persona 或 group memory 语义。
    """

    __tablename__ = "rolling_session_summaries"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    session_id = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, default="")
    chat_type = Column(String, index=True, default="private")

    status = Column(String, index=True, default="active")
    summary_kind = Column(String, index=True, default="deterministic_fallback")
    summary_text = Column(Text, default="")
    summary_json = Column(Text, default="{}")

    covered_from_turn_id = Column(Integer, default=0)
    covered_until_turn_id = Column(Integer, index=True, default=0)
    source_turn_ids_json = Column(Text, default="[]")
    source_turn_count = Column(Integer, default=0)
    source_token_estimate = Column(Integer, default=0)
    source_char_count = Column(Integer, default=0)

    raw_window_start_turn_id = Column(Integer, default=0)
    quality_score = Column(Float, default=0.0)
    issues_json = Column(Text, default="[]")

    model = Column(String, default="")
    prompt_sha256 = Column(String, default="")
    llm_status = Column(String, index=True, default="")
    llm_model = Column(String, default="")
    llm_request_log_id = Column(Integer, nullable=True)
    llm_error = Column(Text, default="")
    retry_count = Column(Integer, default=0)
    next_retry_at = Column(DateTime, nullable=True)
    supersedes_summary_id = Column(Integer, nullable=True)
    stable_hash = Column(String, index=True, default="")
    meta_json = Column(Text, default="{}")

    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class SessionSummaryJob(Base):
    """异步 LLM session summary 生成任务。

    主请求只创建 pending job；后台 worker 后续消费并生成高质量 llm summary。
    """

    __tablename__ = "session_summary_jobs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    session_id = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, default="")
    chat_type = Column(String, index=True, default="private")

    covered_from_turn_id = Column(Integer, index=True, default=0)
    covered_until_turn_id = Column(Integer, index=True, default=0)
    source_turn_ids_json = Column(Text, default="[]")

    previous_summary_id = Column(Integer, nullable=True)
    fallback_summary_id = Column(Integer, nullable=True)
    result_summary_id = Column(Integer, nullable=True)

    status = Column(String, index=True, default="pending")
    retry_count = Column(Integer, default=0)
    max_retry = Column(Integer, default=3)
    next_retry_at = Column(DateTime, nullable=True)
    locked_by = Column(String, default="")
    locked_at = Column(DateTime, nullable=True)
    error = Column(Text, default="")
    stable_hash = Column(String, index=True, default="")
    meta_json = Column(Text, default="{}")

    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class MemoryDigest(Base):
    __tablename__ = "memory_digests"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String, index=True)
    session_id = Column(String, index=True)
    digest_date = Column(String, index=True)  # YYYY-MM-DD
    level = Column(Integer, default=0, index=True)  # 0=rich, 1=summary, 2=compact
    parent_id = Column(Integer, nullable=True)
    content = Column(Text)
    meta_json = Column(Text, default="{}")
    source_start_log_id = Column(Integer, nullable=True)
    source_end_log_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String, index=True)  # 任务名
    cron_expr = Column(String)  # cron: "0 9 * * *" (分 时 日 月 周)
    target_type = Column(String, default="private")  # private | group
    target_id = Column(String)  # QQ号 或 群号
    prompt_template = Column(Text)  # 传给 LLM 的提示模板
    enabled = Column(Integer, default=1)
    last_run_at = Column(DateTime, nullable=True)
    last_attempt_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    delivery_status = Column(
        String(48),
        nullable=False,
        default="legacy_unknown",
        server_default=text("'legacy_unknown'"),
    )
    last_error_summary = Column(
        Text,
        CheckConstraint(
            SCHEDULED_TASK_ERROR_SUMMARY_CHECK[1],
            name=SCHEDULED_TASK_ERROR_SUMMARY_CHECK[0],
        ),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    last_run_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("ix_scheduled_tasks_last_run_id", "last_run_id"),
    )


class ProactiveOutreachLog(Base):
    """主动情感外呼记录，用于幂等投递和调度审计。"""

    __tablename__ = "proactive_outreach_log"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String, index=True)
    idempotency_key = Column(String, unique=True, index=True)
    grounding_json = Column(Text, default="{}")
    judge_should = Column(Boolean, default=False)
    judge_reason = Column(Text, default="")
    next_check_at = Column(DateTime, nullable=True)
    next_intent = Column(Text, default="")
    message = Column(Text, default="")
    status = Column(String, index=True, default="pending")
    forced = Column(Boolean, default=False)
    outbound_run_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        Index(
            "ix_proactive_outreach_log_outbound_run_id",
            "outbound_run_id",
        ),
    )


def _outbound_checks(items):
    return tuple(CheckConstraint(expression, name=name) for name, expression in items)


class OutboundRun(Base):
    """一次确定 occurrence 的生成与投递运行。"""

    __tablename__ = "outbound_runs"
    __table_args__ = (
        *_outbound_checks(OUTBOUND_RUN_CHECKS),
        Index(
            "uq_outbound_run_occurrence",
            "source_type",
            "source_id",
            "occurrence_key",
            unique=True,
        ),
        Index(
            "ix_outbound_run_source",
            "source_type",
            "source_id",
            "status",
        ),
        Index(
            "ix_outbound_run_claim_lease",
            "status",
            "claim_expires_at",
        ),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(String(32), nullable=False)
    source_id = Column(String(255), nullable=False)
    occurrence_key = Column(String(255), nullable=False)
    source_revision = Column(String(128), nullable=False)
    source_snapshot_json = Column(Text, nullable=False)
    source_snapshot_sha256 = Column(String(64), nullable=False)
    delivery_contract_json = Column(Text, nullable=False)
    delivery_contract_sha256 = Column(String(64), nullable=False)
    writer_owner = Column(String(128), nullable=False)
    writer_token = Column(String(64), nullable=False)
    writer_protocol_version = Column(Integer, nullable=False)
    task_kind = Column(String(64), nullable=False)
    scheduled_for = Column(DateTime, nullable=True)
    trigger_type = Column(String(32), nullable=False)
    status = Column(
        String(48),
        nullable=False,
        default="claimed",
        server_default=text("'claimed'"),
    )
    claim_owner = Column(String(128), nullable=True)
    claim_token = Column(String(64), nullable=True)
    claim_expires_at = Column(DateTime, nullable=True)
    attempted_at = Column(DateTime, nullable=True)
    generated_at = Column(DateTime, nullable=True)
    succeeded_at = Column(DateTime, nullable=True)
    failure_type = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    failure_summary = Column(
        Text,
        nullable=False,
        default="",
        server_default=text("''"),
    )
    active_outbox_id = Column(Integer, nullable=True)
    has_ambiguous_ancestor = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("0"),
    )
    delivery_mode = Column(String(24), nullable=False)
    cutover_epoch = Column(Integer, nullable=False)
    created_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class OutboundGenerationAttempt(Base):
    """一次不可变的正文生成模型调用记录。"""

    __tablename__ = "outbound_generation_attempts"
    __table_args__ = (
        *_outbound_checks(OUTBOUND_GENERATION_ATTEMPT_CHECKS),
        Index(
            "uq_outbound_generation_attempt",
            "run_id",
            "attempt_no",
            unique=True,
        ),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("outbound_runs.id"), nullable=False)
    attempt_no = Column(Integer, nullable=False)
    owner = Column(String(128), nullable=False)
    fencing_token = Column(String(64), nullable=False)
    status = Column(
        String(32),
        nullable=False,
        default="started",
        server_default=text("'started'"),
    )
    started_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    completed_at = Column(DateTime, nullable=True)
    model_trace_id = Column(
        String(128), nullable=False, default="", server_default=text("''")
    )
    content_sha256 = Column(
        String(64), nullable=False, default="", server_default=text("''")
    )
    error_type = Column(
        String(64), nullable=False, default="", server_default=text("''")
    )
    error_summary = Column(
        Text, nullable=False, default="", server_default=text("''")
    )
    created_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class OutboundDeliveryOutbox(Base):
    """不可变 payload 与目标快照的通用主动投递队列。"""

    __tablename__ = "outbound_delivery_outbox"
    __table_args__ = (
        *_outbound_checks(OUTBOUND_OUTBOX_CHECKS),
        Index(
            "uq_outbound_delivery_idempotency_key",
            "idempotency_key",
            unique=True,
        ),
        Index(
            "uq_outbound_delivery_replay_leaf",
            "run_id",
            "destination_fingerprint",
            "replay_sequence",
            unique=True,
        ),
        Index(
            "ix_outbound_delivery_due",
            "status",
            "next_attempt_at",
        ),
        Index(
            "ix_outbound_delivery_lease",
            "status",
            "lease_expires_at",
        ),
        Index(
            "ix_outbound_delivery_run_status",
            "run_id",
            "status",
        ),
        Index(
            "ix_outbound_delivery_replay_parent",
            "replay_of_outbox_id",
        ),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("outbound_runs.id"), nullable=False)
    idempotency_key = Column(String(255), nullable=False)
    destination_snapshot_json = Column(Text, nullable=False)
    destination_fingerprint = Column(String(64), nullable=False)
    target_type = Column(String(16), nullable=False)
    endpoint_key = Column(String(64), nullable=False)
    payload_json = Column(Text, nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    status = Column(
        String(24),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    lease_owner = Column(String(128), nullable=True)
    lease_token = Column(String(64), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    next_attempt_at = Column(DateTime, nullable=True)
    allocated_attempt_count = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    request_started_count = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts = Column(Integer, nullable=False)
    retry_deadline_at = Column(DateTime, nullable=False)
    last_error_type = Column(
        String(64), nullable=False, default="", server_default=text("''")
    )
    last_error_summary = Column(
        Text, nullable=False, default="", server_default=text("''")
    )
    delivered_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    cancel_reason_type = Column(String(64), nullable=True)
    replay_of_outbox_id = Column(
        Integer,
        ForeignKey("outbound_delivery_outbox.id"),
        nullable=True,
    )
    replay_sequence = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    replay_request_sha256 = Column(
        String(64), nullable=False, default="", server_default=text("''")
    )
    cutover_epoch = Column(Integer, nullable=False)
    endpoint_config_revision = Column(String(128), nullable=False)
    payload_contract_fingerprint = Column(String(64), nullable=False)
    created_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class OutboundDeliveryAttempt(Base):
    """一次实际 HTTP 投递尝试的不可变审计记录。"""

    __tablename__ = "outbound_delivery_attempts"
    __table_args__ = (
        *_outbound_checks(OUTBOUND_DELIVERY_ATTEMPT_CHECKS),
        Index(
            "uq_outbound_delivery_attempt",
            "outbox_id",
            "attempt_no",
            unique=True,
        ),
        Index(
            "ix_outbound_delivery_attempt_status_started",
            "status",
            "started_at",
        ),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    outbox_id = Column(
        Integer,
        ForeignKey("outbound_delivery_outbox.id"),
        nullable=False,
    )
    attempt_no = Column(Integer, nullable=False)
    worker_owner = Column(String(128), nullable=False)
    lease_token = Column(String(64), nullable=False)
    status = Column(
        String(32),
        nullable=False,
        default="started",
        server_default=text("'started'"),
    )
    transport_phase = Column(
        String(32),
        nullable=False,
        default="allocated",
        server_default=text("'allocated'"),
    )
    request_started = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("0"),
    )
    endpoint_config_revision = Column(String(128), nullable=False)
    http_status = Column(Integer, nullable=True)
    result_category = Column(
        String(64), nullable=False, default="", server_default=text("''")
    )
    error_type = Column(
        String(64), nullable=False, default="", server_default=text("''")
    )
    safe_summary = Column(
        Text, nullable=False, default="", server_default=text("''")
    )
    duration_ms = Column(Integer, nullable=True)
    settlement_retry_at = Column(DateTime, nullable=True)
    settlement_circuit_scope_type = Column(String(32), nullable=True)
    settlement_request_sha256 = Column(
        String(64), nullable=False, default="", server_default=text("''")
    )
    started_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    request_started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class OutboundDeliveryCircuit(Base):
    """跨进程、按配置 revision 隔离的稳定失败熔断状态。"""

    __tablename__ = "outbound_delivery_circuits"
    __table_args__ = (
        *_outbound_checks(OUTBOUND_CIRCUIT_CHECKS),
        Index(
            "uq_outbound_delivery_circuit_scope",
            "scope_type",
            "scope_fingerprint",
            "config_revision",
            unique=True,
        ),
        Index("ix_outbound_delivery_circuit_status", "status"),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    scope_type = Column(String(32), nullable=False)
    scope_fingerprint = Column(String(64), nullable=False)
    config_revision = Column(String(128), nullable=False)
    status = Column(
        String(16),
        nullable=False,
        default="closed",
        server_default=text("'closed'"),
    )
    reason_type = Column(
        String(64), nullable=False, default="", server_default=text("''")
    )
    opened_at = Column(DateTime, nullable=True)
    opened_by_attempt_id = Column(
        Integer,
        ForeignKey("outbound_delivery_attempts.id"),
        nullable=True,
    )
    created_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class OutboundDeliveryControl(Base):
    """每个 producer source 的持久 cutover 控制行。"""

    __tablename__ = "outbound_delivery_controls"
    __table_args__ = (
        *_outbound_checks(OUTBOUND_CONTROL_CHECKS),
        Index(
            "ix_outbound_delivery_control_mode_effective",
            "mode",
            "effective_from",
        ),
    )

    source_type = Column(String(32), primary_key=True, nullable=False)
    mode = Column(
        String(24),
        nullable=False,
        default="legacy_direct",
        server_default=text("'legacy_direct'"),
    )
    cutover_epoch = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    effective_from = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    protocol_version = Column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    writer_version = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    writer_owner = Column(String(128), nullable=True)
    writer_token = Column(String(64), nullable=True)
    writer_lease_expires_at = Column(DateTime, nullable=True)
    created_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class SensitiveData(Base):
    """Qwen 判定为「否」的原始消息，单独存档，不混入 chat_logs。"""

    __tablename__ = "sensitive_data"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String, index=True)
    session_id = Column(String)
    content = Column(Text)  # 原始敏感内容
    guardrail_status = Column(String, default="silent")
    sender_name = Column(String, default="")
    session_name = Column(String, default="")
    created_at = Column(DateTime, default=datetime.now)


class PersonaFact(Base):
    """用户画像事实——LLM 提取候选后，Python 状态机去重/聚类/计数/衰减。
    一个 cluster = 一个语义等价簇，cluster 内共享 cluster_id。

    注意: source_log_ids 字段名沿袭旧命名，实际存储 evidence 文本（非 log ID 整数），
    用前需 json.loads() 解析。
    """

    __tablename__ = "persona_facts"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String, index=True, nullable=False)
    domain_primary = Column(String, default="general")
    content = Column(Text, nullable=False)  # canonical form（簇内标准表达）
    embedding = Column(LargeBinary, nullable=True)  # 本条自身的 embedding 向量
    cluster_centroid = Column(LargeBinary, nullable=True)  # 簇内均值向量（稳定锚点）
    cluster_id = Column(Integer, index=True, nullable=True)
    evidence_count = Column(Integer, default=1)
    source_log_ids = Column(Text, default="[]")  # JSON array of log IDs
    evidence_log_ids_json = Column(Text, default="[]")  # 真实 ChatLog.id 列表；旧 source_log_ids 不可信
    first_seen = Column(DateTime, nullable=True)
    last_seen = Column(DateTime, nullable=True)
    confidence = Column(String, default="可能")  # 确认/可能/待确认/归档
    fact_type = Column(String, default="preference")  # preference | behavior | trait
    memory_type = Column(String, default="stable_preference")  # stable_preference/interaction_style/...
    status = Column(String, default="review")  # review/active/disabled/archived/rejected
    inject_policy = Column(String, default="manual_only")  # auto/manual_only/never
    content_hash = Column(String, default="")
    disabled_reason = Column(Text, default="")
    rejected_reason = Column(Text, default="")
    candidate_meta_json = Column(Text, default="{}")
    last_injected_at = Column(DateTime, nullable=True)
    injected_count = Column(Integer, default=0)
    derived_from = Column(Text, default="[]")  # JSON array of behavior IDs
    contradicted_by = Column(Text, default="[]")  # JSON array of conflicting fact IDs
    created_at = Column(DateTime, default=datetime.now)


class PersonaBehavior(Base):
    """用户行为模式——可观察的重复行为，不一定是偏好。

    注意: 当前階段預留（v1 仅用了 PersonaFact），该表由 create_all 自动创建但无数据写入。
    未来将在状态机中区分 preference/behavior 写入不同表。
    """

    __tablename__ = "persona_behaviors"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String, index=True, nullable=False)
    domain_primary = Column(String, default="general")
    pattern = Column(Text, nullable=False)
    embedding = Column(LargeBinary, nullable=True)
    frequency = Column(Integer, default=1)
    source_log_ids = Column(Text, default="[]")
    last_observed = Column(DateTime, nullable=True)
    confidence = Column(String, default="可能")
    created_at = Column(DateTime, default=datetime.now)


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
    status = Column(String, index=True, default="pending")
    retry_count = Column(Integer, default=0)
    max_retry = Column(Integer, default=3)
    next_retry_at = Column(DateTime, nullable=True)
    locked_by = Column(String, default="")
    locked_at = Column(DateTime, nullable=True)
    error = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    finished_at = Column(DateTime, nullable=True)


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
