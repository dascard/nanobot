import os
from sqlalchemy import create_engine, Column, String, Text, DateTime, Integer
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
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
    user_id = Column(String, index=True)      # 物理发件人 ID (QQ号)
    session_id = Column(String, index=True)   # 场景 ID (群号/私聊号)
    sender_name = Column(String, nullable=True) # 发件人昵称/名片
    session_name = Column(String, nullable=True) # 场景名 (群名/私聊对象名)
    role = Column(String)  # 'user', 'model', or 'ambient'
    content = Column(Text)
    processed = Column(Integer, default=0)  # 0: unprocessed, 1: processed by evolution task
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
    name = Column(String, index=True)            # 任务名
    cron_expr = Column(String)                   # cron: "0 9 * * *" (分 时 日 月 周)
    target_type = Column(String, default="private")  # private | group
    target_id = Column(String)                   # QQ号 或 群号
    prompt_template = Column(Text)               # 传给 LLM 的提示模板
    enabled = Column(Integer, default=1)
    last_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)


def init_db():
    os.makedirs(DB_DIR, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    
    # ── 自动化热修复：处理现有表的列迁移 ──
    # 由于 Base.metadata.create_all 不会修改现有表结构，我们需要手动检查并修补
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    existing_columns = [col["name"] for col in inspector.get_columns("chat_logs")]
    
    # 需要检查并补全的元数据列
    required_upgrades = [
        ("session_id", "TEXT"),
        ("sender_name", "TEXT"),
        ("session_name", "TEXT")
    ]
    
    with engine.connect() as conn:
        for col_name, col_type in required_upgrades:
            if col_name not in existing_columns:
                print(f"  → Migrating: Adding missing column [{col_name}] to chat_logs...")
                try:
                    # SQLite 的 ALTER TABLE 语法
                    conn.execute(text(f"ALTER TABLE chat_logs ADD COLUMN {col_name} {col_type}"))
                    conn.commit()
                except Exception as e:
                    print(f"  ⚠ Migration failed for {col_name}: {e}")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
