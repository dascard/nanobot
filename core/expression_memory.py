"""ExpressionMemory / JargonMemory / ChatStreamConfig CRUD + ID 规范化。"""

import json
import logging

from sqlalchemy import or_

from core.chat_stream_identity import resolve_chat_stream_identity
from core.database import SessionLocal, ExpressionMemory, JargonMemory, ChatStreamConfig

logger = logging.getLogger("nanobot.expression")


class LegacyGroupLearningWriteRetired(RuntimeError):
    """旧表达和黑话写入口已永久退役。"""


def _record_legacy_compatibility(compatibility_id: str) -> None:
    from core.lifecycle import record_compatibility_usage

    record_compatibility_usage(compatibility_id)


def _reject_legacy_write(compatibility_id: str) -> None:
    _record_legacy_compatibility(compatibility_id)
    raise LegacyGroupLearningWriteRetired(
        "旧表达和黑话写入口已退役，请使用群学习候选治理链"
    )


def _safe_json(raw: str, default):
    try:
        return json.loads(raw or "")
    except Exception:
        return default


def normalize_chat_stream_id(
    raw_id: str,
    chat_type: str = "group",
    platform: str = "qq",
) -> str:
    return resolve_chat_stream_identity(
        platform=platform,
        chat_type=chat_type,
        session_id=raw_id,
    ).chat_stream_id


# ── ChatStreamConfig ──

def get_stream_config(chat_stream_id: str) -> dict:
    db = SessionLocal()
    try:
        cfg = db.query(ChatStreamConfig).filter(
            ChatStreamConfig.chat_stream_id == chat_stream_id).first()
        if cfg:
            return {
                "talk_value": cfg.talk_value,
                "mentioned_bot_reply": bool(cfg.mentioned_bot_reply),
                "use_expression": bool(cfg.use_expression),
                "enable_expression_learning": bool(cfg.enable_expression_learning),
                "enable_jargon_learning": bool(cfg.enable_jargon_learning),
                "planner_smooth": cfg.planner_smooth,
                "meta_json": _safe_json(cfg.meta_json, {}),
            }
        return {
            "talk_value": 0.5, "mentioned_bot_reply": True,
            "use_expression": True, "enable_expression_learning": True,
            "enable_jargon_learning": True, "planner_smooth": 3, "meta_json": {},
        }
    finally:
        db.close()


def ensure_stream_config(chat_stream_id: str) -> str:
    db = SessionLocal()
    try:
        cfg = db.query(ChatStreamConfig).filter(
            ChatStreamConfig.chat_stream_id == chat_stream_id).first()
        if not cfg:
            db.add(ChatStreamConfig(chat_stream_id=chat_stream_id))
            db.commit()
            return "new"
        return "exists"
    finally:
        db.close()


def _ensure_stream_config(db, chat_stream_id: str):
    cfg = db.query(ChatStreamConfig).filter(
        ChatStreamConfig.chat_stream_id == chat_stream_id).first()
    if not cfg:
        cfg = ChatStreamConfig(chat_stream_id=chat_stream_id)
        db.add(cfg)
        db.flush()
    return cfg


def update_stream_config(chat_stream_id: str, **updates) -> dict | None:
    allowed = {"talk_value", "mentioned_bot_reply", "use_expression",
               "enable_expression_learning", "enable_jargon_learning", "planner_smooth"}
    updates = {k: v for k, v in updates.items() if k in allowed}
    if not updates:
        return None
    db = SessionLocal()
    try:
        cfg = _ensure_stream_config(db, chat_stream_id)
        for k, v in updates.items():
            setattr(cfg, k, int(v) if k in ("mentioned_bot_reply", "use_expression",
               "enable_expression_learning", "enable_jargon_learning") else v)
        db.commit()
        return updates
    except Exception as e:
        db.rollback()
        logger.warning("[Config] update failed: %s", e)
        return None
    finally:
        db.close()


# ── ExpressionMemory ──

def upsert_expression(chat_stream_id: str, expr: str, *, expr_type: str = "phrase",
                      scene: str = "", source_count: int = 1,
                      examples: list[str] | None = None) -> str:
    _reject_legacy_write(
        "schema.legacy_expression_memory_write"
    )


def query_active_expressions(chat_stream_id: str, scene: str = "",
                             min_confidence: float = 0.6, limit: int = 20) -> list[dict]:
    _record_legacy_compatibility(
        "schema.legacy_expression_memory_read"
    )
    db = SessionLocal()
    try:
        q = db.query(ExpressionMemory).filter(
            ExpressionMemory.chat_stream_id == chat_stream_id,
            ExpressionMemory.status == "active",
            ExpressionMemory.confidence >= min_confidence,
        )
        if scene:
            q = q.filter(or_(
                ExpressionMemory.scene == scene,
                ExpressionMemory.scene == "",
            ))
        q = q.order_by(ExpressionMemory.confidence.desc(), ExpressionMemory.last_seen.desc()).limit(limit)
        return [{"expression": e.expression, "scene": e.scene,
                 "confidence": e.confidence, "weight": e.weight} for e in q.all()]
    finally:
        db.close()


# ── JargonMemory ──

def upsert_jargon(chat_stream_id: str, term: str, *, meaning: str = "",
                  examples: list[str] | None = None) -> str:
    _reject_legacy_write(
        "schema.legacy_jargon_memory_write"
    )


def query_active_jargon(chat_stream_id: str, min_confidence: float = 0.6,
                        limit: int = 10) -> list[dict]:
    _record_legacy_compatibility(
        "schema.legacy_jargon_memory_read"
    )
    db = SessionLocal()
    try:
        q = db.query(JargonMemory).filter(
            JargonMemory.chat_stream_id == chat_stream_id,
            JargonMemory.status == "active",
            JargonMemory.confidence >= min_confidence,
        ).order_by(JargonMemory.confidence.desc()).limit(limit)
        return [{"term": e.term, "meaning": e.meaning,
                 "confidence": e.confidence} for e in q.all()]
    finally:
        db.close()


def mark_expression_checked(chat_stream_id: str, expression: str, accepted: bool = True) -> bool:
    _reject_legacy_write(
        "schema.legacy_expression_memory_write"
    )


def mark_jargon_checked(chat_stream_id: str, term: str, accepted: bool = True) -> bool:
    _reject_legacy_write(
        "schema.legacy_jargon_memory_write"
    )
