"""ExpressionMemory / JargonMemory / ChatStreamConfig CRUD。"""

import logging
import json
from datetime import datetime

from sqlalchemy import and_
from core.database import SessionLocal, ExpressionMemory, JargonMemory, ChatStreamConfig

logger = logging.getLogger("nanobot.expression")


# ── ExpressionMemory ──

def upsert_expression(chat_stream_id: str, expr: str, *, expr_type: str = "phrase",
                      scene: str = "", source_count: int = 1) -> str:
    db = SessionLocal()
    try:
        existing = db.query(ExpressionMemory).filter(
            and_(ExpressionMemory.chat_stream_id == chat_stream_id,
                 ExpressionMemory.expression == expr)
        ).first()
        if existing:
            existing.source_count = (existing.source_count or 1) + source_count
            existing.last_seen = datetime.now()
            existing.confidence = min(0.95, (existing.confidence or 0.5) + 0.05)
            if existing.confidence >= 0.6 and existing.status == "candidate":
                existing.status = "active"
            result = "updated"
        else:
            db.add(ExpressionMemory(
                chat_stream_id=chat_stream_id, expression=expr,
                expression_type=expr_type, scene=scene,
                confidence=0.5, source_count=source_count,
            ))
            result = "new"
        db.commit()
        return result
    except Exception as e:
        db.rollback()
        logger.warning("[Expression] upsert failed: %s", e)
        return "error"
    finally:
        db.close()


def query_active_expressions(chat_stream_id: str, scene: str = "",
                             min_confidence: float = 0.6, limit: int = 20) -> list[dict]:
    db = SessionLocal()
    try:
        q = db.query(ExpressionMemory).filter(
            ExpressionMemory.chat_stream_id == chat_stream_id,
            ExpressionMemory.status == "active",
            ExpressionMemory.confidence >= min_confidence,
        )
        if scene:
            q = q.filter(ExpressionMemory.scene == scene)
        q = q.order_by(ExpressionMemory.confidence.desc(), ExpressionMemory.last_seen.desc()).limit(limit)
        return [{"expression": e.expression, "scene": e.scene,
                 "confidence": e.confidence, "weight": e.weight} for e in q.all()]
    finally:
        db.close()


# ── JargonMemory ──

def upsert_jargon(chat_stream_id: str, term: str, *, meaning: str = "",
                  examples: list[str] | None = None) -> str:
    db = SessionLocal()
    try:
        existing = db.query(JargonMemory).filter(
            and_(JargonMemory.chat_stream_id == chat_stream_id,
                 JargonMemory.term == term)
        ).first()
        if existing:
            if meaning and meaning != existing.meaning:
                existing.meaning = meaning
            existing.last_seen = datetime.now()
            existing.confidence = min(0.95, (existing.confidence or 0.5) + 0.05)
            if existing.confidence >= 0.6 and existing.status == "candidate":
                existing.status = "active"
            result = "updated"
        else:
            db.add(JargonMemory(
                chat_stream_id=chat_stream_id, term=term, meaning=meaning,
                examples_json=json.dumps(examples or [], ensure_ascii=False),
                confidence=0.5,
            ))
            result = "new"
        db.commit()
        return result
    except Exception as e:
        db.rollback()
        logger.warning("[Jargon] upsert failed: %s", e)
        return "error"
    finally:
        db.close()


def query_active_jargon(chat_stream_id: str, min_confidence: float = 0.6,
                        limit: int = 10) -> list[dict]:
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


# ── ChatStreamConfig ──

def get_stream_config(chat_stream_id: str) -> dict:
    db = SessionLocal()
    try:
        cfg = db.query(ChatStreamConfig).filter(
            ChatStreamConfig.chat_stream_id == chat_stream_id).first()
        if cfg:
            return {"talk_value": cfg.talk_value, "use_expression": bool(cfg.use_expression),
                    "enable_expression_learning": bool(cfg.enable_expression_learning),
                    "enable_jargon_learning": bool(cfg.enable_jargon_learning),
                    "planner_smooth": cfg.planner_smooth}
        return {"talk_value": 0.5, "use_expression": True,
                "enable_expression_learning": True, "enable_jargon_learning": True,
                "planner_smooth": 3}
    finally:
        db.close()
