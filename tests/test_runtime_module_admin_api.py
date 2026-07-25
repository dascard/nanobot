"""Runtime Module Diagnostics 的类型化 Admin API 测试。"""

from __future__ import annotations

from tests.async_helpers import run_async


HEADERS = {"Authorization": "Bearer runtime-module-test-token"}


def _enable_admin(monkeypatch) -> None:
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "runtime-module-test-token",
    )


class _RuntimeModule:
    def __init__(self) -> None:
        from core.modules import ModuleContributionRef, ModuleManifest

        self._started = False
        self._manifest = ModuleManifest(
            module_id="runtime.test",
            version="1.0.0",
            owner="tests",
            domain="runtime",
            provided_capabilities=("runtime.test.port",),
            contributions=(
                ModuleContributionRef(
                    kind="endpoint",
                    contribution_id="runtime.test",
                ),
            ),
            startup_phase=10,
            shutdown_phase=10,
            health_checks=("lifecycle",),
            readiness_checks=("lifecycle",),
            release_impacts=("runtime",),
        )

    def manifest(self):
        return self._manifest

    def register(self, builder) -> None:
        builder.register("endpoint", "runtime.test")

    async def start(self, runtime_context) -> None:
        del runtime_context
        self._started = True

    async def stop(self) -> None:
        self._started = False

    def health(self):
        from core.modules import ModuleHealth, ModuleHealthCheck

        return ModuleHealth(
            status="healthy" if self._started else "stopped",
            ready=self._started,
            checks=(
                ModuleHealthCheck(
                    name="lifecycle",
                    healthy=self._started,
                    detail_code="",
                ),
            ),
        )


def test_runtime_module_diagnostics_returns_frozen_snapshot(
    client,
    monkeypatch,
):
    from core.modules import CompositionRoot, ModuleRuntimeContext

    _enable_admin(monkeypatch)
    root = CompositionRoot((_RuntimeModule(),))
    run_async(root.start(ModuleRuntimeContext(testing=True)))
    monkeypatch.setattr(
        client.app.state,
        "composition_root",
        root,
        raising=False,
    )
    try:
        response = client.get(
            "/api/v1/admin/runtime/modules",
            headers=HEADERS,
        )
    finally:
        run_async(root.stop())

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["available"] is True
    assert data["ready"] is True
    assert data["composition_state"] == "running"
    assert data["composition_generation"] == 1
    assert len(data["composition_sha256"]) == 64
    assert data["module_registry"]["namespace"] == (
        "application_module"
    )
    assert data["contribution_registry"]["namespace"] == (
        "module_contribution"
    )
    assert data["verification_registry"]["namespace"] == (
        "verification_suite"
    )
    assert data["verification_registry"]["generation"] == 1
    assert len(data["verification_registry"]["sha256"]) == 64
    suites = {
        suite["suite_id"]: suite
        for suite in data["verification_suites"]
    }
    assert suites["backend-full"]["always_required"] is True
    assert suites["backend-full"]["allow_skip"] is False
    assert suites["sandbox-real-docker"]["security_level"] == (
        "host_privileged"
    )
    assert suites["sandbox-real-docker"]["required_credentials"] == [
        "docker_host_access"
    ]
    assert data["modules"] == [
        {
            "module_id": "runtime.test",
            "version": "1.0.0",
            "owner": "tests",
            "domain": "runtime",
            "lifecycle": "active",
            "required_modules": [],
            "optional_modules": [],
            "provided_capabilities": ["runtime.test.port"],
            "contributions": [
                {
                    "kind": "endpoint",
                    "contribution_id": "runtime.test",
                }
            ],
            "startup_phase": 10,
            "shutdown_phase": 10,
            "health_checks": ["lifecycle"],
            "readiness_checks": ["lifecycle"],
            "feature_flag": "",
            "compatibility_aliases": [],
            "release_impacts": ["runtime"],
            "health": {
                "status": "healthy",
                "ready": True,
                "checks": [
                    {
                        "name": "lifecycle",
                        "healthy": True,
                        "detail_code": "",
                    }
                ],
            },
        }
    ]
    assert data["error_code"] == ""


def test_runtime_module_diagnostics_is_stable_when_root_is_missing(
    client,
    monkeypatch,
):
    _enable_admin(monkeypatch)
    monkeypatch.setattr(
        client.app.state,
        "composition_root",
        None,
        raising=False,
    )

    response = client.get(
        "/api/v1/admin/runtime/modules",
        headers=HEADERS,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    verification_registry = payload.pop("verification_registry")
    verification_suites = payload.pop("verification_suites")
    assert payload == {
        "available": False,
        "ready": False,
        "composition_state": "unavailable",
        "composition_generation": 0,
        "composition_sha256": "",
        "module_registry": {
            "namespace": "application_module",
            "generation": 0,
            "sha256": "",
        },
        "contribution_registry": {
            "namespace": "module_contribution",
            "generation": 0,
            "sha256": "",
        },
        "modules": [],
        "error_code": "composition_root_unavailable",
    }
    assert verification_registry["namespace"] == "verification_suite"
    assert verification_registry["generation"] == 1
    assert len(verification_registry["sha256"]) == 64
    assert any(
        suite["suite_id"] == "backend-full"
        and suite["always_required"] is True
        for suite in verification_suites
    )
