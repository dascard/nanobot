"""显式 Composition Root 的只读 Admin 诊断入口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.admin.common import verify_admin
from api.admin.runtime_module_models import (
    RuntimeModuleDiagnosticsResponse,
)
from api.endpoint_contracts import standard_error_responses
from core.modules import CompositionRoot, CompositionState
from core.release import VERIFICATION_SUITE_REGISTRY


router = APIRouter(tags=["admin-runtime-modules"])


def _empty_registry(namespace: str) -> dict[str, object]:
    return {
        "namespace": namespace,
        "generation": 0,
        "sha256": "",
    }


def _verification_registry_payload() -> dict[str, object]:
    return {
        "namespace": VERIFICATION_SUITE_REGISTRY.namespace,
        "generation": VERIFICATION_SUITE_REGISTRY.generation,
        "sha256": VERIFICATION_SUITE_REGISTRY.sha256,
    }


def _verification_suites_payload() -> list[dict[str, object]]:
    return [
        {
            "suite_id": suite.registry_id,
            "owner": suite.owner,
            "applicable_release_impacts": list(
                suite.applicable_release_impacts
            ),
            "command": list(suite.command),
            "working_directory": suite.working_directory,
            "preconditions": list(suite.preconditions),
            "timeout_seconds": suite.timeout_seconds,
            "allow_skip": suite.allow_skip,
            "required_credentials": list(
                suite.required_credentials
            ),
            "output_artifacts": list(suite.output_artifacts),
            "success_criteria": list(suite.success_criteria),
            "cleanup": list(suite.cleanup),
            "security_level": suite.security_level.value,
            "feature_lifecycle_states": list(
                suite.feature_lifecycle_states
            ),
            "feature_enablement_gates": list(
                suite.feature_enablement_gates
            ),
            "artifact_profiles": list(suite.artifact_profiles),
            "always_required": suite.always_required,
            "dependencies": list(suite.registry_dependencies),
        }
        for suite in VERIFICATION_SUITE_REGISTRY
    ]


def _unavailable_payload(
    *,
    state: str = "unavailable",
    error_code: str = "composition_root_unavailable",
) -> dict[str, object]:
    return {
        "available": False,
        "ready": False,
        "composition_state": state,
        "composition_generation": 0,
        "composition_sha256": "",
        "module_registry": _empty_registry("application_module"),
        "contribution_registry": _empty_registry(
            "module_contribution"
        ),
        "verification_registry": _verification_registry_payload(),
        "verification_suites": _verification_suites_payload(),
        "modules": [],
        "error_code": error_code,
    }


def _health_payload(health: object) -> dict[str, object]:
    checks = getattr(health, "checks", ())
    return {
        "status": str(getattr(health, "status", "unhealthy")),
        "ready": bool(getattr(health, "ready", False)),
        "checks": [
            {
                "name": str(check.name),
                "healthy": bool(check.healthy),
                "detail_code": str(check.detail_code or ""),
            }
            for check in checks
        ],
    }


def runtime_module_diagnostics_payload(
    root: object,
) -> dict[str, object]:
    """投影冻结快照；任何诊断失败都只返回稳定错误码。"""

    if not isinstance(root, CompositionRoot):
        return _unavailable_payload()

    state = root.state.value
    snapshot = root.snapshot
    if snapshot is None:
        return _unavailable_payload(
            state=state,
            error_code="composition_snapshot_unavailable",
        )

    health_by_module: dict[str, object] = {}
    health_error = ""
    if root.state is CompositionState.RUNNING:
        try:
            health_by_module = root.health()
        except Exception:
            health_error = "module_health_unavailable"

    modules = []
    for manifest in snapshot.modules:
        health = health_by_module.get(manifest.module_id)
        modules.append({
            "module_id": manifest.module_id,
            "version": manifest.version,
            "owner": manifest.owner,
            "domain": manifest.domain,
            "lifecycle": manifest.lifecycle,
            "required_modules": list(manifest.required_modules),
            "optional_modules": list(manifest.optional_modules),
            "provided_capabilities": list(
                manifest.provided_capabilities
            ),
            "contributions": [
                {
                    "kind": contribution.kind,
                    "contribution_id": (
                        contribution.contribution_id
                    ),
                }
                for contribution in manifest.contributions
            ],
            "startup_phase": manifest.startup_phase,
            "shutdown_phase": manifest.shutdown_phase,
            "health_checks": list(manifest.health_checks),
            "readiness_checks": list(manifest.readiness_checks),
            "feature_flag": manifest.feature_flag,
            "compatibility_aliases": list(
                manifest.compatibility_aliases
            ),
            "release_impacts": list(manifest.release_impacts),
            "health": (
                _health_payload(health)
                if health is not None
                else None
            ),
        })

    ready = (
        root.state is CompositionState.RUNNING
        and not health_error
        and len(health_by_module) == len(modules)
        and all(
            bool(getattr(health, "ready", False))
            for health in health_by_module.values()
        )
    )
    return {
        "available": True,
        "ready": ready,
        "composition_state": state,
        "composition_generation": snapshot.generation,
        "composition_sha256": snapshot.sha256,
        "module_registry": {
            "namespace": snapshot.modules.namespace,
            "generation": snapshot.modules.generation,
            "sha256": snapshot.modules.sha256,
        },
        "contribution_registry": {
            "namespace": snapshot.contributions.namespace,
            "generation": snapshot.contributions.generation,
            "sha256": snapshot.contributions.sha256,
        },
        "verification_registry": _verification_registry_payload(),
        "verification_suites": _verification_suites_payload(),
        "modules": modules,
        "error_code": health_error,
    }


@router.get(
    "/runtime/modules",
    operation_id="adminRuntimeModulesDiagnostics",
    response_model=RuntimeModuleDiagnosticsResponse,
    responses=standard_error_responses(401, 503),
)
def runtime_module_diagnostics(
    request: Request,
    _auth=Depends(verify_admin),
):
    root = getattr(request.app.state, "composition_root", None)
    return runtime_module_diagnostics_payload(root)


__all__ = [
    "router",
    "runtime_module_diagnostics",
    "runtime_module_diagnostics_payload",
]
