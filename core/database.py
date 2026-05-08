import os
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker

from config import DATABASE_URL

# 使用绝对路径确保在 Docker 挂载时路径不飘移
DB_DIR = os.path.abspath("./data")
DB_PATH = os.path.join(DB_DIR, "nanobot.db")
# BUG-20 FIX: DATABASE_URL now imported from config.py (single source of truth)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


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
    created_at = Column(DateTime, default=datetime.now)


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
    first_seen = Column(DateTime, nullable=True)
    last_seen = Column(DateTime, nullable=True)
    confidence = Column(String, default="可能")  # 确认/可能/待确认/归档
    fact_type = Column(String, default="preference")  # preference | behavior | trait
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
    evidence_log_ids_json = Column(Text, default="[]")
    confidence = Column(Float, default=0.5)
    evidence_count = Column(Integer, default=1)
    first_seen = Column(DateTime, default=datetime.now)
    last_seen = Column(DateTime, default=datetime.now)
    decay_score = Column(Float, default=1.0)
    status = Column(String, default="active")
    meta_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.now)


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
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("chat_stream_id", "sticker_hash", name="uq_sticker_stream_hash"),
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
    planner_smooth = Column(Integer, default=3)
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


class SystemSetting(Base):
    """WebUI 系统设置——KV 存储。"""
    __tablename__ = "system_settings"

    key = Column(String, primary_key=True)
    value = Column(Text, default="")
    description = Column(Text, default="")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


def init_db():
    os.makedirs(DB_DIR, exist_ok=True)

    Base.metadata.create_all(bind=engine)

    # ── 自动化热修复：处理现有表的列迁移 ──
    # 由于 Base.metadata.create_all 不会修改现有表结构，我们需要手动检查并修补
    from sqlalchemy import inspect, text

    inspector = inspect(engine)

    # chat_logs 元数据列（白名单：仅允许已知安全列名和类型）
    allowed_migrations = {
        "session_id": "TEXT",
        "sender_name": "TEXT",
        "session_name": "TEXT",
    }
    # chat_logs v2 字段 (message_id / source_message_ids / meta)
    _chat_v2_migrations = {
        "message_id": "TEXT",
        "source_message_ids_json": "TEXT",
        "meta_json": "TEXT",
    }
    # conversation_turns v2
    existing_columns = [col["name"] for col in inspector.get_columns("chat_logs")]
    conv_cols = [col["name"] for col in inspector.get_columns("conversation_turns")]

    chat_missing = [
        (col_name, col_type)
        for col_name, col_type in {**allowed_migrations, **_chat_v2_migrations}.items()
        if col_name not in existing_columns
    ]
    conv_missing = [
        (col_name, "TEXT")
        for col_name in ("source_message_ids_json", "meta_json")
        if col_name not in conv_cols
    ]

    # 自动备份（仅在确实需要迁移时）
    if (chat_missing or conv_missing) and os.path.exists(DB_PATH):
        import shutil as _shutil
        from datetime import datetime as _dt
        backup_path = f"{DB_PATH}.bak.{_dt.now().strftime('%Y%m%d_%H%M%S')}"
        try:
            _shutil.copy2(DB_PATH, backup_path)
            # 只保留最近 5 个备份
            backups = sorted(
                [f for f in os.listdir(DB_DIR) if f.startswith("nanobot.db.bak.")],
                reverse=True,
            )
            for old in backups[5:]:
                os.remove(os.path.join(DB_DIR, old))
        except Exception as _e:
            import logging as _logging
            _logging.getLogger("nanobot").warning("DB backup/cleanup failed (migration continues): %s", _e)

    with engine.connect() as conn:
        for col_name, col_type in chat_missing:
            print(f"  → Migrating: Adding missing column [{col_name}] to chat_logs...")
            try:
                conn.execute(
                    text(f"ALTER TABLE chat_logs ADD COLUMN {col_name} {col_type}")
                )
                conn.commit()
            except Exception as e:
                print(f"  ⚠ Migration failed for {col_name}: {e}")

        for col_name, col_type in conv_missing:
            print(f"  → Migrating: Adding missing column [{col_name}] to conversation_turns...")
            try:
                conn.execute(
                    text(f"ALTER TABLE conversation_turns ADD COLUMN {col_name} {col_type}")
                )
                conn.commit()
            except Exception as e:
                print(f"  ⚠ Migration failed for {col_name}: {e}")

        if "sticker_memories" in inspector.get_table_names():
            sticker_columns = [col["name"] for col in inspector.get_columns("sticker_memories")]
            sticker_required_columns = {
                "chat_stream_id": "TEXT",
                "sticker_hash": "TEXT",
                "file_ref": "TEXT",
                "send_code": "TEXT",
                "name": "TEXT",
                "description": "TEXT",
                "tags_json": "TEXT",
                "emotions_json": "TEXT",
                "source_type": "TEXT",
                "source_count": "INTEGER DEFAULT 1",
                "status": "TEXT DEFAULT 'active'",
                "usage_count": "INTEGER DEFAULT 0",
                "first_seen": "TIMESTAMP",
                "last_seen": "TIMESTAMP",
                "last_used": "TIMESTAMP",
                "meta_json": "TEXT",
                "local_path": "TEXT",
                "preview_status": "TEXT DEFAULT 'pending'",
                "created_at": "TIMESTAMP",
            }
            for col_name, col_type in sticker_required_columns.items():
                if col_name in sticker_columns:
                    continue
                print(f"  → Migrating: Adding missing column [{col_name}] to sticker_memories...")
                try:
                    conn.execute(
                        text(f"ALTER TABLE sticker_memories ADD COLUMN {col_name} {col_type}")
                    )
                    conn.commit()
                except Exception as e:
                    print(f"  ⚠ Migration failed for sticker_memories.{col_name}: {e}")
            try:
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_sticker_stream_hash "
                    "ON sticker_memories(chat_stream_id, sticker_hash)"
                ))
                conn.commit()
            except Exception as e:
                print(f"  ⚠ Sticker index creation failed: {e}")

        # index: (session_id, message_id) for chat_logs
        try:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_cl_session_msg "
                "ON chat_logs(session_id, message_id)"
            ))
            conn.commit()
        except Exception as e:
            print(f"  ⚠ Index creation failed: {e}")

    user_columns = [col["name"] for col in inspector.get_columns("users")]
    for col_name in ["history_clear_at", "name"]:
        if col_name not in user_columns:
            col_type_map = {"history_clear_at": "TIMESTAMP", "name": "TEXT"}
            print(f"  → Migrating: Adding missing column [{col_name}] to users...")
            try:
                with engine.connect() as conn:
                    conn.execute(text(
                        f"ALTER TABLE users ADD COLUMN {col_name} {col_type_map[col_name]}"
                    ))
                    conn.commit()
            except Exception as e:
                print(f"  ⚠ Migration failed for {col_name}: {e}")



    # expression/jargon unique index migration
    try:
        from sqlalchemy import inspect, text
        inspector = inspect(engine)
        if "expression_memories" in inspector.get_table_names():
            conn = engine.connect()
            try:
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_expr_stream_expr "
                    "ON expression_memories(chat_stream_id, expression)"
                ))
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_jargon_stream_term "
                    "ON jargon_memories(chat_stream_id, term)"
                ))
                conn.commit()
            except Exception as e:
                print(f"  Warning: unique index migration skipped: {e}")
            finally:
                conn.close()
    except Exception:
        pass
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
