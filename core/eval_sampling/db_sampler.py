"""DB 采样——从运行 DB 增量抽取候选 eval case。"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from foundation.identity import (
    ChatStreamIdentityError,
    resolve_chat_stream_identity,
)


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
        try:
            group_id = resolve_chat_stream_identity(
                platform="qq",
                chat_type="group",
                session_id=str(r.session_id or ""),
            ).external_session_id
        except ChatStreamIdentityError:
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
        try:
            group_id = resolve_chat_stream_identity(
                platform="qq",
                chat_type="group",
                session_id=str(r.session_id or ""),
            ).external_session_id
        except ChatStreamIdentityError:
            continue
        input_data = {
            "group_id": group_id,
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


def sample_memory_learning(
    db,
    *,
    after_latest: int = 0,
    limit: int = 50,
    candidate_type: str = "all",
) -> list[dict]:
    """从新群学习候选表抽取待评估表达和黑话。"""

    from core.db.models import GroupLearningCandidate

    normalized_type = str(candidate_type or "").strip()
    if normalized_type not in {"all", "expression", "slang"}:
        raise ValueError("candidate_type 必须是 all/expression/slang")
    selected_types = (
        ("expression", "slang")
        if normalized_type == "all"
        else (normalized_type,)
    )
    bounded_limit = max(1, min(int(limit), 500))
    rows = (
        db.query(GroupLearningCandidate)
        .filter(
            GroupLearningCandidate.id > max(0, int(after_latest)),
            GroupLearningCandidate.candidate_type.in_(selected_types),
            GroupLearningCandidate.status.in_((
                "raw",
                "pending_model_review",
                "waiting_for_evidence",
                "conflict",
            )),
        )
        .order_by(GroupLearningCandidate.id.asc())
        .limit(bounded_limit)
        .all()
    )

    candidates: list[dict] = []
    for row in rows:
        content = str(row.content or "")
        meaning = str(row.meaning or "")
        status = str(row.status or "")
        memory_type = str(row.candidate_type or "")
        fingerprint_raw = (
            f"memory_learning|{memory_type}|{row.fingerprint}"
        )
        fingerprint = hashlib.sha256(
            fingerprint_raw.encode("utf-8")
        ).hexdigest()[:16]
        candidates.append({
            "case_id": f"cand_memory_learning_{row.id}",
            "suite": "memory_learning",
            "source": "db",
            "source_ref": f"group_learning_candidate:{row.id}",
            "description": (
                f"群学习候选 type={memory_type} status={status}"
            ),
            "input": {
                "candidate_id": str(row.candidate_id or ""),
                "chat_stream_id": str(row.chat_stream_id or ""),
                "candidate_type": memory_type,
                "content": content,
                "meaning": meaning,
                "source": str(row.source or ""),
                "status": status,
                "rule_id": str(row.rule_id or ""),
                "hit_count": int(row.hit_count or 0),
            },
            "expected": {"needs_label": True},
            "tags": [
                "sampled",
                "memory_learning",
                memory_type,
                status,
            ],
            "fingerprint": fingerprint,
        })
    return candidates
