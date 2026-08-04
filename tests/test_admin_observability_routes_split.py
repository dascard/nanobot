from __future__ import annotations


_TRACE_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/admin/agent-runs"),
    ("GET", "/api/v1/admin/agent-runs/{run_id}"),
    ("GET", "/api/v1/admin/tool-calls"),
    ("GET", "/api/v1/admin/tool-calls/{tool_call_id}"),
    ("GET", "/api/v1/admin/llm-api-logs"),
    ("GET", "/api/v1/admin/llm-api-logs/{log_id}"),
)


_RUN_EVIDENCE_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/admin/agent-runs/{run_id}/evidence/governance"),
    ("GET", "/api/v1/admin/agent-runs/{run_id}/evidence/export-manifest"),
    ("PUT", "/api/v1/admin/agent-runs/{run_id}/evidence/legal-holds/{hold_id}"),
    ("DELETE", "/api/v1/admin/agent-runs/{run_id}/evidence/legal-holds/{hold_id}"),
    ("POST", "/api/v1/admin/agent-runs/{run_id}/evidence/erasure-preview"),
    ("DELETE", "/api/v1/admin/agent-runs/{run_id}/evidence"),
)


_LOG_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/admin/audit-logs"),
    ("GET", "/api/v1/admin/logs"),
    ("POST", "/api/v1/admin/logs/frontend-error"),
    ("GET", "/api/v1/admin/logs/{name}"),
)


_TRACE_ROUTE_EXPORTS = (
    "list_agent_runs",
    "get_agent_run",
    "list_tool_calls",
    "get_tool_call",
    "list_llm_api_logs",
    "get_llm_api_log",
)


_LOG_ROUTE_EXPORTS = (
    "FrontendErrorBody",
    "_is_allowed_log_name",
    "_log_level_of",
    "_group_log_level_events",
    "list_audit_logs",
    "list_log_files",
    "read_log",
    "log_frontend_error",
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


def test_admin_trace_routes_are_registered_from_split_module():
    for method, path in _TRACE_ROUTE_SIGNATURES:
        routes = _admin_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.trace_routes"}


def test_admin_log_routes_are_registered_from_split_module():
    for method, path in _LOG_ROUTE_SIGNATURES:
        routes = _admin_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.log_routes"}


def test_admin_run_evidence_routes_are_registered_from_split_module():
    for method, path in _RUN_EVIDENCE_ROUTE_SIGNATURES:
        routes = _admin_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {
            "api.admin.run_evidence_routes"
        }


def test_legacy_admin_routes_observability_imports_still_work():
    from api import admin_routes
    from api.admin import log_routes, trace_routes

    for name in _TRACE_ROUTE_EXPORTS:
        assert getattr(admin_routes, name) is getattr(trace_routes, name)

    for name in _LOG_ROUTE_EXPORTS:
        assert getattr(admin_routes, name) is getattr(log_routes, name)

    assert admin_routes.FrontendErrorBody(message="x").message == "x"
    assert admin_routes._is_allowed_log_name("nanobot.log")


def test_split_observability_routes_use_legacy_admin_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/admin/agent-runs",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/agent-runs",
        headers={"Authorization": "Bearer test-token"},
    )
    log_ok = client.post(
        "/api/v1/admin/logs/frontend-error",
        json={"message": "split auth smoke"},
        headers={"Authorization": "Bearer split-token"},
    )
    log_wrong = client.post(
        "/api/v1/admin/logs/frontend-error",
        json={"message": "split auth smoke"},
        headers={"Authorization": "Bearer test-token"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401
    assert log_ok.status_code == 200
    assert log_wrong.status_code == 401


def test_admin_observability_routes_are_not_registered_twice():
    for method, path in (
        _TRACE_ROUTE_SIGNATURES
        + _RUN_EVIDENCE_ROUTE_SIGNATURES
        + _LOG_ROUTE_SIGNATURES
    ):
        assert len(_admin_routes_for(path, method)) == 1, f"{method} {path}"


def test_admin_log_routes_keep_static_paths_before_dynamic_log_name():
    route_paths = [path for path, _route in _admin_route_entries()]

    logs_index = route_paths.index("/api/v1/admin/logs")
    frontend_error_index = route_paths.index("/api/v1/admin/logs/frontend-error")
    read_log_index = route_paths.index("/api/v1/admin/logs/{name}")

    assert logs_index < read_log_index
    assert frontend_error_index < read_log_index
