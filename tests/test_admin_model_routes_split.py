from __future__ import annotations

import asyncio
from pathlib import Path
import threading

import pytest


_ADMIN_MODEL_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/admin/models/status"),
    ("POST", "/api/v1/admin/models/chat-test"),
    ("GET", "/api/v1/admin/model-catalog"),
    ("PATCH", "/api/v1/admin/model-catalog/{model_id}"),
    ("GET", "/api/v1/admin/models/providers"),
    ("PUT", "/api/v1/admin/models/providers/{provider_id}"),
    ("GET", "/api/v1/admin/models/catalog"),
    ("GET", "/api/v1/admin/models/route-references"),
    ("POST", "/api/v1/admin/models/catalog/refresh"),
    ("GET", "/api/v1/admin/model-routes"),
    ("PATCH", "/api/v1/admin/model-routes/{stage}"),
    ("PUT", "/api/v1/admin/models/routes/{route_key}"),
    ("POST", "/api/v1/admin/models/routes/{route_key}/test"),
    ("GET", "/api/v1/admin/models/routes/{route_key}/resolved"),
    ("GET", "/api/v1/admin/models/available"),
    ("POST", "/api/v1/admin/models/local/{component}/test"),
    ("POST", "/api/v1/admin/models/local/{component}/warmup"),
    ("POST", "/api/v1/admin/models/timing-gate-stability-test"),
    ("POST", "/api/v1/admin/models/health-check"),
)


