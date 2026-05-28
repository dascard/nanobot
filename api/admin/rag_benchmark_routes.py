"""RAG Benchmark Web 管理接口。"""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from api.admin.common import audit_request, verify_admin
from core.database import get_db, sqlite_path_from_database_url
from evals.rag_benchmark.adapters import run_case_with_adapter
from evals.rag_benchmark.cases import load_cases
from evals.rag_benchmark.report import write_reports
from evals.rag_benchmark.sample import sample_cases, write_generated_cases
from evals.rag_benchmark.schema import BenchmarkCase, BenchmarkResult, CaseScore
from evals.rag_benchmark.scoring import aggregate_scores, score_case


router = APIRouter(prefix="/benchmark", tags=["admin-rag-benchmark"])

BENCHMARK_MANUAL_DIR = Path("evals/cases/rag_benchmark/manual")
BENCHMARK_GENERATED_DIR = Path("tmp/rag_benchmark/generated")
BENCHMARK_REPORT_DIR = Path("tmp/rag_benchmark/reports")
BENCHMARK_CASE_BACKUP_DIR = Path("tmp/rag_benchmark/case_backups")
BENCHMARK_CASE_TRASH_DIR = Path("tmp/rag_benchmark/case_trash")
BENCHMARK_RUN_LOCK = Path("tmp/rag_benchmark/run.lock")
GENERATOR_VERSION = "rag_benchmark:v1"
CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")


class BenchmarkRunRequest(BaseModel):
    include_manual: bool = True
    include_generated: bool = True
    source_types: list[str] = Field(default_factory=list)
    case_types: list[str] = Field(default_factory=list)
    case_status: str = "enabled"
    provider_mode: str = "deterministic"
    sample_before_run: bool = False
    per_source: int = 5
    max_cases: int = 100
    timeout_seconds: int = 60
    per_case_timeout_seconds: int = 15
    include_candidates_limit: int = 10


class BenchmarkSampleRequest(BaseModel):
    per_source: int = 5


class BenchmarkCaseSaveRequest(BaseModel):
    case: BenchmarkCase


def get_benchmark_db_path() -> Path | None:
    from config import DATABASE_URL

    path = sqlite_path_from_database_url(DATABASE_URL)
    return Path(path) if path else None


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(int(value), high))


def _now_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _safe_rel_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except Exception:
        return path.as_posix()


def _preview(text: str, limit: int = 200) -> str:
    value = " ".join(str(text or "").split())
    return value[:limit] + ("..." if len(value) > limit else "")


def _readable_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _case_id_or_400(case_id: str) -> str:
    if not CASE_ID_RE.fullmatch(case_id or ""):
        raise HTTPException(400, "Invalid case_id")
    if case_id in {".", ".."} or case_id.startswith("."):
        raise HTTPException(400, "Invalid case_id")
    return case_id


def _manual_case_path(case_id: str) -> Path:
    case_id = _case_id_or_400(case_id)
    root = BENCHMARK_MANUAL_DIR.resolve()
    path = (root / f"{case_id}.json").resolve()
    if root != path.parent:
        raise HTTPException(400, "Invalid case path")
    return path


def _dir_writable(path: Path) -> bool:
    if path.exists():
        return path.is_dir() and os.access(path, os.W_OK)
    parent = path.parent if path.parent != path else Path(".")
    return parent.exists() and os.access(parent, os.W_OK)


