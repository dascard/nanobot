"""具体 Agent／模型 Runtime 到自检诊断 Port 的 Adapter。"""

from __future__ import annotations

import importlib

from core.selfcheck.runtime_diagnostics import (
    EffectiveModelRouteDiagnostic,
    ModelRuntimeDiagnosticsSnapshot,
    ReplyRouteCandidateDiagnostic,
    RuntimeToolBindingDiagnostic,
    ToolRuntimeDiagnosticsSnapshot,
)


class RuntimeSelfcheckDiagnosticsAdapter:
    """构建真实绑定，但只向核心层返回脱敏、不可执行的快照。"""

    def inspect_tool_bindings(self) -> ToolRuntimeDiagnosticsSnapshot:
        from bootstrap.native_tool_runtime import (
            build_native_tool_execution_port,
        )
        from core.settings_service import settings
        from core.tool_registration import list_active_tool_registrations
        from core.tool_schema_preview import validate_registered_tool_schemas

        validate_registered_tool_schemas()
        expected = tuple(sorted(
            registration.execution_binding.port_id
            for registration in list_active_tool_registrations()
            if registration.execution_binding is not None
        ))
        native = build_native_tool_execution_port()
        runtimes = [RuntimeToolBindingDiagnostic(
            runtime_id="native",
            binding_ids=tuple(native.binding_ids),
        )]
        unavailable: list[str] = []
        kt_enabled = settings.get_bool("agent.runtime.kt_enabled", False)
        required = ("native", "kt") if kt_enabled else ("native",)

        try:
            from nanobot_kt.tool_registration_adapter import (
                build_kt_tool_configs,
                validate_kt_execution_bindings,
            )
        except ModuleNotFoundError:
            unavailable.append("kt")
        else:
            validation = validate_kt_execution_bindings()
            configs = build_kt_tool_configs()
            import_failures = 0
            for item in configs:
                try:
                    module = importlib.import_module(str(item.module))
                except (ImportError, AttributeError):
                    import_failures += 1
                    continue
                if getattr(module, str(item.class_name), None) is None:
                    import_failures += 1
            binding_ids = expected if (
                validation.active_tool_count == len(expected)
            ) else ()
            runtimes.append(RuntimeToolBindingDiagnostic(
                runtime_id="kt",
                binding_ids=binding_ids,
                import_failure_count=import_failures,
            ))

        return ToolRuntimeDiagnosticsSnapshot(
            expected_binding_ids=expected,
            required_runtime_ids=required,
            runtimes=tuple(runtimes),
            unavailable_runtime_ids=tuple(unavailable),
        )

    def inspect_model_routes(self) -> ModelRuntimeDiagnosticsSnapshot:
        from clients.classifier_client import resolve_model_route
        from config import NEW_API_BASE_URL, NEW_API_KEY
        from core.model_provider.route_registry import (
            list_model_route_descriptors,
        )
        from core.settings_service import settings
        from nanobot_kt.model_runtime import resolve_reply_route_plans

        routes: list[EffectiveModelRouteDiagnostic] = []
        for descriptor in list_model_route_descriptors():
            route = resolve_model_route(descriptor.route_key)
            driver_type = str(route.get("driver_type") or "").lower()
            routes.append(EffectiveModelRouteDiagnostic(
                route_key=str(route.get("route_key") or descriptor.route_key),
                provider_id=str(route.get("provider_id") or ""),
                driver_type=driver_type,
                model=str(route.get("model") or ""),
                provider_enabled=route.get("provider_enabled") is not False,
                route_completion_supported=(
                    route.get("route_completion_supported") is not False
                ),
                endpoint_configured=(
                    driver_type == "codex"
                    or bool(str(route.get("base_url") or "").strip())
                ),
                credential_configured=bool(
                    route.get("api_key_configured")
                ),
            ))

        runtime_kind = settings.get_str(
            "agent.runtime.default",
            "native",
        ).strip().lower()
        plans = resolve_reply_route_plans(
            default_base_url=str(NEW_API_BASE_URL or ""),
            default_api_key=str(NEW_API_KEY or ""),
            runtime_kind=runtime_kind,
        )
        reply_candidates = tuple(
            ReplyRouteCandidateDiagnostic(
                provider_id=str(plan.provider_id or ""),
                driver_type=str(plan.driver_type or "").lower(),
                model=str(plan.model or ""),
                endpoint_configured=(
                    str(plan.driver_type or "").lower() == "codex"
                    or bool(str(plan.base_url or "").strip())
                ),
            )
            for plan in plans
        )
        return ModelRuntimeDiagnosticsSnapshot(
            routes=tuple(routes),
            reply_candidates=reply_candidates,
        )


__all__ = ["RuntimeSelfcheckDiagnosticsAdapter"]
