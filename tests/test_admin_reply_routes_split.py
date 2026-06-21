from __future__ import annotations

import inspect
from pathlib import Path


_ADMIN_REPLY_ROUTE_SIGNATURES = (
    ("POST", "/api/v1/admin/reply-test/run"),
    ("GET", "/api/v1/admin/reply-eval/cases"),
    ("POST", "/api/v1/admin/reply-eval/cases"),
    ("PUT", "/api/v1/admin/reply-eval/cases/{case_id}"),
    ("DELETE", "/api/v1/admin/reply-eval/cases/{case_id}"),
    ("POST", "/api/v1/admin/reply-eval/generate-preview"),
    ("POST", "/api/v1/admin/reply-eval/save-generated"),
    ("POST", "/api/v1/admin/reply-eval/run"),
    ("GET", "/api/v1/admin/reply-eval/traffic"),
    ("GET", "/api/v1/admin/reply-eval/runs"),
    ("GET", "/api/v1/admin/reply-eval/runs/{run_id}"),
)


_REPLY_ROUTE_EXPORTS = (
    "ReplyTestRunRequest",
    "ReplyEvalCaseIn",
    "ReplyEvalCasePatch",
    "ReplyEvalSaveGeneratedIn",
    "ReplyEvalRunIn",
    "_loads_json_list",
    "_reply_case_to_dict",
    "_reply_eval_run_to_dict",
    "_reply_log_attempt",
    "_reply_contract_has_final_action",
    "_reply_contract_run_key",
    "_is_reply_eval_test_session",
    "_safe_rate",
    "_resolve_reply_test_prompt_settings",
    "_run_reply_test_once",
    "_upsert_reply_eval_case",
    "reply_test_run",
    "reply_eval_list_cases",
    "reply_eval_create_case",
    "reply_eval_update_case",
    "reply_eval_delete_case",
    "reply_eval_generate_preview",
    "reply_eval_save_generated",
    "reply_eval_run",
    "reply_eval_real_traffic",
    "reply_eval_list_runs",
    "reply_eval_get_run",
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


def test_admin_reply_routes_are_registered_from_split_module():
    for method, path in _ADMIN_REPLY_ROUTE_SIGNATURES:
        routes = _admin_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.reply_routes"}


def test_legacy_admin_routes_reply_imports_still_work():
    from api import admin_routes
    from api.admin import reply_routes

    for name in _REPLY_ROUTE_EXPORTS:
        assert getattr(admin_routes, name) is getattr(reply_routes, name)

    body = admin_routes.ReplyTestRunRequest(message="你在吗")
    assert body.prompt_engine == "prompt"
    assert body.variant == "v2_code_retry"
    assert admin_routes._resolve_reply_test_prompt_settings(body) == ("prompt", "prompt", True)
    assert admin_routes._safe_rate(1, 4) == 0.25


def test_split_reply_routes_use_legacy_admin_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/admin/reply-eval/cases",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/reply-eval/cases",
        headers={"Authorization": "Bearer test-token"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_admin_reply_routes_are_not_registered_twice():
    for method, path in _ADMIN_REPLY_ROUTE_SIGNATURES:
        assert len(_admin_routes_for(path, method)) == 1, f"{method} {path}"


def test_admin_reply_static_routes_before_dynamic_run_id_route():
    route_paths = [path for path, _route in _admin_route_entries()]

    runs_index = route_paths.index("/api/v1/admin/reply-eval/runs")
    run_id_index = route_paths.index("/api/v1/admin/reply-eval/runs/{run_id}")

    assert runs_index < run_id_index


def test_admin_reply_async_boundaries_remain_coroutines():
    from api.admin import reply_routes

    assert inspect.iscoroutinefunction(reply_routes._run_reply_test_once)
    assert inspect.iscoroutinefunction(reply_routes.reply_test_run)
    assert inspect.iscoroutinefunction(reply_routes.reply_eval_run)


def test_admin_reply_routes_do_not_import_parent_admin_routes_or_sync_awaitable():
    path = Path("api/admin/reply_routes.py")

    assert path.exists()
    source = path.read_text(encoding="utf-8")

    assert "from api.admin_routes" not in source
    assert "import api.admin_routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