_MODEL_ROUTE_EXPORTS = (
    "ChatModelTestRequest",
    "ProviderUpdateBody",
    "ModelCatalogPatch",
    "ModelRoutePatch",
    "ModelRouteEditBody",
    "TimingGateStabilityRequest",
    "_ALLOWED_TIERS",
    "_STAGE_META",
    "_ROUTE_SETTING_MAP",
    "_CLASSIFIER_ROUTE_KEYS",
    "_ROUTE_ALIAS",
    "_CHAT_ROUTES",
    "_TINY_TEST_PNG",
    "_resolve_route_value",
    "_resolve_route_key",
    "_redact",
    "_test_nli_contradiction",
    "models_status",
    "chat_model_test",
    "get_model_catalog",
    "patch_model_catalog",
    "list_model_providers",
    "update_model_provider",
    "get_model_catalog_v2",
    "get_route_references",
    "refresh_model_catalog",
    "get_model_routes",
    "patch_model_route",
    "edit_model_route",
    "test_model_route",
    "get_resolved_route",
    "list_available_models",
    "test_local_component",
    "warmup_local_component",
    "timing_gate_stability_test",
    "model_health_check",
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


def test_admin_model_routes_are_registered_from_split_module():
    for method, path in _ADMIN_MODEL_ROUTE_SIGNATURES:
        routes = _admin_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.model_routes"}


def test_model_replies_stays_in_parent_admin_routes():
    routes = _admin_routes_for("/api/v1/admin/model-replies", "GET")

    assert routes
    assert {route.endpoint.__module__ for route in routes} == {"api.admin_routes"}


def test_legacy_admin_routes_model_imports_still_work():
    from api import admin_routes
    from api.admin import model_routes

    for name in _MODEL_ROUTE_EXPORTS:
        assert getattr(admin_routes, name) is getattr(model_routes, name)

    assert admin_routes.ChatModelTestRequest(model="x").model == "x"
    assert admin_routes.ProviderUpdateBody(enabled=True).enabled is True
    assert admin_routes._resolve_route_key("vision")[1] == "sticker_describe"
    assert admin_routes._redact({"x.api_key": "secret", "x.model": "m"}) == {
        "x.api_key": "***",
        "x.model": "m",
    }


def test_split_model_routes_use_legacy_admin_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/admin/model-catalog",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/model-catalog",
        headers={"Authorization": "Bearer test-token"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_admin_model_routes_are_not_registered_twice():
    for method, path in _ADMIN_MODEL_ROUTE_SIGNATURES:
        assert len(_admin_routes_for(path, method)) == 1, f"{method} {path}"


def test_admin_model_routes_do_not_import_parent_admin_routes_or_sync_awaitable():
    path = Path("api/admin/model_routes.py")

    assert path.exists()
    source = path.read_text(encoding="utf-8")

    assert "from api.admin_routes" not in source
    assert "import api.admin_routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


@pytest.mark.asyncio
async def test_timing_gate_stability_offloads_each_run_serially(monkeypatch):
    from api.admin import model_routes
    from clients import classifier_client

    judge_started = threading.Event()
    release_judge = threading.Event()
    event_loop_thread_id = threading.get_ident()
    real_to_thread = asyncio.to_thread

    class FakeTimingGate:
        def __init__(self):
            self.contexts: list[str] = []
            self.thread_ids: list[int] = []
            self._lock = threading.Lock()

        def judge(self, context: str) -> dict:
            with self._lock:
                index = len(self.contexts)
                self.contexts.append(context)
                self.thread_ids.append(threading.get_ident())
            if index == 0:
                judge_started.set()
                if not release_judge.wait(timeout=5):
                    raise TimeoutError("timing gate test did not release first judge call")
            return results[index]

    results = (
        {
            "action": "reply",
            "reason": "case-a-run-0",
            "delay_seconds": 0,
            "error_type": None,
            "raw": "raw-a-0",
        },
        {
            "action": "no_reply",
            "reason": "case-a-run-1",
            "delay_seconds": 1,
            "error_type": "parse_error",
            "raw": "raw-a-1",
        },
        {
            "action": "no_reply",
            "reason": "case-b-run-0",
            "delay_seconds": 2,
            "error_type": None,
            "raw": "raw-b-0",
        },
        {
            "action": "reply",
            "reason": "case-b-run-1",
            "delay_seconds": 3,
            "error_type": None,
            "raw": "raw-b-1",
        },
    )
    gate = FakeTimingGate()
    to_thread_calls: list[tuple[object, tuple[object, ...]]] = []
    active = 0
    max_active = 0

    async def to_thread_spy(func, /, *args, **kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            to_thread_calls.append((func, args))
            return await real_to_thread(func, *args, **kwargs)
        finally:
            active -= 1

    monkeypatch.setattr(classifier_client, "get_timing_gate", lambda: gate)
    monkeypatch.setattr(model_routes.asyncio, "to_thread", to_thread_spy)

    endpoint_task = asyncio.create_task(
        model_routes.timing_gate_stability_test(
            model_routes.TimingGateStabilityRequest(
                cases=[
                    {
                        "name": "案例 A",
                        "context": "<recent>CASE_A</recent>",
                        "pending_count": 1,
                    },
                    {
                        "name": "案例 B",
                        "context": "<recent>CASE_B</recent>",
                        "pending_count": 2,
                    },
                ],
                runs=2,
            ),
            _auth="admin",
        )
    )
    started = await real_to_thread(judge_started.wait, 5)
    probe_ran_while_judge_blocked = False

    async def event_loop_probe():
        nonlocal probe_ran_while_judge_blocked
        probe_ran_while_judge_blocked = (
            judge_started.is_set() and not release_judge.is_set()
        )

    try:
        if started:
            await asyncio.create_task(event_loop_probe())
    finally:
        release_judge.set()
    result = await endpoint_task

    assert started is True
    assert probe_ran_while_judge_blocked is True
    assert len(to_thread_calls) == 4
    assert max_active == 1
    assert len(gate.contexts) == 4
    assert gate.contexts == [call_args[0] for _, call_args in to_thread_calls]
    assert all(thread_id != event_loop_thread_id for thread_id in gate.thread_ids)
    assert ["CASE_A" in context for context in gate.contexts] == [True, True, False, False]

    assert result["dry_run"] is True
    assert [case["name"] for case in result["cases"]] == ["案例 A", "案例 B"]
    case_a, case_b = result["cases"]
    assert [run["index"] for run in case_a["runs"]] == [0, 1]
    assert [run["reason"] for run in case_a["runs"]] == ["case-a-run-0", "case-a-run-1"]
    assert case_a["run_count"] == 2
    assert case_a["parse_error_count"] == 1
    assert case_a["parse_error_ratio"] == 0.5
    assert case_a["action_dist"] == {"reply": 1, "no_reply": 1}
    assert case_a["error_dist"] == {"none": 1, "parse_error": 1}
    assert case_a["raw_samples"] == ["raw-a-0", "raw-a-1"]

    assert [run["index"] for run in case_b["runs"]] == [0, 1]
    assert [run["reason"] for run in case_b["runs"]] == ["case-b-run-0", "case-b-run-1"]
    assert case_b["run_count"] == 2
    assert case_b["parse_error_count"] == 0
    assert case_b["parse_error_ratio"] == 0.0
    assert case_b["action_dist"] == {"no_reply": 1, "reply": 1}
    assert case_b["error_dist"] == {"none": 2}
    assert case_b["raw_samples"] == ["raw-b-0", "raw-b-1"]

    assert result["overall_parse_error_count"] == 1
    assert result["overall_parse_error_ratio"] == 0.25
