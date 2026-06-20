"""EvalCandidate / EvalRun 的 DB 存储操作。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from core.database import EvalCandidate, EvalRun, EvalRunResult
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
