"""Admin Eval Workbench 路由。"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.admin.common import audit_request, client_ip, verify_admin
from core.database import AdminAuditLog, get_db
from core.eval_sampling.store import (
    candidate_queue_summary,
    candidate_trend_report,
    defer_candidate,
    get_candidate,
    get_run,
    get_runs,
    ignore_candidate,
    label_candidate,
    list_candidates,
    plan_candidate_batch_audit,
    plan_candidate_promotion,
    preflight_candidate_promotions,
    promote_candidate,
    record_candidate_batch_audit,
    reject_candidate,
    reopen_candidate,
    save_run,
    save_run_results,
    update_candidate,
)
from evals.expected_contract import expected_contract_payload

router = APIRouter(tags=["admin-eval"])

TIMING_TUNING_PROPOSAL_REPORT = Path("evals/reports/timing_tuning_proposal_latest.json")
TIMING_TUNING_REVIEW_DECISIONS = {
    "needs_data",
    "rejected",
    "approved_for_manual_experiment",
    "reviewed_no_change",
}


class TimingTuningProposalReviewRequest(BaseModel):
    decision: str = Field(..., min_length=1, max_length=64)
    reason_code: str = Field(default="", max_length=128)
    note: str = Field(default="", max_length=2000)
    reviewer: str = Field(default="", max_length=128)


def _current_timing_tuning_proposal_report() -> Path:
    admin_routes = sys.modules.get("api.admin_routes")
    if admin_routes is not None and hasattr(admin_routes, "TIMING_TUNING_PROPOSAL_REPORT"):
        return Path(getattr(admin_routes, "TIMING_TUNING_PROPOSAL_REPORT"))
    return Path(TIMING_TUNING_PROPOSAL_REPORT)


def _proposal_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _proposal_missing_response(path: Path) -> dict[str, Any]:
    return {
        "exists": False,
        "report_path": str(path),
        "readiness": {
            "ready": False,
            "blocking_reasons": [
                {
                    "code": "proposal_report_missing",
                    "message": "调参提案报告不存在，请先运行 evals.timing_tuning_proposal",
                }
            ],
        },
    }


def _proposal_review_from_audit(row: AdminAuditLog | None) -> dict[str, Any] | None:
    if row is None:
        return None
    try:
        detail = json.loads(row.detail_json or "{}")
    except json.JSONDecodeError:
        detail = {}
    if not isinstance(detail, dict):
        detail = {}
    detail["audit_id"] = row.id
    detail["created_at"] = row.created_at.isoformat() if row.created_at else ""
    return detail


@router.get("/evals/expected-contract")
def eval_expected_contract(_auth=Depends(verify_admin)):
    return expected_contract_payload()


@router.get("/evals/timing-tuning/proposal")
def eval_timing_tuning_proposal(_auth=Depends(verify_admin)):
    path = _current_timing_tuning_proposal_report()
    if not path.exists():
        return _proposal_missing_response(path)

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"invalid proposal report: {exc}",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=500,
            detail="invalid proposal report: JSON object expected",
        )
    return {"exists": True, "report_path": str(path), "report": payload}


@router.get("/evals/timing-tuning/proposal/review")
def eval_timing_tuning_proposal_review_state(
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    path = _current_timing_tuning_proposal_report()
    if not path.exists():
        payload = _proposal_missing_response(path)
        payload["review"] = None
        payload["proposal_sha256"] = ""
        return payload

    proposal_sha256 = _proposal_sha256(path)
    row = (
        db.query(AdminAuditLog)
        .filter(
            AdminAuditLog.action == "review_timing_tuning_proposal",
            AdminAuditLog.target_type == "timing_tuning_proposal",
            AdminAuditLog.target_id == proposal_sha256,
        )
        .order_by(AdminAuditLog.id.desc())
        .first()
    )
    return {
        "exists": True,
        "report_path": str(path),
        "proposal_sha256": proposal_sha256,
        "review": _proposal_review_from_audit(row),
    }


@router.post("/evals/timing-tuning/proposal/reviews")
def eval_timing_tuning_proposal_review(
    payload: TimingTuningProposalReviewRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    decision = payload.decision.strip()
    if decision not in TIMING_TUNING_REVIEW_DECISIONS:
        raise HTTPException(status_code=422, detail="invalid review decision")

    path = _current_timing_tuning_proposal_report()
    if not path.exists():
        raise HTTPException(status_code=404, detail="proposal report missing")

    proposal_sha256 = _proposal_sha256(path)
    detail = {
        "decision": decision,
        "reason_code": payload.reason_code.strip(),
        "note": payload.note.strip(),
        "reviewer": payload.reviewer.strip(),
        "report_path": str(path),
        "proposal_sha256": proposal_sha256,
    }
    row = AdminAuditLog(
        action="review_timing_tuning_proposal",
        target_type="timing_tuning_proposal",
        target_id=proposal_sha256,
        detail_json=json.dumps(detail, ensure_ascii=False),
        ip_address=client_ip(request),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _proposal_review_from_audit(row)


@router.get("/evals/candidates")
def eval_list_candidates(
    suite: str = "",
    status: str = "",
    source: str = "",
    page: int = 1,
    limit: int = 50,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    items, total = list_candidates(
        db, suite=suite, status=status, source=source,
        limit=max(1, min(limit, 200)),
        offset=(max(page, 1) - 1) * limit,
    )
    summary = candidate_queue_summary(db, suite=suite, status=status, source=source)
    return {"items": items, "total": total, "page": page, "summary": summary}


class CandidatePreflightRequest(BaseModel):
    case_ids: list[str] = Field(default_factory=list)
    suite: str = ""
    status: str = "labeled"
    source: str = ""
    target_dataset: str = ""
    limit: int = 200


@router.post("/evals/candidates/preflight")
def eval_preflight_candidates(
    body: CandidatePreflightRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        return preflight_candidate_promotions(
            db,
            case_ids=body.case_ids,
            suite=body.suite,
            status=body.status,
            source=body.source,
            target_dataset=body.target_dataset,
            limit=body.limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


class CandidateBatchAuditDecision(BaseModel):
    case_id: str
    decision: str = "noop"
    reason_code: str = ""
    note: str = ""
    defer_until: str = ""
    expected_status: str = ""
    expected_updated_at: str = ""


class CandidateBatchAuditRequest(BaseModel):
    dry_run: bool = True
    case_ids: list[str] = Field(default_factory=list)
    suite: str = ""
    status: str = ""
    source: str = ""
    target_dataset: str = ""
    limit: int = 200
    batch_note: str = ""
    decisions: list[CandidateBatchAuditDecision] = Field(default_factory=list)


@router.post("/evals/candidates/batch-audit")
def eval_candidate_batch_audit(
    body: CandidateBatchAuditRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        plan = plan_candidate_batch_audit(
            db,
            case_ids=body.case_ids,
            suite=body.suite,
            status=body.status,
            source=body.source,
            target_dataset=body.target_dataset,
            limit=body.limit,
            batch_note=body.batch_note,
            decisions=[decision.model_dump() for decision in body.decisions],
        )
        if body.dry_run:
            return plan
        if not plan.get("ok"):
            raise ValueError("candidate batch audit has item errors")
        return record_candidate_batch_audit(
            db,
            plan,
            ip_address=client_ip(request),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/evals/candidates/trend")
def eval_candidates_trend(
    days: int = Query(30, ge=1, le=90),
    suite: str = "",
    status: str = "",
    source: str = "",
    target_dataset: str = "",
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        return candidate_trend_report(
            db,
            days=days,
            suite=suite,
            status=status,
            source=source,
            target_dataset=target_dataset,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/evals/candidates/{case_id}")
def eval_get_candidate(case_id: str, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    from core.eval_sampling.store import _candidate_dict
    row = get_candidate(db, case_id)
    if not row:
        raise HTTPException(404, "candidate not found")
    return _candidate_dict(row)


class EvalCandidatePatch(BaseModel):
    priority: Optional[int] = None
    note: Optional[str] = None
    status: Optional[str] = None


@router.patch("/evals/candidates/{case_id}")
def eval_patch_candidate(
    case_id: str, body: EvalCandidatePatch,
    request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    updates = {}
    if body.priority is not None:
        updates["priority"] = body.priority
    if body.note is not None:
        updates["note"] = body.note
    if body.status is not None:
        updates["status"] = body.status
    try:
        result = update_candidate(db, case_id, **updates)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not result:
        raise HTTPException(404, "candidate not found")
    audit_request(db, request, "update_candidate", "eval_candidate", case_id, updates)
    return result


class LabelRequest(BaseModel):
    expected: dict = Field(default_factory=dict)
    expected_json: Optional[dict] = None
    note: str = ""

    def normalized_expected(self) -> dict:
        if self.expected and self.expected_json and self.expected != self.expected_json:
            raise ValueError("expected and expected_json conflict")
        return self.expected or self.expected_json or {}


class PromoteRequest(BaseModel):
    target_dataset: str = "regression"
    dry_run: bool = False


class CandidateTriageRequest(BaseModel):
    reason_code: str = ""
    note: str = ""
    defer_until: str = ""


def _triage_response_or_404(result):
    if not result:
        raise HTTPException(404, "candidate not found")
    return result


@router.post("/evals/candidates/{case_id}/label")
def eval_label_candidate(
    case_id: str, body: LabelRequest,
    request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        expected = body.normalized_expected()
        result = label_candidate(db, case_id, expected, note=body.note or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not result:
        raise HTTPException(404, "candidate not found")
    audit_request(db, request, "label_candidate", "eval_candidate", case_id,
                  {"expected_keys": list(expected.keys())})
    return result


@router.post("/evals/candidates/{case_id}/reject")
def eval_reject_candidate(
    case_id: str,
    body: CandidateTriageRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        result = reject_candidate(
            db,
            case_id,
            reason_code=body.reason_code,
            note=body.note,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    result = _triage_response_or_404(result)
    audit_request(db, request, "reject_candidate", "eval_candidate", case_id, result["audit"])
    return result["candidate"]


@router.post("/evals/candidates/{case_id}/defer")
def eval_defer_candidate(
    case_id: str,
    body: CandidateTriageRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        result = defer_candidate(
            db,
            case_id,
            reason_code=body.reason_code,
            note=body.note,
            defer_until=body.defer_until,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    result = _triage_response_or_404(result)
    audit_request(db, request, "defer_candidate", "eval_candidate", case_id, result["audit"])
    return result["candidate"]


@router.post("/evals/candidates/{case_id}/reopen")
def eval_reopen_candidate(
    case_id: str,
    body: CandidateTriageRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        result = reopen_candidate(
            db,
            case_id,
            reason_code=body.reason_code,
            note=body.note,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    result = _triage_response_or_404(result)
    audit_request(db, request, "reopen_candidate", "eval_candidate", case_id, result["audit"])
    return result["candidate"]


@router.post("/evals/candidates/{case_id}/ignore")
def eval_ignore_candidate(
    case_id: str, request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        result = ignore_candidate(db, case_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not result:
        raise HTTPException(404, "candidate not found")
    audit_request(db, request, "ignore_candidate", "eval_candidate", case_id)
    return result


@router.post("/evals/candidates/{case_id}/promote")
def eval_promote_candidate(
    case_id: str, request: Request, body: PromoteRequest | None = None,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    body = body or PromoteRequest()
    try:
        if body.dry_run:
            plan = plan_candidate_promotion(db, case_id, target_dataset=body.target_dataset)
            audit_request(
                db,
                request,
                "promote_candidate_dry_run",
                "eval_candidate",
                case_id,
                {"path": plan["path"], "target_dataset": plan["target_dataset"]},
            )
            return {"dry_run": True, **plan}
        plan = plan_candidate_promotion(db, case_id, target_dataset=body.target_dataset)
        path = promote_candidate(db, case_id, target_dataset=body.target_dataset)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not path:
        raise HTTPException(404, "candidate not found")
    audit_request(
        db,
        request,
        "promote_candidate",
        "eval_candidate",
        case_id,
        {"path": path, "target_dataset": body.target_dataset},
    )
    return {
        "ok": True,
        "dry_run": False,
        "case_id": plan["case_id"],
        "suite": plan["suite"],
        "target_dataset": plan["target_dataset"],
        "path": path,
    }


@router.post("/evals/sample/run")
async def eval_run_sample(
    request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.eval_sampling.scheduler import run_sampling_cycle
    created = await run_sampling_cycle()
    audit_request(db, request, "run_eval_sample", "eval", "", {"created": created})
    return {"ok": True, "created": created}


@router.get("/evals/sample/status")
def eval_sample_status(db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    from core.database import EvalSampleCursor
    rows = db.query(EvalSampleCursor).order_by(EvalSampleCursor.id.desc()).all()
    return {
        "cursors": [
            {
                "source_type": r.source_type,
                "source_key": r.source_key,
                "cursor": json.loads(r.cursor_json or "{}"),
                "updated_at": str(r.updated_at) if r.updated_at else "",
            }
            for r in rows
        ]
    }


class EvalRunRequest(BaseModel):
    suite: str = "regression"
    include_candidates: bool = False


@router.post("/evals/run")
def eval_run_suite(
    body: EvalRunRequest,
    request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from evals.run import run_suite_with_details

    try:
        report, case_results = run_suite_with_details(
            body.suite, include_candidates=body.include_candidates)
    except Exception as e:
        raise HTTPException(500, f"eval run failed: {e}")

    run_record = save_run(db, body.suite, {
        "total": report.total,
        "passed": report.passed,
        "failed": report.failed,
        "pass_rate": report.pass_rate,
        "summary": {"failed_cases": report.failed_cases or []},
    })
    save_run_results(db, run_record.id, case_results)

    audit_request(db, request, "run_eval_suite", "eval", body.suite,
                  {"run_id": run_record.id, "passed": report.passed, "failed": report.failed})

    return {
        "run_id": run_record.id,
        "suite": report.suite,
        "total": report.total,
        "passed": report.passed,
        "failed": report.failed,
        "pass_rate": report.pass_rate,
        "failed_cases": report.failed_cases or [],
    }


@router.get("/evals/runs")
def eval_list_runs(
    limit: int = 20, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    items = get_runs(db, limit=max(1, min(limit, 100)))
    return {"items": items}


@router.get("/evals/runs/{run_id}")
def eval_get_run(
    run_id: int, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    run_dict, results = get_run(db, run_id)
    if not run_dict:
        raise HTTPException(404, "run not found")
    return {"run": run_dict, "results": results}
