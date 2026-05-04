"""群体记忆——写入、查询、合并、衰减。

LLM 提候选 + confidence_hint，Python 决定 NEW/UPDATE/MERGE/ARCHIVE/DECAY。
"""

import hashlib
import json
import logging
from datetime import datetime

from sqlalchemy import and_

from core.database import GroupMemory, SessionLocal

logger = logging.getLogger("nanobot.group_memory")

MEMORY_TYPES = {"topic", "slang", "relationship", "style", "event", "preference"}
CONFIDENCE_FLOOR = 0.55


def _norm_key(content: str) -> str:
    return hashlib.md5(content.strip().lower().encode()).hexdigest()[:12]


def upsert(
    group_id: str, memory_type: str, content: str,
    *, evidence_log_ids: list[int] | None = None,
    confidence_hint: float = 0.5, meta: dict | None = None,
) -> str:
    """写入或更新一条群体记忆。返回 new/updated/skipped。"""
    if memory_type not in MEMORY_TYPES:
        return "skipped"

    db = SessionLocal()
    try:
        key = _norm_key(content)
        existing = (
            db.query(GroupMemory)
            .filter(and_(GroupMemory.group_id == group_id,
                         GroupMemory.memory_type == memory_type))
            .all()
        )
        for m in existing:
            if _norm_key(m.content) == key:
                m.evidence_count += 1
                m.last_seen = datetime.now()
                m.confidence = min(1.0, m.confidence + confidence_hint * 0.1)
                m.decay_score = min(1.0, m.decay_score + 0.05)
                if evidence_log_ids:
                    _merge_evidence(m, evidence_log_ids)
                if meta:
                    m.meta_json = json.dumps(
                        {**_safe_meta(m.meta_json), **meta}, ensure_ascii=False)
                db.commit()
                return "updated"

        entry = GroupMemory(
            group_id=group_id, memory_type=memory_type,
            content=content.strip(),
            evidence_log_ids_json=json.dumps(evidence_log_ids or []),
            confidence=confidence_hint, evidence_count=1,
            first_seen=datetime.now(), last_seen=datetime.now(),
            decay_score=1.0, status="active",
            meta_json=json.dumps(meta or {}, ensure_ascii=False),
        )
        db.add(entry)
        db.commit()
        return "new"
    except Exception as e:
        db.rollback()
        logger.warning("[group_memory] upsert failed: %s", e)
        return "skipped"
    finally:
        db.close()


def query_active(
    group_id: str, *, memory_type: str | None = None,
    min_confidence: float = 0.5, limit: int = 20,
) -> list[dict]:
    """查询活跃记忆——用于注入 GroupProfile。"""
    db = SessionLocal()
    try:
        q = db.query(GroupMemory).filter(and_(
            GroupMemory.group_id == group_id,
            GroupMemory.status == "active",
            GroupMemory.confidence >= min_confidence,
        ))
        if memory_type:
            q = q.filter(GroupMemory.memory_type == memory_type)
        rows = (
            q.order_by(GroupMemory.confidence.desc(),
                       GroupMemory.last_seen.desc())
            .limit(limit).all()
        )
        return [_row_to_dict(r) for r in rows]
    finally:
        db.close()


def apply_decay(group_id: str):
    """对活跃记忆降 decay_score。<0.2 → archived。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(GroupMemory)
            .filter(and_(GroupMemory.group_id == group_id,
                         GroupMemory.status == "active"))
            .all()
        )
        for r in rows:
            if r.decay_score <= 0.2:
                r.status = "archived"
            else:
                r.decay_score = max(0.0, r.decay_score - 0.02)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("[group_memory] decay failed: %s", e)
    finally:
        db.close()


def build_profile(group_id: str) -> dict:
    """从 GroupMemory 动态生成 GroupProfile JSON。"""
    all_mem = query_active(group_id, min_confidence=0.7)
    by_type: dict[str, list[dict]] = {}
    for m in all_mem:
        by_type.setdefault(m["memory_type"], []).append(m)

    def _top(kind: str, n: int) -> list[str]:
        return [m["content"] for m in by_type.get(kind, [])[:n]]

    return {
        "common_topics": _top("topic", 5),
        "slang": {m["content"]: _safe_meta(m["meta_json"]).get("meaning", "")
                  for m in by_type.get("slang", [])[:8]},
        "style": _top("style", 5),
        "events": _top("event", 3),
        "bot_preferences": _top("preference", 3),
    }


def _merge_evidence(memory, new_ids: list[int]):
    try:
        existing = json.loads(memory.evidence_log_ids_json or "[]")
        merged = list(set(existing + new_ids))[:50]
        memory.evidence_log_ids_json = json.dumps(merged)
    except (json.JSONDecodeError, TypeError):
        memory.evidence_log_ids_json = json.dumps(new_ids[:50])


def _safe_meta(raw: str) -> dict:
    try:
        d = json.loads(raw or "{}")
        return d if isinstance(d, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _row_to_dict(r: GroupMemory) -> dict:
    return {
        "id": r.id, "group_id": r.group_id,
        "memory_type": r.memory_type, "content": r.content,
        "confidence": r.confidence, "evidence_count": r.evidence_count,
        "decay_score": r.decay_score,
        "first_seen": r.first_seen.strftime("%Y-%m-%d") if r.first_seen else "",
        "last_seen": r.last_seen.strftime("%Y-%m-%d") if r.last_seen else "",
        "status": r.status, "meta_json": r.meta_json,
    }
