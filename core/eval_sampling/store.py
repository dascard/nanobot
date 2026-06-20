"""EvalCandidate / EvalRun 的 DB 存储操作。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core.database import AdminAuditLog, EvalCandidate, EvalRun, EvalRunResult
from evals.expected_contract import validate_expected_contract


REPO_ROOT = Path(__file__).resolve().parent.parent.parent

RUNNABLE_EVAL_SUITES = frozenset({
    "sticker",
    "memory_learning",
    "moderation",
    "model_routing",
    "group_reply",
    "reply_contract",
    "rendering_contract",
    "timing_gate",
})

CANDIDATE_STATUS_CANDIDATE = "candidate"
CANDIDATE_STATUS_LABELED = "labeled"
CANDIDATE_STATUS_IGNORED = "ignored"
CANDIDATE_STATUS_DEFERRED = "deferred"
CANDIDATE_STATUS_REJECTED = "rejected"
CANDIDATE_STATUS_PROMOTED = "promoted"

PATCHABLE_CANDIDATE_STATUSES = {
    CANDIDATE_STATUS_CANDIDATE,
    CANDIDATE_STATUS_IGNORED,
}

LABELABLE_CANDIDATE_STATUSES = {
    CANDIDATE_STATUS_CANDIDATE,
    CANDIDATE_STATUS_DEFERRED,
}

IGNORABLE_CANDIDATE_STATUSES = {
    CANDIDATE_STATUS_CANDIDATE,
    CANDIDATE_STATUS_LABELED,
}

REJECTABLE_CANDIDATE_STATUSES = {
    CANDIDATE_STATUS_CANDIDATE,
    CANDIDATE_STATUS_LABELED,
    CANDIDATE_STATUS_DEFERRED,
    CANDIDATE_STATUS_IGNORED,
}

DEFERABLE_CANDIDATE_STATUSES = {
    CANDIDATE_STATUS_CANDIDATE,
    CANDIDATE_STATUS_LABELED,
}

REOPENABLE_CANDIDATE_STATUSES = {
    CANDIDATE_STATUS_IGNORED,
    CANDIDATE_STATUS_DEFERRED,
    CANDIDATE_STATUS_REJECTED,
}

REJECT_REASON_CODES = frozenset({
    "unspecified",
    "duplicate",
    "low_value",
    "unsafe_or_sensitive",
    "not_reproducible",
    "out_of_scope",
    "bad_sample",
})

DEFER_REASON_CODES = frozenset({
    "unspecified",
    "needs_more_context",
    "needs_batch_review",
    "waiting_for_baseline",
    "needs_product_decision",
    "temporary_blocker",
})

REOPEN_REASON_CODES = frozenset({
    "unspecified",
    "new_evidence",
    "operator_correction",
    "defer_expired",
    "needs_relabel",
})

BATCH_AUDIT_DECISIONS = frozenset({
    "noop",
    "needs_label",
    "promote_ready",
    "reject",
    "defer",
    "reopen",
})

BATCH_AUDIT_DECISION_REASON_CODES = {
    "noop": frozenset({"unspecified"}),
    "needs_label": frozenset({"unspecified"}),
    "promote_ready": frozenset({"unspecified"}),
    "reject": REJECT_REASON_CODES,
    "defer": DEFER_REASON_CODES,
    "reopen": REOPEN_REASON_CODES,
}


def _readiness_reason(code: str, message: str, **extra: Any) -> dict[str, Any]:
    reason: dict[str, Any] = {"code": code, "message": message}
    reason.update(extra)
    return reason


def _normalize_reason_code(value: str | None, allowed: frozenset[str]) -> str:
    code = str(value or "unspecified").strip() or "unspecified"
    if len(code) > 64 or code not in allowed:
        raise ValueError(f"invalid reason_code: {value}")
    return code


def _normalize_note(value: str | None) -> str:
    return str(value or "").strip()[:1000]


def _normalize_defer_until(value: str | None) -> str:
    return str(value or "").strip()[:64]


def _normalize_batch_note(value: str | None) -> str:
    return str(value or "").strip()[:1000]


def _validate_batch_audit_scope(
    *,
    case_ids: list[str] | None,
    suite: str,
    status: str,
    source: str,
    limit: int,
) -> tuple[list[str], int]:
    ids = [str(case_id).strip() for case_id in (case_ids or []) if str(case_id).strip()]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case_ids are not allowed")
    if len(ids) > 500:
        raise ValueError("case_ids limit exceeded")
    if not ids and not (suite or status or source):
        raise ValueError("case_ids or at least one filter is required")
    capped_limit = max(1, min(int(limit or 200), 500))
    return ids, capped_limit


def _normalize_batch_audit_decision(raw: dict[str, Any]) -> dict[str, Any]:
    case_id = str(raw.get("case_id") or "").strip()
    if not case_id:
        raise ValueError("decision case_id is required")
    decision = str(raw.get("decision") or "noop").strip() or "noop"
    if decision not in BATCH_AUDIT_DECISIONS:
        raise ValueError(f"invalid batch audit decision: {decision}")
    reason_code = _normalize_reason_code(
        raw.get("reason_code"),
        BATCH_AUDIT_DECISION_REASON_CODES[decision],
    )
    note = _normalize_note(raw.get("note"))
    defer_until = _normalize_defer_until(raw.get("defer_until"))
    if decision != "defer" and defer_until:
        raise ValueError("defer_until is only allowed for defer decision")
    return {
        "case_id": case_id,
        "decision": decision,
        "reason_code": reason_code,
        "note": note,
        "defer_until": defer_until,
        "expected_status": str(raw.get("expected_status") or "").strip(),
        "expected_updated_at": str(raw.get("expected_updated_at") or "").strip(),
    }


def _batch_audit_decision_by_case_id(
    decisions: list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    by_case_id: dict[str, dict[str, Any]] = {}
    for raw in decisions or []:
        normalized = _normalize_batch_audit_decision(raw)
        case_id = normalized["case_id"]
        if case_id in by_case_id:
            raise ValueError(f"duplicate decision for case_id: {case_id}")
        by_case_id[case_id] = normalized
    return by_case_id


def _count_values(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = value or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _candidate_batch_id(
    *,
    case_ids: list[str],
    suite: str,
    status: str,
    source: str,
    target_dataset: str,
) -> str:
    now = datetime.now()
    raw = json.dumps(
        {
            "case_ids": case_ids,
            "suite": suite,
            "status": status,
            "source": source,
            "target_dataset": target_dataset,
            "created_at": now.isoformat(timespec="seconds"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    suffix = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"batch_{now:%Y%m%d}_{suffix}"


def _triage_payload(
    row: EvalCandidate,
    *,
    before_status: str,
    reason_code: str,
    note: str,
    defer_until: str = "",
) -> dict[str, Any]:
    return {
        "candidate": _candidate_dict(row),
        "audit": {
            "before_status": before_status,
            "after_status": row.status,
            "reason_code": reason_code,
            "note": note,
            "defer_until": defer_until,
        },
    }


# ── Cursor ──

def get_cursor(db, source_type: str, source_key: str) -> dict:
    """获取采样游标。"""
    from core.database import EvalSampleCursor
    row = db.query(EvalSampleCursor).filter(
        EvalSampleCursor.source_type == source_type,
        EvalSampleCursor.source_key == source_key,
    ).first()
    if row:
        try:
            return json.loads(row.cursor_json or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def save_cursor(db, source_type: str, source_key: str, cursor_dict: dict):
    """保存采样游标。"""
    from core.database import EvalSampleCursor
    row = db.query(EvalSampleCursor).filter(
        EvalSampleCursor.source_type == source_type,
        EvalSampleCursor.source_key == source_key,
    ).first()
    if not row:
        row = EvalSampleCursor(source_type=source_type, source_key=source_key)
        db.add(row)
    row.cursor_json = json.dumps(cursor_dict, ensure_ascii=False)
    row.updated_at = datetime.now()
    db.commit()


# ── Fingerprint ──

def _make_fingerprint(suite: str, error_type: str, message: str) -> str:
    """生成指纹：sha256(suite + error_type + normalized_message[:200])[:16]"""
    norm = (message or "").strip()[:200]
    raw = f"{suite}|{error_type}|{norm}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ── Candidate CRUD ──

def upsert_candidate(db, case_dict: dict) -> bool:
    """插入或跳过（指纹碰撞时跳过）。返回 True=新创建。"""
    fingerprint = case_dict.get("fingerprint") or _make_fingerprint(
        case_dict.get("suite", ""),
        case_dict.get("description", "")[:40],
        json.dumps(case_dict.get("input", {}), ensure_ascii=False),
    )
    case_id = case_dict.get("case_id", "")
    existing = db.query(EvalCandidate).filter(
        (EvalCandidate.fingerprint == fingerprint) | (EvalCandidate.case_id == case_id)
    ).first()
    if existing:
        return False

    candidate = EvalCandidate(
        case_id=case_dict.get("case_id", ""),
        suite=case_dict.get("suite", ""),
        source=case_dict.get("source", "log"),
        source_ref=case_dict.get("source_ref", ""),
        description=case_dict.get("description", ""),
        input_json=json.dumps(case_dict.get("input", {}), ensure_ascii=False),
        expected_json=json.dumps(case_dict.get("expected", {"needs_label": True}), ensure_ascii=False),
        tags_json=json.dumps(case_dict.get("tags", []), ensure_ascii=False),
        status=case_dict.get("status", "candidate"),
        priority=case_dict.get("priority", 0),
        fingerprint=fingerprint,
        note=case_dict.get("note", ""),
    )
    db.add(candidate)
    db.commit()
    return True


def _candidate_query(db, *, suite: str = "", status: str = "", source: str = ""):
    q = db.query(EvalCandidate)
    if suite:
        q = q.filter(EvalCandidate.suite == suite)
    if status:
        q = q.filter(EvalCandidate.status == status)
    if source:
        q = q.filter(EvalCandidate.source == source)
    return q


def list_candidates(
    db,
    suite: str = "",
    status: str = "",
    source: str = "",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """列出候选。返回 (items, total)。"""
    q = _candidate_query(db, suite=suite, status=status, source=source)
    total = q.count()
    rows = (
        q.order_by(EvalCandidate.priority.desc(), EvalCandidate.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    items = [_candidate_dict(r) for r in rows]
    return items, total


def candidate_queue_summary(
    db,
    *,
    suite: str = "",
    status: str = "",
    source: str = "",
    target_dataset: str = "",
) -> dict[str, Any]:
    """返回当前过滤范围内的候选队列摘要。"""
    rows = _candidate_query(db, suite=suite, status=status, source=source).all()
    by_status: dict[str, int] = {}
    by_suite: dict[str, int] = {}
    by_source: dict[str, int] = {}
    readiness_counts = {"ready": 0, "blocked": 0}
    reason_counts: dict[str, int] = {}

    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1
        by_suite[row.suite] = by_suite.get(row.suite, 0) + 1
        by_source[row.source] = by_source.get(row.source, 0) + 1
        readiness = candidate_readiness(row, target_dataset=target_dataset or row.suite)
        if readiness["ready"]:
            readiness_counts["ready"] += 1
        else:
            readiness_counts["blocked"] += 1
        for reason in readiness["blocking_reasons"]:
            code = str(reason.get("code", "unknown"))
            reason_counts[code] = reason_counts.get(code, 0) + 1

    return {
        "total": len(rows),
        "filters": {
            "suite": suite,
            "status": status,
            "source": source,
            "target_dataset": target_dataset,
        },
        "by_status": by_status,
        "by_suite": by_suite,
        "by_source": by_source,
        "readiness": readiness_counts,
        "top_blocking_reasons": [
            {"code": code, "count": count}
            for code, count in sorted(
                reason_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ],
    }


def _candidate_trend_bucket_key(value: Any) -> str:
    if hasattr(value, "date"):
        return value.date().isoformat()
    return str(value or "")[:10]


def _top_counts(values: list[str]) -> list[dict[str, Any]]:
    return [
        {"code": code, "count": count}
        for code, count in sorted(
            _count_values(values).items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]


def candidate_trend_report(
    db,
    *,
    days: int = 30,
    suite: str = "",
    status: str = "",
    source: str = "",
    target_dataset: str = "",
) -> dict[str, Any]:
    """按候选创建日期分桶，返回当前候选状态与 readiness 快照。"""
    capped_days = max(1, min(int(days or 30), 90))
    start_date = datetime.now().date() - timedelta(days=capped_days - 1)
    start_at = datetime.combine(start_date, datetime.min.time())
    rows = (
        _candidate_query(db, suite=suite, status=status, source=source)
        .filter(EvalCandidate.created_at >= start_at)
        .order_by(EvalCandidate.created_at.asc(), EvalCandidate.id.asc())
        .all()
    )

    buckets: dict[str, dict[str, Any]] = {}
    summary_statuses: list[str] = []
    summary_suites: list[str] = []
    summary_sources: list[str] = []
    summary_readiness = {"ready": 0, "blocked": 0}
    summary_blocking_reasons: list[str] = []

    for row in rows:
        bucket_key = _candidate_trend_bucket_key(row.created_at)
        bucket = buckets.setdefault(
            bucket_key,
            {
                "date": bucket_key,
                "created": 0,
                "by_status": {},
                "by_suite": {},
                "by_source": {},
                "readiness": {"ready": 0, "blocked": 0},
                "_blocking_reasons": [],
            },
        )
        status_value = row.status or "unknown"
        suite_value = row.suite or "unknown"
        source_value = row.source or "unknown"
        readiness = candidate_readiness(row, target_dataset=target_dataset or row.suite)
        readiness_key = "ready" if readiness["ready"] else "blocked"

        bucket["created"] += 1
        bucket["by_status"][status_value] = bucket["by_status"].get(status_value, 0) + 1
        bucket["by_suite"][suite_value] = bucket["by_suite"].get(suite_value, 0) + 1
        bucket["by_source"][source_value] = bucket["by_source"].get(source_value, 0) + 1
        bucket["readiness"][readiness_key] += 1
        summary_readiness[readiness_key] += 1

        summary_statuses.append(status_value)
        summary_suites.append(suite_value)
        summary_sources.append(source_value)
        for reason in readiness.get("blocking_reasons", []):
            code = str(reason.get("code") or "unknown")
            bucket["_blocking_reasons"].append(code)
            summary_blocking_reasons.append(code)

    bucket_list: list[dict[str, Any]] = []
    for bucket in buckets.values():
        blocking_reasons = bucket.pop("_blocking_reasons")
        bucket["top_blocking_reasons"] = _top_counts(blocking_reasons)
        bucket_list.append(bucket)

    return {
        "ok": True,
        "filters": {
            "days": capped_days,
            "bucket": "day",
            "suite": suite,
            "status": status,
            "source": source,
            "target_dataset": target_dataset,
        },
        "summary": {
            "total": len(rows),
            "by_status": _count_values(summary_statuses),
            "by_suite": _count_values(summary_suites),
            "by_source": _count_values(summary_sources),
            "readiness": summary_readiness,
            "top_blocking_reasons": _top_counts(summary_blocking_reasons),
        },
        "buckets": bucket_list,
    }


def preflight_candidate_promotions(
    db,
    *,
    case_ids: list[str] | None = None,
    suite: str = "",
    status: str = "labeled",
    source: str = "",
    target_dataset: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    """批量预检候选晋升，不写文件、不改 DB。"""
    capped_limit = max(1, min(int(limit or 200), 500))
    rows_by_id: dict[str, EvalCandidate] = {}
    ordered_ids: list[str] = []

    if case_ids:
        ordered_ids = [str(case_id) for case_id in case_ids][:capped_limit]
        rows = (
            db.query(EvalCandidate)
            .filter(EvalCandidate.case_id.in_(ordered_ids))
            .all()
        )
        rows_by_id = {row.case_id: row for row in rows}
    else:
        rows = (
            _candidate_query(db, suite=suite, status=status or "labeled", source=source)
            .order_by(EvalCandidate.priority.desc(), EvalCandidate.id.desc())
            .limit(capped_limit)
            .all()
        )
        ordered_ids = [row.case_id for row in rows]
        rows_by_id = {row.case_id: row for row in rows}

    items: list[dict[str, Any]] = []
    ready_count = 0
    blocked_count = 0
    for case_id in ordered_ids:
        row = rows_by_id.get(case_id)
        readiness = candidate_readiness(
            row,
            target_dataset=target_dataset or (row.suite if row else "regression"),
        )
        if readiness["ready"]:
            ready_count += 1
        else:
            blocked_count += 1
        items.append({
            "case_id": case_id,
            "suite": row.suite if row else "",
            "status": row.status if row else "",
            "target_dataset": readiness["target_dataset"],
            "path": readiness["target_path"],
            "readiness": readiness,
        })

    return {
        "ok": blocked_count == 0,
        "total": len(items),
        "ready": ready_count,
        "blocked": blocked_count,
        "target_dataset": target_dataset,
        "items": items,
    }


def plan_candidate_batch_audit(
    db,
    *,
    case_ids: list[str] | None = None,
    suite: str = "",
    status: str = "",
    source: str = "",
    target_dataset: str = "",
    limit: int = 200,
    batch_note: str = "",
    decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """生成人工仲裁批次审计快照，不写 DB。"""
    ordered_ids, capped_limit = _validate_batch_audit_scope(
        case_ids=case_ids,
        suite=suite,
        status=status,
        source=source,
        limit=limit,
    )
    decision_by_id = _batch_audit_decision_by_case_id(decisions)

    rows_by_id: dict[str, EvalCandidate] = {}
    if ordered_ids:
        rows = (
            db.query(EvalCandidate)
            .filter(EvalCandidate.case_id.in_(ordered_ids))
            .all()
        )
        rows_by_id = {row.case_id: row for row in rows}
    else:
        rows = (
            _candidate_query(db, suite=suite, status=status, source=source)
            .order_by(EvalCandidate.priority.desc(), EvalCandidate.id.desc())
            .limit(capped_limit)
            .all()
        )
        ordered_ids = [row.case_id for row in rows]
        rows_by_id = {row.case_id: row for row in rows}

    unknown_decision_ids = sorted(set(decision_by_id) - set(ordered_ids))
    if unknown_decision_ids:
        raise ValueError(f"decision case_id not in batch: {unknown_decision_ids[0]}")

    batch_id = _candidate_batch_id(
        case_ids=ordered_ids,
        suite=suite,
        status=status,
        source=source,
        target_dataset=target_dataset,
    )
    normalized_batch_note = _normalize_batch_note(batch_note)
    filters = {
        "case_ids": ordered_ids,
        "suite": suite,
        "status": status,
        "source": source,
        "target_dataset": target_dataset,
        "limit": capped_limit,
    }

    items: list[dict[str, Any]] = []
    ready_count = 0
    blocked_count = 0
    statuses: list[str] = []
    suites: list[str] = []
    sources: list[str] = []
    decisions_seen: list[str] = []
    reason_codes: list[str] = []
    blocking_reasons: list[str] = []

    for case_id in ordered_ids:
        row = rows_by_id.get(case_id)
        decision = decision_by_id.get(case_id) or {
            "case_id": case_id,
            "decision": "noop",
            "reason_code": "unspecified",
            "note": "",
            "defer_until": "",
            "expected_status": "",
            "expected_updated_at": "",
        }
        readiness = candidate_readiness(
            row,
            target_dataset=target_dataset or (row.suite if row else "regression"),
        )
        if readiness["ready"]:
            ready_count += 1
        else:
            blocked_count += 1

        errors: list[dict[str, Any]] = []
        before_status = row.status if row else ""
        updated_at = str(row.updated_at) if row and row.updated_at else ""
        if row is None:
            errors.append(_readiness_reason("candidate_not_found", "candidate not found"))
        if row is not None and decision["expected_status"] and decision["expected_status"] != row.status:
            errors.append(
                _readiness_reason(
                    "expected_status_mismatch",
                    "candidate status changed",
                    expected_status=decision["expected_status"],
                    actual_status=row.status,
                )
            )
        if (
            row is not None
            and decision["expected_updated_at"]
            and decision["expected_updated_at"] != updated_at
        ):
            errors.append(
                _readiness_reason(
                    "expected_updated_at_mismatch",
                    "candidate updated_at changed",
                    expected_updated_at=decision["expected_updated_at"],
                    actual_updated_at=updated_at,
                )
            )
        if decision["decision"] == "promote_ready" and not readiness["ready"]:
            errors.append(
                _readiness_reason(
                    "promote_not_ready",
                    "candidate is not ready for promotion",
                )
            )

        for reason in readiness.get("blocking_reasons", []):
            blocking_reasons.append(str(reason.get("code") or "unknown"))

        status_value = before_status or "missing"
        suite_value = row.suite if row else ""
        source_value = row.source if row else ""
        statuses.append(status_value)
        if suite_value:
            suites.append(suite_value)
        if source_value:
            sources.append(source_value)
        decisions_seen.append(decision["decision"])
        reason_codes.append(decision["reason_code"])
        items.append({
            "case_id": case_id,
            "exists": row is not None,
            "suite": suite_value,
            "source": source_value,
            "source_ref": row.source_ref if row else "",
            "before_status": before_status,
            "priority": row.priority if row else 0,
            "decision": decision["decision"],
            "reason_code": decision["reason_code"],
            "note": decision["note"],
            "defer_until": decision["defer_until"],
            "target_dataset": readiness["target_dataset"],
            "readiness": readiness,
            "errors": errors,
        })

    counts = {
        "by_status": _count_values(statuses),
        "by_suite": _count_values(suites),
        "by_source": _count_values(sources),
        "by_decision": _count_values(decisions_seen),
        "by_reason_code": _count_values(reason_codes),
        "by_blocking_reason": _count_values(blocking_reasons),
    }
    ok = all(not item["errors"] for item in items)
    return {
        "ok": ok,
        "dry_run": True,
        "batch_id": batch_id,
        "audit_log_id": None,
        "filters": filters,
        "batch_note": normalized_batch_note,
        "total": len(items),
        "ready": ready_count,
        "blocked": blocked_count,
        "counts": counts,
        "items": items,
    }


def record_candidate_batch_audit(db, plan: dict[str, Any], *, ip_address: str = "") -> dict[str, Any]:
    """写入一条批次审计日志，不修改候选状态。"""
    if not plan.get("ok"):
        raise ValueError("cannot record invalid candidate batch audit")
    detail = {
        key: value
        for key, value in plan.items()
        if key not in {"dry_run", "audit_log_id"}
    }
    row = AdminAuditLog(
        action="audit_eval_candidate_batch",
        target_type="eval_candidate_batch",
        target_id=str(plan.get("batch_id") or ""),
        detail_json=json.dumps(detail, ensure_ascii=False),
        ip_address=(ip_address or "")[:45],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    result = dict(plan)
    result["dry_run"] = False
    result["audit_log_id"] = row.id
    return result


def get_candidate(db, case_id: str) -> EvalCandidate | None:
    """按 case_id 获取候选。"""
    return db.query(EvalCandidate).filter(EvalCandidate.case_id == case_id).first()


def update_candidate(db, case_id: str, **fields):
    """更新候选字段。"""
    row = get_candidate(db, case_id)
    if not row:
        return None
    next_status = fields.get("status")
    if next_status is not None:
        if next_status not in PATCHABLE_CANDIDATE_STATUSES:
            raise ValueError(f"invalid status transition: {next_status}")
        if row.status == CANDIDATE_STATUS_IGNORED and next_status == CANDIDATE_STATUS_CANDIDATE:
            row.status = CANDIDATE_STATUS_CANDIDATE
        elif (
            row.status in {CANDIDATE_STATUS_CANDIDATE, CANDIDATE_STATUS_LABELED}
            and next_status == CANDIDATE_STATUS_IGNORED
        ):
            row.status = CANDIDATE_STATUS_IGNORED
        elif row.status != next_status:
            raise ValueError(f"invalid status transition: {row.status} -> {next_status}")
        fields = {key: value for key, value in fields.items() if key != "status"}
    for key, val in fields.items():
        if hasattr(row, key):
            setattr(row, key, val)
    row.updated_at = datetime.now()
    db.commit()
    return _candidate_dict(row)


def label_candidate(db, case_id: str, expected_dict: dict, *, note: str | None = None):
    """标记候选：设置 expected_json 和 status=labeled。"""
    row = get_candidate(db, case_id)
    if not row:
        return None
    if row.status not in LABELABLE_CANDIDATE_STATUSES:
        raise ValueError(f"candidate status must be candidate or deferred before label: {row.status}")
    validate_expected_contract(row.suite, expected_dict)
    row.expected_json = json.dumps(expected_dict, ensure_ascii=False)
    row.status = CANDIDATE_STATUS_LABELED
    if note is not None:
        row.note = note
    row.updated_at = datetime.now()
    db.commit()
    return _candidate_dict(row)


def ignore_candidate(db, case_id: str):
    """忽略候选：status=ignored。"""
    row = get_candidate(db, case_id)
    if not row:
        return None
    if row.status not in IGNORABLE_CANDIDATE_STATUSES:
        raise ValueError(f"invalid status transition: {row.status} -> ignored")
    row.status = CANDIDATE_STATUS_IGNORED
    row.updated_at = datetime.now()
    db.commit()
    return _candidate_dict(row)


def reject_candidate(
    db,
    case_id: str,
    *,
    reason_code: str | None = None,
    note: str | None = None,
):
    """拒绝候选：status=rejected，并返回候选和审计 payload。"""
    row = get_candidate(db, case_id)
    if not row:
        return None
    if row.status not in REJECTABLE_CANDIDATE_STATUSES:
        raise ValueError(f"invalid status transition: {row.status} -> rejected")
    before = row.status
    reason = _normalize_reason_code(reason_code, REJECT_REASON_CODES)
    normalized_note = _normalize_note(note)
    row.status = CANDIDATE_STATUS_REJECTED
    if normalized_note:
        row.note = normalized_note
    row.updated_at = datetime.now()
    db.commit()
    return _triage_payload(
        row,
        before_status=before,
        reason_code=reason,
        note=normalized_note,
    )


def defer_candidate(
    db,
    case_id: str,
    *,
    reason_code: str | None = None,
    note: str | None = None,
    defer_until: str | None = None,
):
    """暂缓候选：status=deferred，并返回候选和审计 payload。"""
    row = get_candidate(db, case_id)
    if not row:
        return None
    if row.status not in DEFERABLE_CANDIDATE_STATUSES:
        raise ValueError(f"invalid status transition: {row.status} -> deferred")
    before = row.status
    reason = _normalize_reason_code(reason_code, DEFER_REASON_CODES)
    normalized_note = _normalize_note(note)
    normalized_defer_until = _normalize_defer_until(defer_until)
    row.status = CANDIDATE_STATUS_DEFERRED
    if normalized_note:
        row.note = normalized_note
    row.updated_at = datetime.now()
    db.commit()
    return _triage_payload(
        row,
        before_status=before,
        reason_code=reason,
        note=normalized_note,
        defer_until=normalized_defer_until,
    )


def reopen_candidate(
    db,
    case_id: str,
    *,
    reason_code: str | None = None,
    note: str | None = None,
):
    """复开候选：从 ignored/deferred/rejected 回到 candidate。"""
    row = get_candidate(db, case_id)
    if not row:
        return None
    if row.status not in REOPENABLE_CANDIDATE_STATUSES:
        raise ValueError(f"invalid status transition: {row.status} -> candidate")
    before = row.status
    reason = _normalize_reason_code(reason_code, REOPEN_REASON_CODES)
    normalized_note = _normalize_note(note)
    row.status = CANDIDATE_STATUS_CANDIDATE
    if normalized_note:
        row.note = normalized_note
    row.updated_at = datetime.now()
    db.commit()
    return _triage_payload(
        row,
        before_status=before,
        reason_code=reason,
        note=normalized_note,
    )


def _validate_dataset_name(value: str) -> tuple[str, dict[str, Any] | None]:
    name = str(value or "regression").strip()
    if not name or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        return name, _readiness_reason(
            "target_dataset_invalid",
            f"invalid target_dataset: {value}",
            field="target_dataset",
        )
    return name, None


def _safe_dataset_name(value: str) -> str:
    name, reason = _validate_dataset_name(value)
    if reason:
        raise ValueError(reason["message"])
    return name


def candidate_readiness(
    row: EvalCandidate | None,
    *,
    target_dataset: str | None = None,
) -> dict[str, Any]:
    """返回候选当前是否可晋升，以及阻断原因。"""
    dataset, dataset_reason = _validate_dataset_name(
        target_dataset or (row.suite if row is not None else "regression")
    )
    target_path = ""
    blocking_reasons: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if row is None:
        blocking_reasons.append(_readiness_reason("candidate_not_found", "candidate not found"))
        return {
            "ready": False,
            "can_label": False,
            "can_promote": False,
            "status": "blocked",
            "suite": "",
            "target_dataset": dataset,
            "target_path": "",
            "blocking_reasons": blocking_reasons,
            "warnings": warnings,
        }

    if dataset_reason:
        blocking_reasons.append(dataset_reason)
    else:
        target_path = str(REPO_ROOT / "evals" / "cases" / dataset / f"{row.case_id}.json")

    if row.status != "labeled":
        blocking_reasons.append(_readiness_reason(
            "invalid_status",
            "candidate status must be labeled before promote",
            status=row.status,
        ))

    if row.suite not in RUNNABLE_EVAL_SUITES:
        blocking_reasons.append(_readiness_reason(
            "suite_not_runnable",
            "suite is not runnable",
            suite=row.suite,
        ))

    expected = _safe_json(row.expected_json, {})
    try:
        validate_expected_contract(row.suite, expected)
    except ValueError as exc:
        blocking_reasons.append(_readiness_reason(
            "expected_invalid",
            str(exc),
            suite=row.suite,
        ))

    if target_path and Path(target_path).exists():
        blocking_reasons.append(_readiness_reason(
            "target_case_exists",
            f"target case already exists: {target_path}",
            path=target_path,
        ))

    ready = not blocking_reasons
    return {
        "ready": ready,
        "can_label": row.status == "candidate",
        "can_promote": ready,
        "status": "ready" if ready else "blocked",
        "suite": row.suite,
        "target_dataset": dataset,
        "target_path": target_path,
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
    }


def plan_candidate_promotion(db, case_id: str, *, target_dataset: str = "regression") -> dict:
    """构建候选晋升计划，不写文件、不改 DB。"""
    row = get_candidate(db, case_id)
    if not row:
        raise ValueError("candidate not found")
    readiness = candidate_readiness(row, target_dataset=target_dataset)
    if not readiness["ready"]:
        reason = readiness["blocking_reasons"][0]
        raise ValueError(f"{reason['code']}: {reason['message']}")
    expected = _safe_json(row.expected_json, {})

    dataset = readiness["target_dataset"]
    out_path = Path(readiness["target_path"])

    tags = _safe_json(row.tags_json, [])
    if not isinstance(tags, list):
        tags = []
    if "promoted" not in tags:
        tags = [*tags, "promoted"]

    case_data = {
        "id": case_id,
        "suite": row.suite,
        "description": row.description,
        "input": _safe_json(row.input_json, {}),
        "expected": expected,
        "tags": tags,
        "meta": {
            "origin": "eval_candidate",
            "source": row.source,
            "source_ref": row.source_ref or "",
            "fingerprint": row.fingerprint or "",
        },
    }
    return {
        "case_id": case_id,
        "suite": row.suite,
        "target_dataset": dataset,
        "path": str(out_path),
        "case": case_data,
    }


def promote_candidate(db, case_id: str, *, target_dataset: str = "regression") -> str | None:
    """提升候选到指定 eval dataset。必须已标注。"""
    try:
        plan = plan_candidate_promotion(db, case_id, target_dataset=target_dataset)
    except ValueError as e:
        if str(e) == "candidate not found":
            return None
        raise

    out_path = Path(plan["path"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with out_path.open("x", encoding="utf-8") as fh:
            json.dump(plan["case"], fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    except FileExistsError as e:
        raise ValueError(f"target case already exists: {out_path}") from e

    row = get_candidate(db, case_id)
    if not row:
        return None
    row.status = "promoted"
    row.updated_at = datetime.now()
    db.commit()
    return str(out_path)


# ── Run CRUD ──

def save_run(db, suite: str, report_dict: dict) -> EvalRun:
    """保存 eval run 记录。"""
    import subprocess
    git_sha = ""
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=base, text=True, stderr=subprocess.DEVNULL, timeout=3,
        ).strip()
    except Exception:
        pass

    run = EvalRun(
        suite=suite,
        git_sha=git_sha,
        status="completed",
        total=report_dict.get("total", 0),
        passed=report_dict.get("passed", 0),
        failed=report_dict.get("failed", 0),
        pass_rate=report_dict.get("pass_rate", 0.0),
        summary_json=json.dumps(report_dict.get("summary", {}), ensure_ascii=False),
    )
    db.add(run)
    db.commit()
    return run


def save_run_results(db, run_id: int, results: list[dict]):
    """批量保存 run 的每个 case 结果。"""
    for r in results:
        db.add(EvalRunResult(
            run_id=run_id,
            case_id=r.get("case_id", ""),
            suite=r.get("suite", ""),
            passed=1 if r.get("passed") else 0,
            score=r.get("score", 0.0),
            errors_json=json.dumps(r.get("errors", []), ensure_ascii=False),
            output_json=json.dumps(r.get("output", {}), ensure_ascii=False),
        ))
    db.commit()


def get_runs(db, limit: int = 20) -> list[dict]:
    """列出最近 runs。"""
    rows = db.query(EvalRun).order_by(EvalRun.id.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "suite": r.suite,
            "git_sha": r.git_sha or "",
            "status": r.status,
            "total": r.total,
            "passed": r.passed,
            "failed": r.failed,
            "pass_rate": r.pass_rate,
            "created_at": str(r.created_at) if r.created_at else "",
        }
        for r in rows
    ]


def get_run(db, run_id: int) -> tuple[dict | None, list[dict]]:
    """获取单个 run 及其结果。"""
    run = db.query(EvalRun).filter(EvalRun.id == run_id).first()
    if not run:
        return None, []
    results = (
        db.query(EvalRunResult)
        .filter(EvalRunResult.run_id == run_id)
        .order_by(EvalRunResult.id)
        .all()
    )
    run_dict = {
        "id": run.id,
        "suite": run.suite,
        "git_sha": run.git_sha or "",
        "status": run.status,
        "total": run.total,
        "passed": run.passed,
        "failed": run.failed,
        "pass_rate": run.pass_rate,
        "summary_json": run.summary_json or "{}",
        "created_at": str(run.created_at) if run.created_at else "",
    }
    result_list = [
        {
            "id": r.id,
            "case_id": r.case_id,
            "suite": r.suite,
            "passed": bool(r.passed),
            "score": r.score or 0.0,
            "errors": json.loads(r.errors_json or "[]"),
            "output": json.loads(r.output_json or "{}"),
        }
        for r in results
    ]
    return run_dict, result_list


# ── Internal helpers ──

def _candidate_dict(r: EvalCandidate) -> dict:
    return {
        "id": r.id,
        "case_id": r.case_id,
        "suite": r.suite,
        "source": r.source,
        "source_ref": r.source_ref or "",
        "description": r.description or "",
        "input": _safe_json(r.input_json, {}),
        "expected": _safe_json(r.expected_json, {}),
        "tags": _safe_json(r.tags_json, []),
        "status": r.status,
        "priority": r.priority,
        "fingerprint": r.fingerprint or "",
        "note": r.note or "",
        "created_at": str(r.created_at) if r.created_at else "",
        "updated_at": str(r.updated_at) if r.updated_at else "",
        "readiness": candidate_readiness(r),
    }


def _safe_json(raw, default=None):
    try:
        return json.loads(raw) if isinstance(raw, str) else (raw or default)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}