def _open_readonly_session(db_path: Path) -> Session:
    path = db_path.resolve().as_posix()
    engine = create_engine(
        f"sqlite:///file:{path}?mode=ro&uri=true",
        connect_args={"check_same_thread": False},
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _table_exists(db: Session, table_name: str) -> bool:
    row = db.execute(
        text("SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = :name LIMIT 1"),
        {"name": table_name},
    ).fetchone()
    return row is not None


def db_fingerprint(db_path: Path, db: Session | None = None) -> dict[str, Any]:
    stat = db_path.stat()
    close_db = False
    if db is None:
        db = _open_readonly_session(db_path)
        close_db = True
    try:
        count = 0
        if _table_exists(db, "semantic_index_items"):
            count = int(db.execute(text("SELECT COUNT(*) FROM semantic_index_items")).scalar() or 0)
        return {
            "path": db_path.resolve().as_posix(),
            "size": int(stat.st_size),
            "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "mtime_ns": int(stat.st_mtime_ns),
            "semantic_index_count": count,
        }
    finally:
        if close_db:
            db.close()


def _same_fingerprint(left: dict[str, Any] | None, right: dict[str, Any]) -> bool:
    if not isinstance(left, dict):
        return False
    keys = ("size", "mtime_ns", "semantic_index_count")
    return all(left.get(key) == right.get(key) for key in keys)


def preflight_readonly(db_path: Path | None = None) -> dict[str, Any]:
    if db_path is None:
        db_path = get_benchmark_db_path()
    if db_path is None:
        return {"ok": False, "errors": ["benchmark web run currently supports SQLite only"], "source_counts": {}}
    if not db_path.exists():
        return {"ok": False, "errors": ["database file not found"], "source_counts": {}}
    db = _open_readonly_session(db_path)
    try:
        errors: list[str] = []
        if not _table_exists(db, "semantic_index_items"):
            errors.append("semantic_index_items missing")
        if not _table_exists(db, "semantic_index_fts"):
            errors.append("semantic_index_fts missing")
        counts: dict[str, int] = {}
        if not errors:
            rows = db.execute(text(
                "SELECT source_type, COUNT(*) FROM semantic_index_items "
                "WHERE status = 'active' GROUP BY source_type"
            )).fetchall()
            counts = {str(row[0]): int(row[1]) for row in rows}
        return {"ok": not errors, "errors": errors, "source_counts": counts}
    finally:
        db.close()


def _case_to_list_item(case: BenchmarkCase, *, origin: str, editable: bool, stale: bool = False) -> dict[str, Any]:
    return {
        "id": case.id,
        "suite": case.suite,
        "source_type": case.source_type,
        "case_type": case.case_type,
        "case_status": case.status,
        "origin": origin,
        "editable": editable,
        "stale": stale,
        "query_preview": _preview(case.query),
        "expected_candidate_ids": case.expected.candidate_ids[:5],
        "constraints": {
            "requires_citation": case.expected.requires_citation,
            "requires_sendable": case.expected.requires_sendable,
            "requires_group_id": case.expected.requires_group_id,
            "allow_empty": case.expected.allow_empty,
        },
    }


def _load_cases_with_origin() -> list[tuple[BenchmarkCase, str, bool]]:
    items: list[tuple[BenchmarkCase, str, bool]] = []
    for case in load_cases(manual_dir=BENCHMARK_MANUAL_DIR, generated_dir=Path("__missing_generated__")):
        items.append((case, "manual", True))
    for case in load_cases(manual_dir=Path("__missing_manual__"), generated_dir=BENCHMARK_GENERATED_DIR):
        items.append((case, "generated", False))
    return items


def _filtered_cases(
    *,
    include_manual: bool,
    include_generated: bool,
    source_types: list[str],
    case_types: list[str],
    case_status: str,
) -> list[tuple[BenchmarkCase, str, bool]]:
    source_set = {item for item in source_types if item}
    type_set = {item for item in case_types if item}
    result: list[tuple[BenchmarkCase, str, bool]] = []
    for case, origin, editable in _load_cases_with_origin():
        if origin == "manual" and not include_manual:
            continue
        if origin == "generated" and not include_generated:
            continue
        if source_set and case.source_type not in source_set:
            continue
        if type_set and case.case_type not in type_set:
            continue
        if case_status and case_status != "all" and case.status != case_status:
            continue
        result.append((case, origin, editable))
    return result


@contextmanager
def _run_lock():
    BENCHMARK_RUN_LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(BENCHMARK_RUN_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise HTTPException(409, "benchmark run already in progress")
    try:
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
        yield
    finally:
        try:
            BENCHMARK_RUN_LOCK.unlink()
        except FileNotFoundError:
            pass


def _summary_scores(scores: list[CaseScore]) -> dict[str, Any]:
    return {
        "total": len(scores),
        "passed": sum(1 for score in scores if score.ok),
        "failed": sum(1 for score in scores if not score.ok),
    }


def _trim_result(result: BenchmarkResult, limit: int) -> dict[str, Any]:
    data = result.model_dump()
    data["candidates"] = data.get("candidates", [])[:limit]
    data["candidate_ids"] = data.get("candidate_ids", [])[:limit]
    data["debug_summary"] = {}
    return data


@router.get("/status")
def benchmark_status(_auth=Depends(verify_admin)):
    db_path = get_benchmark_db_path()
    preflight = preflight_readonly(db_path)
    fingerprint = {}
    readonly_supported = db_path is not None and db_path.exists()
    if readonly_supported:
        try:
            db = _open_readonly_session(db_path)
            try:
                fingerprint = db_fingerprint(db_path, db)
            finally:
                db.close()
        except Exception:
            readonly_supported = False
    return {
        "manual_dir_writable": _dir_writable(BENCHMARK_MANUAL_DIR),
        "generated_dir_writable": _dir_writable(BENCHMARK_GENERATED_DIR),
        "reports_dir_writable": _dir_writable(BENCHMARK_REPORT_DIR),
        "db_readonly_supported": readonly_supported,
        "db_fingerprint": fingerprint,
        "preflight": preflight,
    }


@router.get("/cases")
def list_benchmark_cases(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    source_type: str = "",
    case_type: str = "",
    origin: str = "",
    case_status: str = "all",
    result_status: str = "",
    q: str = "",
    _auth=Depends(verify_admin),
):
    db_path = get_benchmark_db_path()
    current_fp = db_fingerprint(db_path) if db_path and db_path.exists() else {}
    rows = []
    for case, item_origin, editable in _load_cases_with_origin():
        stale = item_origin == "generated" and not _same_fingerprint(case.meta.get("db_fingerprint"), current_fp)
        if source_type and case.source_type != source_type:
            continue
        if case_type and case.case_type != case_type:
            continue
        if origin and item_origin != origin:
            continue
        if case_status != "all" and case.status != case_status:
            continue
        if result_status == "stale" and not stale:
            continue
        if q and q.lower() not in (case.id + " " + case.query).lower():
            continue
        rows.append(_case_to_list_item(case, origin=item_origin, editable=editable, stale=stale))
    start = (page - 1) * limit
    return {"total": len(rows), "items": rows[start:start + limit], "page": page, "limit": limit}


@router.get("/cases/{case_id}")
def get_benchmark_case(case_id: str, _auth=Depends(verify_admin)):
    case_id = _case_id_or_400(case_id)
    for case, origin, editable in _load_cases_with_origin():
        if case.id == case_id:
            data = case.model_dump()
            data["query"] = _preview(case.query, 200)
            return {"case": data, "origin": origin, "editable": editable}
    raise HTTPException(404, "benchmark case not found")


@router.put("/cases/{case_id}")
def save_benchmark_case(
    case_id: str,
    body: BenchmarkCaseSaveRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    case_id = _case_id_or_400(case_id)
    if body.case.id != case_id:
        raise HTTPException(400, "case.id must match case_id")
    path = _manual_case_path(case_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    case = body.case.model_copy(update={"meta": {**body.case.meta, "origin": "manual"}})
    if existed:
        BENCHMARK_CASE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup_path = BENCHMARK_CASE_BACKUP_DIR / f"{case_id}.{_now_id()}.json"
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(case.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)
    action = "update_rag_benchmark_case" if existed else "create_rag_benchmark_case"
    audit_request(db, request, action, "rag_benchmark_case", case_id, {"path": _safe_rel_path(path)})
    return {"ok": True, "case": case.model_dump(), "path": _safe_rel_path(path)}


@router.delete("/cases/{case_id}")
def delete_benchmark_case(
    case_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    case_id = _case_id_or_400(case_id)
    path = _manual_case_path(case_id)
    if not path.exists():
        raise HTTPException(404, "manual benchmark case not found")
    BENCHMARK_CASE_TRASH_DIR.mkdir(parents=True, exist_ok=True)
    trash_path = BENCHMARK_CASE_TRASH_DIR / f"{case_id}.{_now_id()}.json"
    os.replace(path, trash_path)
    audit_request(db, request, "delete_rag_benchmark_case", "rag_benchmark_case", case_id, {
        "trash_path": _safe_rel_path(trash_path),
    })
    return {"ok": True, "trash_path": _safe_rel_path(trash_path)}


@router.post("/sample")
def sample_benchmark_cases(body: BenchmarkSampleRequest, _auth=Depends(verify_admin)):
    db_path = get_benchmark_db_path()
    preflight = preflight_readonly(db_path)
    if not preflight["ok"] or db_path is None:
        return {"ok": False, "preflight": preflight, "created": 0, "paths": {}}
    db = _open_readonly_session(db_path)
    try:
        fingerprint = db_fingerprint(db_path, db)
        per_source = _clamp(body.per_source, 1, 50)
        cases = sample_cases(db, per_source=per_source)
        generated_at = datetime.now().isoformat()
        for case in cases:
            case.meta["db_fingerprint"] = fingerprint
            case.meta["generated_at"] = generated_at
            case.meta["generator_version"] = GENERATOR_VERSION
        paths = write_generated_cases(cases, BENCHMARK_GENERATED_DIR)
        return {
            "ok": True,
            "created": len(cases),
            "preflight": preflight,
            "db_fingerprint": fingerprint,
            "paths": {key: _safe_rel_path(path) for key, path in paths.items()},
        }
    finally:
        db.close()


@router.post("/run")
def run_benchmark_web(body: BenchmarkRunRequest, _auth=Depends(verify_admin)):
    db_path = get_benchmark_db_path()
    preflight = preflight_readonly(db_path)
    if not preflight["ok"] or db_path is None:
        return {
            "run_id": "",
            "ok": False,
            "metrics": {"overall": {"total_cases": 0}},
            "score_summary": {"total": 0, "passed": 0, "failed": 0},
            "failed_scores": [],
            "preflight": preflight,
            "stale_generated_cases": [],
            "report_id": "",
            "report_paths": {},
            "started_at": "",
            "finished_at": "",
            "latency_ms": 0,
        }
    provider_mode = body.provider_mode
    if provider_mode not in {"deterministic", "no_reranker_baseline", "runtime"}:
        raise HTTPException(400, "Invalid provider_mode")
    with _run_lock():
        started_at = datetime.now()
        started = time.perf_counter()
        db = _open_readonly_session(db_path)
        try:
            if body.sample_before_run:
                sample_body = BenchmarkSampleRequest(per_source=_clamp(body.per_source, 1, 50))
                cases = sample_cases(db, per_source=sample_body.per_source)
                fingerprint_for_sample = db_fingerprint(db_path, db)
                generated_at = datetime.now().isoformat()
                for case in cases:
                    case.meta["db_fingerprint"] = fingerprint_for_sample
                    case.meta["generated_at"] = generated_at
                    case.meta["generator_version"] = GENERATOR_VERSION
                write_generated_cases(cases, BENCHMARK_GENERATED_DIR)

            current_fp = db_fingerprint(db_path, db)
            selected: list[BenchmarkCase] = []
            stale: list[str] = []
            for case, origin, _editable in _filtered_cases(
                include_manual=body.include_manual,
                include_generated=body.include_generated,
                source_types=body.source_types,
                case_types=body.case_types,
                case_status=body.case_status,
            ):
                if origin == "generated" and not _same_fingerprint(case.meta.get("db_fingerprint"), current_fp):
                    stale.append(case.id)
                    continue
                selected.append(case)
            selected = selected[: _clamp(body.max_cases, 1, 100)]
            candidate_limit = _clamp(body.include_candidates_limit, 0, 50)
            timeout_seconds = _clamp(body.timeout_seconds, 1, 300)
            per_case_timeout = _clamp(body.per_case_timeout_seconds, 1, 120)
            results: list[BenchmarkResult] = []
            scores: list[CaseScore] = []
            timeout_reached = False
            for case in selected:
                if time.perf_counter() - started > timeout_seconds:
                    timeout_reached = True
                    break
                case_started = time.perf_counter()
                result = run_case_with_adapter(db, case, provider_mode=provider_mode, readonly=True)
                if time.perf_counter() - case_started > per_case_timeout:
                    result.fallback_reason = (result.fallback_reason + "; " if result.fallback_reason else "") + "per_case_soft_timeout_exceeded"
                results.append(result)
                scores.append(score_case(case, result))
            metrics = aggregate_scores(selected[:len(scores)], scores)
            report_paths = write_reports(selected[:len(scores)], results, scores, report_out=BENCHMARK_REPORT_DIR)
            failed = [score.model_dump() for score in scores if not score.ok][:50]
            failed_results = [
                _trim_result(result, candidate_limit)
                for result, score in zip(results, scores)
                if not score.ok
            ][:50]
            finished_at = datetime.now()
            return {
                "run_id": str(report_paths["report_id"]),
                "ok": all(score.ok for score in scores) and not timeout_reached,
                "metrics": metrics,
                "score_summary": _summary_scores(scores) | {"timeout_reached": timeout_reached},
                "failed_scores": failed,
                "results": failed_results,
                "preflight": preflight,
                "stale_generated_cases": stale,
                "report_id": str(report_paths["report_id"]),
                "report_paths": {
                    "json": _safe_rel_path(Path(report_paths["json"])),
                    "markdown": _safe_rel_path(Path(report_paths["markdown"])),
                },
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "latency_ms": int((time.perf_counter() - started) * 1000),
            }
        finally:
            db.close()


@router.get("/reports/latest")
def latest_benchmark_report(_auth=Depends(verify_admin)):
    json_path = BENCHMARK_REPORT_DIR / "latest.json"
    md_path = BENCHMARK_REPORT_DIR / "latest.md"
    if not json_path.exists():
        return {"exists": False}
    payload = _readable_json(json_path)
    scores = payload.get("scores") or []
    markdown = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    return {
        "exists": True,
        "metrics": payload.get("metrics") or {},
        "failed_scores": [score for score in scores if not score.get("ok")][:50],
        "markdown": markdown,
        "report_paths": {
            "json": _safe_rel_path(json_path),
            "markdown": _safe_rel_path(md_path),
        },
    }
