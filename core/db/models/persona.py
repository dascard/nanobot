"""用户画像与旧 Prompt 历史模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, LargeBinary, String, Text, text

from core.db.base import Base


class Persona(Base):
    __tablename__ = "personas"
    user_id = Column(String, primary_key=True, index=True)
    persona_json = Column(Text, default="{}")
    status = Column(
        String,
        index=True,
        nullable=False,
        default="active",
        server_default=text("'active'"),
    )
    archive_meta_json = Column(
        Text,
        nullable=False,
        default="{}",
        server_default=text("'{}'"),
    )
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class SystemPrompt(Base):
    """历史兼容记录；canonical Prompt Runtime 不读取该表。"""

    __tablename__ = "system_prompts"
    user_id = Column(String, primary_key=True, index=True)
    prompt_text = Column(Text, default="")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class PersonaFact(Base):
    """经 Python 状态机审核的用户画像事实。"""

    __tablename__ = "persona_facts"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String, index=True, nullable=False)
    domain_primary = Column(String, default="general")
    content = Column(Text, nullable=False)
    embedding = Column(LargeBinary, nullable=True)
    cluster_centroid = Column(LargeBinary, nullable=True)
    cluster_id = Column(Integer, index=True, nullable=True)
    evidence_count = Column(Integer, default=1)
    source_log_ids = Column(Text, default="[]")
    evidence_log_ids_json = Column(Text, default="[]")
    first_seen = Column(DateTime, nullable=True)
    last_seen = Column(DateTime, nullable=True)
    confidence = Column(String, default="可能")
    fact_type = Column(String, default="preference")
    memory_type = Column(String, default="stable_preference")
    status = Column(String, default="review")
    inject_policy = Column(String, default="manual_only")
    content_hash = Column(String, default="")
    disabled_reason = Column(Text, default="")
    rejected_reason = Column(Text, default="")
    candidate_meta_json = Column(Text, default="{}")
    last_injected_at = Column(DateTime, nullable=True)
    injected_count = Column(Integer, default=0)
    derived_from = Column(Text, default="[]")
    contradicted_by = Column(Text, default="[]")
    created_at = Column(DateTime, default=datetime.now)


class PersonaBehavior(Base):
    """用户可观察的重复行为模式。"""

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
    status = Column(
        String,
        index=True,
        nullable=False,
        default="active",
        server_default=text("'active'"),
    )
    archive_meta_json = Column(
        Text,
        nullable=False,
        default="{}",
        server_default=text("'{}'"),
    )
    created_at = Column(DateTime, default=datetime.now)


__all__ = ["Persona", "PersonaBehavior", "PersonaFact", "SystemPrompt"]
