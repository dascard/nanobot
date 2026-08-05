"""DB 采样——从运行 DB 增量抽取候选 eval case。"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from sqlalchemy import String, cast, exists, literal

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


_PROACTIVE_EVIDENCE_STATUSES = frozenset({
    "sent",
    "sent_after_ambiguous_replay",
    "failed",
    "evaluation_error",
    "ambiguous",
    "cancelled",
    "legacy_ambiguous_hold",
})


def proactive_outreach_source_ref(log_id: int, status: str) -> str:
    return f"proactive_outreach_log:{int(log_id)}:{str(status or 'unknown')}"


def proactive_outreach_case_id(log_id: int, status: str) -> str:
    return f"cand_proactive_outreach_{int(log_id)}_{str(status or 'unknown')}"


def build_proactive_outreach_evidence_case(db, row) -> dict:
    """把主动外呼与 Outbound 事实投影成不含正文和目标身份的候选。"""

    from core.db.models import (
        OutboundDeliveryAttempt,
        OutboundDeliveryOutbox,
        OutboundRun,
    )

    status = str(row.status or "unknown")
    source_ref = proactive_outreach_source_ref(int(row.id), status)
    grounding = _safe_json(row.grounding_json)
    trigger = grounding.get("_trigger_runtime")
    trigger_evidence = {}
    if isinstance(trigger, dict):
        for key in (
            "schema_version",
            "trigger_id",
            "trigger_type",
            "source_type",
            "source_ref_sha256",
            "idempotency_sha256",
            "owner_sha256",
            "occurred_at",
            "expires_at",
            "governance_sha256",
            "trigger_sha256",
            "run_id",
        ):
            value = trigger.get(key)
            if isinstance(value, (str, int, bool, type(None))):
                trigger_evidence[key] = value

    outbound = None
    if row.outbound_run_id is not None:
        candidate = db.get(OutboundRun, int(row.outbound_run_id))
        if (
            candidate is not None
            and str(candidate.source_type or "") == "proactive_outreach"
            and str(candidate.source_id or "") == str(row.id)
        ):
            outbound = candidate
    outbox = None
    attempts = []
    if outbound is not None and outbound.active_outbox_id is not None:
        candidate_outbox = db.get(
            OutboundDeliveryOutbox,
            int(outbound.active_outbox_id),
        )
        if candidate_outbox is not None and int(candidate_outbox.run_id) == int(
            outbound.id
        ):
            outbox = candidate_outbox
            attempts = (
                db.query(OutboundDeliveryAttempt)
                .filter(OutboundDeliveryAttempt.outbox_id == int(outbox.id))
                .order_by(OutboundDeliveryAttempt.attempt_no.asc())
                .all()
            )

    message = str(row.message or "")
    judge_reason = str(row.judge_reason or "")
    input_data = {
        "outreach_log_id": int(row.id),
        "status": status,
        "forced": bool(row.forced),
        "judge_should": bool(row.judge_should),
        "message_chars": len(message),
        "message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest(),
        "judge_reason_sha256": hashlib.sha256(
            judge_reason.encode("utf-8")
        ).hexdigest(),
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "trigger": trigger_evidence,
        "outbound": {
            "run_id": int(outbound.id) if outbound is not None else None,
            "status": str(outbound.status or "") if outbound is not None else "",
            "trigger_type": (
                str(outbound.trigger_type or "") if outbound is not None else ""
            ),
            "attempt_count": (
                int(outbound.attempt_count or 0) if outbound is not None else 0
            ),
            "failure_type": (
                str(outbound.failure_type or "") if outbound is not None else ""
            ),
            "has_ambiguous_ancestor": (
                bool(outbound.has_ambiguous_ancestor)
                if outbound is not None
                else False
            ),
        },
        "delivery": {
            "outbox_id": int(outbox.id) if outbox is not None else None,
            "status": str(outbox.status or "") if outbox is not None else "",
            "attempt_count": len(attempts),
            "request_started_count": sum(
                int(bool(attempt.request_started)) for attempt in attempts
            ),
            "result_categories": sorted({
                str(attempt.result_category or "")
                for attempt in attempts
                if str(attempt.result_category or "")
            }),
            "error_types": sorted({
                str(attempt.error_type or "")
                for attempt in attempts
                if str(attempt.error_type or "")
            }),
            "delivered": bool(
                outbox is not None and outbox.delivered_at is not None
            ),
        },
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            input_data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "case_id": proactive_outreach_case_id(int(row.id), status),
        "suite": "proactive_outreach",
        "source": "db",
        "source_ref": source_ref,
        "description": f"主动外呼 #{row.id} status={status}",
        "input": input_data,
        "expected": {"needs_label": True},
        "tags": ["sampled", "proactive_outreach", status],
        "fingerprint": fingerprint,
    }


def sample_proactive_outreach_evidence(
    db,
    *,
    limit: int = 50,
) -> list[dict]:
    """抽取尚未进入 EvalCandidate 的终态主动外呼证据。

    不使用单调游标：旧 pending 行可能在游标前方变成终态，按 source_ref
    缺失查询才能在状态最终落定后补采。
    """

    from core.db.models import EvalCandidate, ProactiveOutreachLog

    bounded_limit = max(1, min(int(limit), 500))
    case_id_expression = (
        literal("cand_proactive_outreach_")
        + cast(ProactiveOutreachLog.id, String)
        + literal("_")
        + ProactiveOutreachLog.status
    )
    rows = (
        db.query(ProactiveOutreachLog)
        .filter(
            ProactiveOutreachLog.status.in_(_PROACTIVE_EVIDENCE_STATUSES),
            ~exists().where(EvalCandidate.case_id == case_id_expression),
        )
        .order_by(ProactiveOutreachLog.id.asc())
        .limit(bounded_limit)
        .all()
    )
    return [build_proactive_outreach_evidence_case(db, row) for row in rows]


__all__ = [
    "build_proactive_outreach_evidence_case",
    "proactive_outreach_case_id",
    "proactive_outreach_source_ref",
    "sample_chatlog_replies",
    "sample_memory_learning",
    "sample_proactive_outreach_evidence",
    "sample_timing_events",
]
