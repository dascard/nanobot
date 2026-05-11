"""EvalCandidate / EvalRun 的 DB 存储操作。"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from core.database import EvalCandidate, EvalRun, EvalRunResult


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
    existing = db.query(EvalCandidate).filter(
        EvalCandidate.fingerprint == fingerprint
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


def list_candidates(
    db,
    suite: str = "",
    status: str = "",
    source: str = "",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """列出候选。返回 (items, total)。"""
    q = db.query(EvalCandidate)
    if suite:
        q = q.filter(EvalCandidate.suite == suite)
    if status:
        q = q.filter(EvalCandidate.status == status)
    if source:
        q = q.filter(EvalCandidate.source == source)
    total = q.count()
    rows = q.order_by(EvalCandidate.id.desc()).offset(offset).limit(limit).all()
    items = [_candidate_dict(r) for r in rows]
    return items, total


def get_candidate(db, case_id: str) -> EvalCandidate | None:
    """按 case_id 获取候选。"""
    return db.query(EvalCandidate).filter(EvalCandidate.case_id == case_id).first()


def update_candidate(db, case_id: str, **fields):
    """更新候选字段。"""
    row = get_candidate(db, case_id)
    if not row:
        return None
    for key, val in fields.items():
        if hasattr(row, key):
            setattr(row, key, val)
    row.updated_at = datetime.now()
    db.commit()
    return _candidate_dict(row)


def label_candidate(db, case_id: str, expected_dict: dict):
    """标记候选：设置 expected_json 和 status=labeled。"""
    row = get_candidate(db, case_id)
    if not row:
        return None
    row.expected_json = json.dumps(expected_dict, ensure_ascii=False)
    row.status = "labeled"
    row.updated_at = datetime.now()
    db.commit()
    return _candidate_dict(row)


def ignore_candidate(db, case_id: str):
    """忽略候选：status=ignored。"""
    row = get_candidate(db, case_id)
    if not row:
        return None
    row.status = "ignored"
    row.updated_at = datetime.now()
    db.commit()
    return _candidate_dict(row)


def promote_candidate(db, case_id: str) -> str | None:
    """提升候选到 regression 目录。返回文件路径或 None。"""
    row = get_candidate(db, case_id)
    if not row:
        return None
    # 写 JSON 文件到 evals/cases/regression/
    base = Path(__file__).resolve().parent.parent.parent
    target_dir = base / "evals" / "cases" / "regression"
    target_dir.mkdir(parents=True, exist_ok=True)
    out_path = target_dir / f"{case_id}.json"

    case_data = {
        "id": case_id,
        "suite": row.suite,
        "description": row.description,
        "input": json.loads(row.input_json or "{}"),
        "expected": json.loads(row.expected_json or "{}"),
        "tags": json.loads(row.tags_json or "[]"),
    }
    out_path.write_text(
        json.dumps(case_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
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
    }


def _safe_json(raw, default=None):
    try:
        return json.loads(raw) if isinstance(raw, str) else (raw or default)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}
