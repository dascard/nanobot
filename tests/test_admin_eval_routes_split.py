from __future__ import annotations

import inspect
from pathlib import Path


_ADMIN_EVAL_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/admin/evals/expected-contract"),
    ("GET", "/api/v1/admin/evals/harness/catalog"),
    ("GET", "/api/v1/admin/evals/timing-tuning/proposal"),
    ("GET", "/api/v1/admin/evals/timing-tuning/proposal/review"),
    ("POST", "/api/v1/admin/evals/timing-tuning/proposal/reviews"),
    ("GET", "/api/v1/admin/evals/candidates"),
    ("POST", "/api/v1/admin/evals/candidates/preflight"),
    ("POST", "/api/v1/admin/evals/candidates/batch-audit"),
    ("GET", "/api/v1/admin/evals/candidates/trend"),
    ("GET", "/api/v1/admin/evals/candidates/{case_id}"),
    ("PATCH", "/api/v1/admin/evals/candidates/{case_id}"),
    ("POST", "/api/v1/admin/evals/candidates/{case_id}/label"),
    ("POST", "/api/v1/admin/evals/candidates/{case_id}/reject"),
    ("POST", "/api/v1/admin/evals/candidates/{case_id}/defer"),
    ("POST", "/api/v1/admin/evals/candidates/{case_id}/reopen"),
    ("POST", "/api/v1/admin/evals/candidates/{case_id}/ignore"),
    ("POST", "/api/v1/admin/evals/candidates/{case_id}/promote"),
    ("POST", "/api/v1/admin/evals/sample/run"),
    ("GET", "/api/v1/admin/evals/sample/status"),
    ("POST", "/api/v1/admin/evals/replay/compare"),
    ("POST", "/api/v1/admin/evals/replay/fault-matrix"),
    ("POST", "/api/v1/admin/evals/run"),
    ("GET", "/api/v1/admin/evals/runs"),
    ("GET", "/api/v1/admin/evals/runs/{run_id}"),
)


_EVAL_ROUTE_EXPORTS = (
    "TimingTuningProposalReviewRequest",
    "CandidatePreflightRequest",
    "CandidateBatchAuditDecision",
    "CandidateBatchAuditRequest",
    "EvalCandidatePatch",
    "LabelRequest",
    "PromoteRequest",
    "CandidateTriageRequest",
    "EvalReplayCompareRequest",
    "EvalReplayFaultMatrixRequest",
    "EvalRunRequest",
    "TIMING_TUNING_PROPOSAL_REPORT",
    "TIMING_TUNING_REVIEW_DECISIONS",
    "_current_timing_tuning_proposal_report",
    "_proposal_sha256",
    "_proposal_missing_response",
    "_proposal_review_from_audit",
    "_triage_response_or_404",
    "eval_expected_contract",
    "eval_harness_catalog",
    "eval_timing_tuning_proposal",
    "eval_timing_tuning_proposal_review_state",
    "eval_timing_tuning_proposal_review",
    "eval_list_candidates",
    "eval_preflight_candidates",
    "eval_candidate_batch_audit",
    "eval_candidates_trend",
    "eval_get_candidate",
    "eval_patch_candidate",
    "eval_label_candidate",
    "eval_reject_candidate",
    "eval_defer_candidate",
    "eval_reopen_candidate",
    "eval_ignore_candidate",
    "eval_promote_candidate",
    "eval_run_sample",
    "eval_sample_status",
    "eval_replay_compare",
    "eval_replay_fault_matrix",
    "eval_run_suite",
    "eval_list_runs",
    "eval_get_run",
)


def _admin_route_entries():
    from server import app

    def _iter_routes(routes, prefix: str = ""):
        for route in routes:
            endpoint = getattr(route, "endpoint", None)
            route_path = getattr(route, "path", None)
            if endpoint is not None and route_path is not None:
                yield prefix + route_path, route
                continue

            original_router = getattr(route, "original_router", None)
            if original_router is None:
                continue
            include_context = getattr(route, "include_context", None)
            include_prefix = getattr(include_context, "prefix", "")
            yield from _iter_routes(original_router.routes, prefix + include_prefix)

    return list(_iter_routes(app.routes))


def _admin_routes_for(path: str, method: str | None = None):
    return [
        route
        for route_path, route in _admin_route_entries()
        if route_path == path and (method is None or method in getattr(route, "methods", set()))
    ]


def test_admin_eval_routes_are_registered_from_split_module():
    for method, path in _ADMIN_EVAL_ROUTE_SIGNATURES:
        routes = _admin_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.eval_routes"}


def test_legacy_admin_routes_eval_imports_still_work():
    from api import admin_routes
    from api.admin import eval_routes

    for name in _EVAL_ROUTE_EXPORTS:
        assert getattr(admin_routes, name) is getattr(eval_routes, name)

    body = admin_routes.LabelRequest(expected_json={"timing_action": "continue"})
    assert body.normalized_expected() == {"timing_action": "continue"}
    assert admin_routes.CandidatePreflightRequest().status == "labeled"
    assert "needs_data" in admin_routes.TIMING_TUNING_REVIEW_DECISIONS


def test_split_eval_routes_use_legacy_admin_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/admin/evals/expected-contract",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/evals/expected-contract",
        headers={"Authorization": "Bearer test-token"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_split_eval_routes_use_legacy_proposal_report_monkeypatch(client, monkeypatch, tmp_path):
    from api import admin_routes

    report = tmp_path / "proposal.json"
    report.write_text('{"proposal_version": 1}', encoding="utf-8")
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(admin_routes, "TIMING_TUNING_PROPOSAL_REPORT", report, raising=False)

    response = client.get(
        "/api/v1/admin/evals/timing-tuning/proposal",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert response.json()["report_path"] == str(report)


def test_admin_eval_routes_are_not_registered_twice():
    for method, path in _ADMIN_EVAL_ROUTE_SIGNATURES:
        assert len(_admin_routes_for(path, method)) == 1, f"{method} {path}"


def test_admin_eval_static_candidate_routes_before_dynamic_case_id_route():
    route_paths = [path for path, _route in _admin_route_entries()]

    dynamic_index = route_paths.index("/api/v1/admin/evals/candidates/{case_id}")
    assert route_paths.index("/api/v1/admin/evals/candidates/preflight") < dynamic_index
    assert route_paths.index("/api/v1/admin/evals/candidates/batch-audit") < dynamic_index
    assert route_paths.index("/api/v1/admin/evals/candidates/trend") < dynamic_index


def test_admin_eval_static_runs_route_before_dynamic_run_id_route():
    route_paths = [path for path, _route in _admin_route_entries()]

    runs_index = route_paths.index("/api/v1/admin/evals/runs")
    run_id_index = route_paths.index("/api/v1/admin/evals/runs/{run_id}")

    assert runs_index < run_id_index


def test_admin_eval_async_boundaries_remain_coroutines():
    from api.admin import eval_routes

    assert inspect.iscoroutinefunction(eval_routes.eval_run_sample)


def test_admin_eval_routes_do_not_import_parent_admin_routes_or_sync_awaitable():
    path = Path("api/admin/eval_routes.py")

    assert path.exists()
    source = path.read_text(encoding="utf-8")

    assert "from api.admin_routes" not in source
    assert "import api.admin_routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
