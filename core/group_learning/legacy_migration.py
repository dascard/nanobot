"""旧群学习记录的内容摘要与人工审核凭据合同。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any


_LEGACY_REVIEW_ACTIONS = {
    ("legacy_expression", "legacy_expression.accept"): (
        "expression_memory",
        "legacy_accept",
    ),
    ("legacy_expression", "legacy_expression.reject"): (
        "expression_memory",
        "legacy_reject",
    ),
    ("legacy_jargon", "legacy_jargon.accept"): (
        "jargon_memory",
        "legacy_accept",
    ),
    ("legacy_jargon", "legacy_jargon.reject"): (
        "jargon_memory",
        "legacy_reject",
    ),
}


def _sha256_json(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def legacy_content_hash(content: str, meaning: str) -> str:
    """绑定旧记录正文与释义，供迁移 dry-run 和审计凭据复用。"""

    return _sha256_json({
        "content": str(content or "").strip(),
        "meaning": str(meaning or "").strip(),
    })


def legacy_reviewed_content_hash(content: str, meaning: str) -> str:
    """生成与群学习人工审核合同一致的正文摘要。"""

    payload = (
        f"{str(content or '').strip()}\0"
        f"{str(meaning or '').strip()}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class LegacyHumanReviewProof:
    """一个与具体旧记录绑定的管理员审核凭据。"""

    audit_log_id: int
    reviewer_id: str
    reviewed_at: datetime
    human_action: str
    approved_content_hash: str


def validate_legacy_human_review_proof(
    *,
    source: str,
    legacy_id: int,
    chat_stream_id: str,
    content: str,
    meaning: str,
    audit_log_id: int,
    admin_user: str,
    action: str,
    target_type: str,
    target_id: str,
    detail_json: str,
    created_at: datetime | None,
) -> LegacyHumanReviewProof | None:
    """验证审计动作、目标、会话和正文摘要，拒绝模糊历史日志。"""

    action_contract = _LEGACY_REVIEW_ACTIONS.get(
        (str(source or ""), str(action or ""))
    )
    if action_contract is None:
        return None
    expected_target_type, human_action = action_contract
    reviewer_id = str(admin_user or "").strip()
    if (
        str(target_type or "") != expected_target_type
        or str(target_id or "") != str(int(legacy_id))
        or not reviewer_id
        or created_at is None
        or int(audit_log_id or 0) <= 0
    ):
        return None
    try:
        detail: Any = json.loads(str(detail_json or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(detail, dict):
        return None
    if (
        detail.get("schema_version") != 1
        or str(detail.get("chat_stream_id") or "") != chat_stream_id
        or str(detail.get("content_hash") or "")
        != legacy_content_hash(content, meaning)
    ):
        return None
    return LegacyHumanReviewProof(
        audit_log_id=int(audit_log_id),
        reviewer_id=reviewer_id[:128],
        reviewed_at=created_at,
        human_action=human_action,
        approved_content_hash=legacy_reviewed_content_hash(
            content,
            meaning,
        ),
    )


__all__ = [
    "LegacyHumanReviewProof",
    "legacy_content_hash",
    "legacy_reviewed_content_hash",
    "validate_legacy_human_review_proof",
]
