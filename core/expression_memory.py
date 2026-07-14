"""ExpressionMemory / JargonMemory / ChatStreamConfig CRUD + ID 规范化。"""

import json
import logging

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError

from core.chat_stream_identity import resolve_chat_stream_identity
from core.database import SessionLocal, ExpressionMemory, JargonMemory, ChatStreamConfig
from core.time_utils import db_now_naive

logger = logging.getLogger("nanobot.expression")


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
    db = SessionLocal()
    try:
        existing = db.query(ExpressionMemory).filter(
            and_(ExpressionMemory.chat_stream_id == chat_stream_id,
                 ExpressionMemory.expression == expr)
        ).first()
        if existing:
            existing.source_count = (existing.source_count or 1) + source_count
            existing.last_seen = db_now_naive()
            delta = min(0.12, 0.02 + 0.01 * min(source_count, 10))
            existing.confidence = min(0.95, (existing.confidence or 0.5) + delta)
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
    except IntegrityError:
        db.rollback()
        logger.debug("[Expression] concurrent insert, re-querying for update")
        try:
            existing = db.query(ExpressionMemory).filter(
                and_(ExpressionMemory.chat_stream_id == chat_stream_id,
                     ExpressionMemory.expression == expr)
            ).first()
            if existing:
                existing.source_count = (existing.source_count or 1) + source_count
                existing.last_seen = db_now_naive()
                delta = min(0.12, 0.02 + 0.01 * min(source_count, 10))
                existing.confidence = min(0.95, (existing.confidence or 0.5) + delta)
                if (existing.checked or existing.confidence >= 0.75) and existing.status == "candidate":
                    existing.status = "active"
                db.commit()
                return "updated_after_conflict"
        except Exception:
            db.rollback()
        return "error"
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
            existing.last_seen = db_now_naive()
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
    except IntegrityError:
        db.rollback()
        logger.debug("[Jargon] concurrent insert, re-querying")
        try:
            existing = db.query(JargonMemory).filter(
                and_(JargonMemory.chat_stream_id == chat_stream_id,
                     JargonMemory.term == term)
            ).first()
            if existing:
                existing.last_seen = db_now_naive()
                existing.confidence = min(0.95, (existing.confidence or 0.5) + 0.03)
                if (existing.checked or existing.confidence >= 0.75) and existing.status == "candidate":
                    existing.status = "active"
                db.commit()
                return "updated_after_conflict"
        except Exception:
            db.rollback()
        return "error"
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


# ── 审核入口 ──

def _mark_checked(table, chat_stream_id: str, field: str, value: str, accepted: bool) -> bool:
    db = SessionLocal()
    try:
        q = db.query(table).filter(
            getattr(table, "chat_stream_id") == chat_stream_id,
            getattr(table, field) == value,
        )
        row = q.first()
        if row:
            row.checked = 1
            row.status = "active" if accepted else "rejected"
            db.commit()
            return True
        return False
    except Exception as e:
        db.rollback()
        logger.warning("[Expression] mark_checked failed: %s", e)
        return False
    finally:
        db.close()


def mark_expression_checked(chat_stream_id: str, expression: str, accepted: bool = True) -> bool:
    return _mark_checked(ExpressionMemory, chat_stream_id, "expression", expression, accepted)


def mark_jargon_checked(chat_stream_id: str, term: str, accepted: bool = True) -> bool:
    return _mark_checked(JargonMemory, chat_stream_id, "term", term, accepted)


# ── Context builders ──

def build_expression_context(chat_stream_id: str, *, limit: int = 8) -> str:
    cfg = get_stream_config(chat_stream_id)
    if not cfg.get("use_expression", True):
        return ""
    expressions = query_active_expressions(chat_stream_id, limit=limit)
    if not expressions:
        return ""
    lines = ["[ExpressionContext]"]
    lines.append("以下是本群常见表达，只作为语气参考，不要强行模仿，不要每句都使用：")
    for e in expressions[:limit]:
        expr = str(e.get("expression", "")).strip()
        scene = str(e.get("scene", "")).strip()
        if not expr:
            continue
        lines.append(f"- {expr}" + (f"（场景：{scene}）" if scene else ""))
    return "\n".join(lines)


def build_jargon_context(chat_stream_id: str, *, limit: int = 8) -> str:
    jargon = query_active_jargon(chat_stream_id, limit=limit)
    if not jargon:
        return ""
    lines = ["[JargonContext]"]
    lines.append("以下是本群黑话/术语解释，仅用于理解语境：")
    for j in jargon[:limit]:
        term = str(j.get("term", "")).strip()
        meaning = str(j.get("meaning", "")).strip()
        if term and meaning:
            lines.append(f"- {term}: {meaning}")
        elif term:
            lines.append(f"- {term}")
    return "\n".join(lines)
