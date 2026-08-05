"""主动外呼反馈到现有 EvalCandidate 工作流的可信适配。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from core.db.models import EvalCandidate, ProactiveOutreachLog
from core.eval_sampling.db_sampler import (
    build_proactive_outreach_evidence_case,
    proactive_outreach_source_ref,
)


PROACTIVE_FEEDBACK_LABELS = frozenset({
    "helpful",
    "neutral",
    "not_helpful",
    "intrusive",
    "incorrect",
    "duplicate",
})
PROACTIVE_FEEDBACK_SOURCES = frozenset({
    "user_reported",
    "operator_review",
})
_FEEDBACK_ELIGIBLE_STATUSES = frozenset({
    "sent",
    "sent_after_ambiguous_replay",
    "failed",
    "evaluation_error",
    "ambiguous",
    "cancelled",
    "legacy_ambiguous_hold",
})


class ProactiveFeedbackError(ValueError):
    """反馈引用、标签或目标状态不满足合同。"""


class ProactiveFeedbackConflict(ProactiveFeedbackError):
    """同一外呼状态已经绑定不同的反馈事实。"""


@dataclass(frozen=True, slots=True)
class ProactiveFeedbackResult:
    case_id: str
    label: str
    source: str
    evidence_sha256: str
    created: bool
    deduplicated: bool


def _feedback_payload(raw: str) -> dict:
    try:
        parsed = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    feedback = parsed.get("feedback")
    return feedback if isinstance(feedback, dict) else {}


def record_proactive_outreach_feedback(
    db: Session,
    *,
    log_id: int,
    label: str,
    source: str,
    evidence_ref: str,
) -> ProactiveFeedbackResult:
    """保存可信反馈；仅持久化证据引用摘要，不保存用户反馈原文。"""

    normalized_label = str(label or "").strip().lower()
    normalized_source = str(source or "").strip().lower()
    normalized_evidence_ref = str(evidence_ref or "").strip()
    if normalized_label not in PROACTIVE_FEEDBACK_LABELS:
        raise ProactiveFeedbackError("主动外呼反馈标签不受支持")
    if normalized_source not in PROACTIVE_FEEDBACK_SOURCES:
        raise ProactiveFeedbackError("主动外呼反馈来源不受支持")
    if (
        not normalized_evidence_ref
        or len(normalized_evidence_ref) > 512
        or any(ord(character) < 32 for character in normalized_evidence_ref)
    ):
        raise ProactiveFeedbackError("主动外呼反馈证据引用无效")
    if type(log_id) is not int or log_id <= 0:
        raise ProactiveFeedbackError("主动外呼 log_id 无效")

    row = db.get(ProactiveOutreachLog, log_id)
    if row is None:
        raise ProactiveFeedbackError("主动外呼记录不存在")
    status = str(row.status or "")
    if status not in _FEEDBACK_ELIGIBLE_STATUSES:
        raise ProactiveFeedbackError("主动外呼尚未形成可反馈的终态证据")
    evidence_sha256 = hashlib.sha256(
        normalized_evidence_ref.encode("utf-8")
    ).hexdigest()
    source_ref = proactive_outreach_source_ref(log_id, status)
    existing = (
        db.query(EvalCandidate)
        .filter(EvalCandidate.source_ref == source_ref)
        .one_or_none()
    )
    expected_feedback = {
        "label": normalized_label,
        "source": normalized_source,
        "evidence_sha256": evidence_sha256,
    }
    if existing is not None:
        current_feedback = _feedback_payload(existing.expected_json)
        if current_feedback:
            if current_feedback != expected_feedback:
                raise ProactiveFeedbackConflict(
                    "同一主动外呼状态已记录不同反馈"
                )
            return ProactiveFeedbackResult(
                case_id=str(existing.case_id),
                label=normalized_label,
                source=normalized_source,
                evidence_sha256=evidence_sha256,
                created=False,
                deduplicated=True,
            )
        case = build_proactive_outreach_evidence_case(db, row)
        existing.input_json = json.dumps(
            case["input"],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        existing.expected_json = json.dumps(
            {"needs_label": False, "feedback": expected_feedback},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        existing.tags_json = json.dumps(
            [*case["tags"], "feedback", normalized_label],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        existing.status = "labeled"
        existing.note = "已记录脱敏主动外呼反馈"
        db.flush()
        return ProactiveFeedbackResult(
            case_id=str(existing.case_id),
            label=normalized_label,
            source=normalized_source,
            evidence_sha256=evidence_sha256,
            created=False,
            deduplicated=False,
        )

    case = build_proactive_outreach_evidence_case(db, row)
    candidate = EvalCandidate(
        case_id=case["case_id"],
        suite=case["suite"],
        source=case["source"],
        source_ref=case["source_ref"],
        description=case["description"],
        input_json=json.dumps(
            case["input"],
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        expected_json=json.dumps(
            {"needs_label": False, "feedback": expected_feedback},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        tags_json=json.dumps(
            [*case["tags"], "feedback", normalized_label],
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        status="labeled",
        priority=0,
        fingerprint=case["fingerprint"],
        note="已记录脱敏主动外呼反馈",
    )
    db.add(candidate)
    db.flush()
    return ProactiveFeedbackResult(
        case_id=str(candidate.case_id),
        label=normalized_label,
        source=normalized_source,
        evidence_sha256=evidence_sha256,
        created=True,
        deduplicated=False,
    )


__all__ = [
    "PROACTIVE_FEEDBACK_LABELS",
    "PROACTIVE_FEEDBACK_SOURCES",
    "ProactiveFeedbackConflict",
    "ProactiveFeedbackError",
    "ProactiveFeedbackResult",
    "record_proactive_outreach_feedback",
]
