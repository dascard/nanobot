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
    __tablename__ = "users"
    id = Column(String, primary_key=True, index=True)
    history_clear_at = Column(
        DateTime, nullable=True
    )  # 清除标记：查询只取此时间之后的消息
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


class GroupName(Base):
    """群名映射——单独存储，改名时只写一行而非全部 chat_logs 回写。"""
    __tablename__ = "group_names"
    group_id = Column(String, primary_key=True, index=True)
    name = Column(String, default="")
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
    existing_columns = [col["name"] for col in inspector.get_columns("chat_logs")]
    with engine.connect() as conn:
        for col_name, col_type in allowed_migrations.items():
            if col_name not in existing_columns:
                print(
                    f"  → Migrating: Adding missing column [{col_name}] to chat_logs..."
                )
                try:
                    conn.execute(
                        text(f"ALTER TABLE chat_logs ADD COLUMN {col_name} {col_type}")
                    )
                    conn.commit()
                except Exception as e:
                    print(f"  ⚠ Migration failed for {col_name}: {e}")

    user_columns = [col["name"] for col in inspector.get_columns("users")]
    if "history_clear_at" not in user_columns:
        print("  → Migrating: Adding missing column [history_clear_at] to users...")
        try:
            with engine.connect() as conn:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN history_clear_at TIMESTAMP")
                )
                conn.commit()
        except Exception as e:
            print(f"  ⚠ Migration failed for history_clear_at: {e}")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
