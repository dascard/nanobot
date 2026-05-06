"""ExpressionMemory / JargonMemory / ChatStreamConfig CRUD + ID 规范化。"""

import json
import logging
from datetime import datetime

from sqlalchemy import and_, or_
from core.database import SessionLocal, ExpressionMemory, JargonMemory, ChatStreamConfig

logger = logging.getLogger("nanobot.expression")


def normalize_chat_stream_id(raw_id: str, chat_type: str = "group", platform: str = "qq") -> str:
    raw = str(raw_id).removeprefix("group_").removeprefix("private_")
    return f"{platform}:{raw}:{chat_type}"


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
                "meta_json": json.loads(cfg.meta_json or "{}"),
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


def update_stream_config(chat_stream_id: str, **updates) -> dict | None:
    allowed = {"talk_value", "mentioned_bot_reply", "use_expression",
               "enable_expression_learning", "enable_jargon_learning", "planner_smooth"}
    updates = {k: v for k, v in updates.items() if k in allowed}
    if not updates:
        return None
    db = SessionLocal()
    try:
        ensure_stream_config(chat_stream_id)
        cfg = db.query(ChatStreamConfig).filter(
            ChatStreamConfig.chat_stream_id == chat_stream_id).first()
        if cfg:
            for k, v in updates.items():
                setattr(cfg, k, int(v) if k.startswith("enable") or k == "mentioned_bot_reply" or k == "use_expression" else v)
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
    db = SessionLocal()
    try:
        existing = db.query(ExpressionMemory).filter(
            and_(ExpressionMemory.chat_stream_id == chat_stream_id,
                 ExpressionMemory.expression == expr)
        ).first()
        if existing:
            existing.source_count = (existing.source_count or 1) + source_count
            existing.last_seen = datetime.now()
            existing.confidence = min(0.95, (existing.confidence or 0.5) + 0.03)
            # merge examples
            if examples:
                old_examples = json.loads(existing.example_json or "[]")
                for ex in (examples or []):
                    if ex not in old_examples:
                        old_examples.append(ex)
                existing.example_json = json.dumps(old_examples[:5], ensure_ascii=False)
            # checked 或高置信度才 active
            if (existing.checked or existing.confidence >= 0.75) and existing.status == "candidate":
                existing.status = "active"
            result = "updated"
        else:
            db.add(ExpressionMemory(
                chat_stream_id=chat_stream_id, expression=expr,
                expression_type=expr_type, scene=scene,
                example_json=json.dumps((examples or [])[:5], ensure_ascii=False),
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
            existing.confidence = min(0.95, (existing.confidence or 0.5) + 0.03)
            if examples:
                old_examples = json.loads(existing.examples_json or "[]")
                for ex in (examples or []):
                    if ex not in old_examples:
                        old_examples.append(ex)
                existing.examples_json = json.dumps(old_examples[:5], ensure_ascii=False)
            if (existing.checked or existing.confidence >= 0.75) and existing.status == "candidate":
                existing.status = "active"
            result = "updated"
        else:
            db.add(JargonMemory(
                chat_stream_id=chat_stream_id, term=term, meaning=meaning,
                examples_json=json.dumps((examples or [])[:5], ensure_ascii=False),
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
