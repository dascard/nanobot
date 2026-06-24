"""DB 采样——从运行 DB 增量抽取候选 eval case。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta


def _safe_json(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def sample_chatlog_replies(db, *, after_id: int = 0, limit: int = 50) -> list[dict]:
    """从 ChatLog 抽取 bot 回复样本（group_reply suite）。
    游标追踪：after_id 是上次最大 ChatLog.id，每次取 id > after_id 的记录。
    """
    from core.database import ChatLog

    rows = (
        db.query(ChatLog)
        .filter(ChatLog.role == "assistant", ChatLog.id > after_id)
        .order_by(ChatLog.id.asc())
        .limit(limit * 3)
        .all()
    )

    candidates: list[dict] = []
    for r in rows:
        meta = _safe_json(r.meta_json)
        if meta.get("kind") != "group_reply":
            continue
        group_id = str(r.session_id or "").removeprefix("group_")
        if not group_id:
            continue
        if len(candidates) >= limit:
            break

        created = r.created_at
        context_rows = (
            db.query(ChatLog)
            .filter(
                ChatLog.session_id == r.session_id,
                ChatLog.created_at.between(
                    created - timedelta(minutes=2),
                    created + timedelta(seconds=30),
                ),
            )
            .order_by(ChatLog.created_at)
            .limit(20)
            .all()
        )
        context = [
            {"role": cr.role, "content": (cr.content or "")[:200], "sender_name": cr.sender_name or ""}
            for cr in context_rows
        ]

        case_id = f"cand_group_reply_{r.id}"
        input_data = {
            "group_id": group_id,
            "bot_reply": (r.content or "")[:300],
            "reply_meta": meta.get("reply_meta"),
            "context": context,
        }
        fingerprint_raw = f"group_reply||{r.content or ''}"[:200]
        fingerprint = hashlib.sha256(fingerprint_raw.encode("utf-8")).hexdigest()[:16]

        candidates.append({
            "case_id": case_id,
            "suite": "group_reply",
            "source": "db",
            "source_ref": f"chatlog:{r.id}",
            "description": f"群 {group_id} bot 回复 #{r.id}",
            "input": input_data,
            "expected": {"needs_label": True},
            "tags": ["sampled", "group_reply"],
            "fingerprint": fingerprint,
        })
    return candidates


def sample_timing_events(db, *, after_id: int = 0, limit: int = 50) -> list[dict]:
    """从 ChatLog.meta_json 抽取 TimingGate 判定样本。
    游标追踪：after_id 是上次最大 ChatLog.id。
    """
    from core.database import ChatLog

    rows = (
        db.query(ChatLog)
        .filter(ChatLog.role == "ambient", ChatLog.id > after_id)
        .order_by(ChatLog.id.asc())
        .limit(limit * 5)
        .all()
    )

    candidates: list[dict] = []
    for r in rows:
        meta = _safe_json(r.meta_json)
        tg = meta.get("timing_gate")
        if not tg:
            continue
        if len(candidates) >= limit:
            break

        case_id = f"cand_timing_gate_{r.id}"
        input_data = {
            "group_id": str(r.session_id or "").removeprefix("group_"),
            "action": tg.get("action", ""),
            "trigger_reason": tg.get("reason", ""),
            "generation": tg.get("generation", 0),
            "latency_ms": tg.get("latency_ms", 0),
            "pending_count": tg.get("pending_count", 0),
            "talk_value": tg.get("talk_value"),
            "message": (r.content or "")[:200],
        }
        fingerprint_raw = f"timing_gate|{tg.get('action','')}|{tg.get('reason','')}"[:200]
        fingerprint = hashlib.sha256(fingerprint_raw.encode("utf-8")).hexdigest()[:16]

        candidates.append({
            "case_id": case_id,
            "suite": "timing_gate",
            "source": "db",
            "source_ref": f"chatlog:{r.id}",
            "description": f"action={tg.get('action', '')} reason={tg.get('reason', '')}",
            "input": input_data,
            "expected": {"needs_label": True},
            "tags": ["sampled", "timing_gate"],
            "fingerprint": fingerprint,
        })
    return candidates


def sample_memory_learning(db, *, after_latest: int = 0, limit: int = 50,
                           table: str = "all") -> list[dict]:
    """从 JargonMemory / ExpressionMemory 抽取低质量候选。
    table: "jargon" / "expression" / "all"
    游标追踪：after_latest 是上次最大 id。
    """
    from core.database import JargonMemory, ExpressionMemory

    candidates: list[dict] = []
    do_jargon = table in ("all", "jargon")
    do_expr = table in ("all", "expression")

    if do_jargon:
        jargon_rows = (
            db.query(JargonMemory)
            .filter(JargonMemory.id > after_latest, JargonMemory.status == "candidate")
            .order_by(JargonMemory.id.asc())
            .limit(limit * 2)
            .all()
        )
    else:
        jargon_rows = []

    for j in jargon_rows:
        term = j.term or ""
        is_suspicious = bool(re.search(r"[×xX\*\/\=\+\-\d\.]{2,}", term))
        low_conf = (j.confidence or 1.0) < 0.6
        if not (is_suspicious or low_conf):
            continue
        if len(candidates) >= limit:
            break

        examples = (_safe_json(j.examples_json) if isinstance(j.examples_json, str)
                    else (j.examples_json or []))
        case_id = f"cand_memory_learning_j_{j.id}"
        input_data = {
            "chat_stream_id": j.chat_stream_id,
            "term": term,
            "meaning": j.meaning or "",
            "confidence": j.confidence,
            "examples": examples,
        }
        fingerprint_raw = f"memory_learning|jargon|{term}"[:200]
        fingerprint = hashlib.sha256(fingerprint_raw.encode("utf-8")).hexdigest()[:16]

        candidates.append({
            "case_id": case_id,
            "suite": "memory_learning",
            "source": "db",
            "source_ref": f"jargon:{j.id}",
            "description": f"可疑 jargon term={term} conf={j.confidence}",
            "input": input_data,
            "expected": {"needs_label": True},
            "tags": ["sampled", "memory_learning", "jargon"],
            "fingerprint": fingerprint,
        })

    if do_expr:
        expr_rows = (
            db.query(ExpressionMemory)
            .filter(ExpressionMemory.id > after_latest, ExpressionMemory.status == "candidate")
            .order_by(ExpressionMemory.id.asc())
            .limit(limit * 2)
            .all()
        )
    else:
        expr_rows = []
    for e in expr_rows:
        expr = e.expression or ""
        is_suspicious = bool(re.search(r"[×xX\*\/\=\+\-\d\.]{2,}", expr))
        low_conf = (e.confidence or 1.0) < 0.6
        if not (is_suspicious or low_conf):
            continue
        if len(candidates) >= limit:
            break

        case_id = f"cand_memory_learning_e_{e.id}"
        input_data = {
            "chat_stream_id": e.chat_stream_id,
            "expression": expr,
            "expression_type": e.expression_type or "phrase",
            "confidence": e.confidence,
        }
        fingerprint_raw = f"memory_learning|expression|{expr}"[:200]
        fingerprint = hashlib.sha256(fingerprint_raw.encode("utf-8")).hexdigest()[:16]

        candidates.append({
            "case_id": case_id,
            "suite": "memory_learning",
            "source": "db",
            "source_ref": f"expression:{e.id}",
            "description": f"可疑 expression expr={expr} conf={e.confidence}",
            "input": input_data,
            "expected": {"needs_label": True},
            "tags": ["sampled", "memory_learning", "expression"],
            "fingerprint": fingerprint,
        })

    return candidates
