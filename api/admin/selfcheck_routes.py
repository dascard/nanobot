"""自检能力清单与覆盖缺口的只读 Admin API。"""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from api.admin.common import verify_admin
from api.admin.endpoint_registry import ADMIN_ENDPOINT_CONTRACT_REGISTRY
from api.admin.selfcheck_models import (
    SelfcheckCapabilitiesResponse,
    SelfcheckProbeCatalogResponse,
    SelfcheckRunListResponse,
    SelfcheckRunRequest,
    SelfcheckRunResponse,
)
from api.endpoint_contracts import standard_error_responses
from core.agent_runtime.gateway import get_agent_runtime_registry
from core.database import get_db
from core.db.models.selfcheck import SelfcheckResultRow, SelfcheckRunRow
from core.selfcheck.capabilities import (
    build_capability_registry,
    capability_coverage_summary,
)
from core.selfcheck.engine import SelfcheckEngine
from core.selfcheck.probes import SELFCHECK_PROBE_REGISTRY


router = APIRouter(tags=["admin-self-check"])


def _registered_agent_registry() -> object | None:
    try:
        return get_agent_runtime_registry()
    except RuntimeError:
        return None


def _registered_agent_descriptors() -> tuple[object, ...]:
    registry = _registered_agent_registry()
    return tuple(registry.descriptors()) if registry is not None else ()


def selfcheck_capabilities_payload(request: Request) -> dict[str, object]:
    snapshot = build_capability_registry(
        request.app,
        agent_descriptors=_registered_agent_descriptors(),
        endpoint_contracts=(
            ADMIN_ENDPOINT_CONTRACT_REGISTRY.registry_snapshot
        ),
    )
    return {
        "registry": {
            "namespace": snapshot.namespace,
            "generation": snapshot.generation,
            "sha256": snapshot.sha256,
        },
        "coverage": capability_coverage_summary(snapshot),
        "items": [descriptor.to_public_dict() for descriptor in snapshot],
    }


@router.get(
    "/self-check/capabilities",
    operation_id="get_api_v1_admin_self_check_capabilities",
    response_model=SelfcheckCapabilitiesResponse,
    responses=standard_error_responses(401, 503),
)
def selfcheck_capabilities(
    request: Request,
    _auth=Depends(verify_admin),
):
    return selfcheck_capabilities_payload(request)


def _probe_payload(probe: object) -> dict[str, object]:
    return {
        "check_id": probe.check_id,
        "category": probe.category,
        "label": probe.label,
        "level": probe.level,
        "severity": probe.severity,
        "executor_key": probe.executor_key,
        "timeout_seconds": probe.timeout_seconds,
        "environments": list(probe.environments),
        "capability_kinds": list(probe.capability_kinds),
        "capability_source_ids": list(probe.capability_source_ids),
        "destructive": probe.destructive,
        "requires_model": probe.requires_model,
    }


@router.get(
    "/self-check/probes",
    operation_id="get_api_v1_admin_self_check_probes",
    response_model=SelfcheckProbeCatalogResponse,
    responses=standard_error_responses(401),
)
def selfcheck_probes(_auth=Depends(verify_admin)):
    return {
        "registry": {
            "namespace": SELFCHECK_PROBE_REGISTRY.namespace,
            "generation": SELFCHECK_PROBE_REGISTRY.generation,
            "sha256": SELFCHECK_PROBE_REGISTRY.sha256,
        },
        "items": [_probe_payload(probe) for probe in SELFCHECK_PROBE_REGISTRY],
    }


@router.post(
    "/self-check/runs",
    operation_id="post_api_v1_admin_self_check_runs",
    response_model=SelfcheckRunResponse,
    responses=standard_error_responses(401, 422, 503),
)
def create_selfcheck_run(
    body: SelfcheckRunRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(verify_admin),
):
    try:
        report = SelfcheckEngine(
            app=request.app,
            db=db,
            testing=os.environ.get("NANOBOT_TESTING") == "1",
            agent_descriptors=_registered_agent_descriptors(),
            agent_registry=_registered_agent_registry(),
            allow_model_checks=body.allow_model_checks,
            endpoint_contracts=(
                ADMIN_ENDPOINT_CONTRACT_REGISTRY.registry_snapshot
            ),
        ).run(
            trigger=body.trigger,
            requested_by=str(admin),
            check_ids=body.check_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return report.to_dict()


def _json_object(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _json_list(raw: str | None) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _run_list_item(row: SelfcheckRunRow) -> dict[str, object]:
    return {
        "run_id": row.run_id,
        "trigger": row.trigger,
        "environment": row.environment,
        "status": row.status,
        "capability_registry_sha256": row.capability_registry_sha256,
        "probe_registry_sha256": row.probe_registry_sha256,
        "summary": _json_object(row.summary_json),
        "started_at": row.started_at,
        "completed_at": row.completed_at,
    }


def _result_item(row: SelfcheckResultRow) -> dict[str, object]:
    probe = SELFCHECK_PROBE_REGISTRY.get(row.check_id)
    return {
        "check_id": row.check_id,
        "category": row.category,
        "status": row.status,
        "severity": row.severity,
        "level": probe.level if probe is not None else "unknown",
        "duration_ms": row.duration_ms,
        "detail_code": row.detail_code,
        "message": row.message,
        "capability_ids": _json_list(row.capability_ids_json),
        "metrics": _json_object(row.metrics_json),
        "evidence": _json_object(row.evidence_json),
        "started_at": row.started_at,
        "completed_at": row.completed_at,
    }


@router.get(
    "/self-check/runs",
    operation_id="get_api_v1_admin_self_check_runs",
    response_model=SelfcheckRunListResponse,
    responses=standard_error_responses(401),
)
def list_selfcheck_runs(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    query = db.query(SelfcheckRunRow)
    return {
        "total": query.count(),
        "items": [
            _run_list_item(row)
            for row in query.order_by(SelfcheckRunRow.started_at.desc()).limit(limit).all()
        ],
    }


@router.get(
    "/self-check/runs/{run_id}",
    operation_id="get_api_v1_admin_self_check_runs_run_id",
    response_model=SelfcheckRunResponse,
    responses=standard_error_responses(401, 404),
)
def get_selfcheck_run(
    run_id: str,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    row = db.get(SelfcheckRunRow, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="selfcheck run not found")
    payload = _run_list_item(row)
    payload["results"] = [
        _result_item(result)
        for result in (
            db.query(SelfcheckResultRow)
            .filter(SelfcheckResultRow.run_id == run_id)
            .order_by(SelfcheckResultRow.id.asc())
            .all()
        )
    ]
    if row.completed_at is None:
        raise HTTPException(status_code=503, detail="selfcheck run still running")
    return payload


__all__ = [
    "router",
    "selfcheck_capabilities",
    "selfcheck_capabilities_payload",
    "create_selfcheck_run",
    "get_selfcheck_run",
    "list_selfcheck_runs",
    "selfcheck_probes",
]
