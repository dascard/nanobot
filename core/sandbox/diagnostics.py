"""Sandbox 原始配置、有效来源与会话门禁的安全诊断视图。"""

from __future__ import annotations

import os
from collections.abc import Mapping

from sqlalchemy.orm import Session

from core.config_registry import SETTING_DEFS
from core.database import SystemSetting
from core.sandbox.access_contracts import (
    LEASE_PROCESS_TOOL_NAMES,
    TOOL_REQUIRED_CAPABILITY,
    SandboxCapability,
)
from core.sandbox.access_policy import (
    SandboxAccessPolicy,
    canonical_sandbox_identity,
)
from core.sandbox.access_repository import SandboxAccessRepository
from core.sandbox.contracts import SandboxServiceError
from core.sandbox.execution_profiles import load_execution_profile_registry
from core.settings_service import coerce_setting_value, settings


_SANDBOX_BOOLEAN_SETTINGS: tuple[tuple[str, bool], ...] = (
    ("sandbox.infrastructure_enable_allowed", True),
    ("sandbox.session_execution_allowed", True),
    ("sandbox.developer_network_allowed", True),
    ("sandbox.enabled", False),
    ("sandbox.exec_enabled", False),
    ("sandbox.group_enabled", False),
)


def _parsed_value(value: object, key: str) -> object | None:
    definition = SETTING_DEFS.get(key)
    if definition is None:
        return None
    try:
        return coerce_setting_value(value, definition)
    except (TypeError, ValueError):
        return None


def sandbox_setting_diagnostic(
    db: Session,
    key: str,
    *,
    hard_ceiling: bool,
) -> dict[str, object]:
    """解释一个 Sandbox 设置，不返回凭据或宿主路径。"""

    definition = SETTING_DEFS[key]
    env_configured = bool(
        definition.env_name and definition.env_name in os.environ
    )
    env_value = (
        _parsed_value(os.environ.get(definition.env_name), key)
        if env_configured
        else None
    )
    row = db.get(SystemSetting, key)
    database_configured = row is not None and row.value is not None
    database_value = (
        _parsed_value(row.value, key)
        if database_configured
        else None
    )
    resolved = settings.get_resolved_for_session(
        db,
        key,
        definition.default,
    )
    return {
        "key": key,
        "environment": {
            "configured": env_configured,
            "value": env_value,
        },
        "database_override": {
            "configured": database_configured,
            "value": database_value,
            "source_allowed": (
                "database" in definition.source_precedence
            ),
        },
        "effective": {
            "value": bool(resolved.value),
            "source": resolved.source,
            "origin": resolved.origin,
        },
        "hard_ceiling": hard_ceiling,
    }


def sandbox_feature_diagnostics(db: Session) -> dict[str, object]:
    return {
        key: sandbox_setting_diagnostic(
            db,
            key,
            hard_ceiling=hard_ceiling,
        )
        for key, hard_ceiling in _SANDBOX_BOOLEAN_SETTINGS
    }


def _gate(
    gate_id: str,
    *,
    passed: bool,
    reason_code: str,
    hard_ceiling: bool = False,
    applicable: bool = True,
    details: Mapping[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "id": gate_id,
        "applicable": applicable,
        "passed": bool(passed),
        "reason_code": "" if passed else reason_code,
        "hard_ceiling": hard_ceiling,
    }
    if details:
        result["details"] = dict(details)
    return result


def sandbox_session_access_diagnostic(
    db: Session,
    *,
    tool_name: str,
    platform: object,
    chat_type: object,
    session_id: object,
) -> dict[str, object]:
    """返回与唯一 Policy 同输入、同最终结论的逐门安全诊断。"""

    required = TOOL_REQUIRED_CAPABILITY.get(str(tool_name or ""))
    if required is None:
        raise ValueError("tool_name 不是 Sandbox 工具")
    feature = sandbox_feature_diagnostics(db)

    def effective(key: str) -> bool:
        return bool(
            (
                feature[key].get("effective")
                if isinstance(feature[key], Mapping)
                else {}
            ).get("value")
        )

    gates: list[dict[str, object]] = [
        _gate(
            "infrastructure_ceiling",
            passed=effective("sandbox.infrastructure_enable_allowed"),
            reason_code="sandbox_infrastructure_disabled",
            hard_ceiling=True,
        ),
        _gate(
            "sandbox_enabled",
            passed=effective("sandbox.enabled"),
            reason_code="sandbox_business_disabled",
        ),
        _gate(
            "exec_enabled",
            passed=(
                required is not SandboxCapability.EXEC
                or effective("sandbox.exec_enabled")
            ),
            reason_code="sandbox_exec_disabled",
            applicable=required is SandboxCapability.EXEC,
        ),
    ]

    identity = None
    identity_error = ""
    try:
        identity = canonical_sandbox_identity(
            platform=platform,
            chat_type=chat_type,
            session_id=session_id,
        )
    except Exception as exc:
        identity_error = type(exc).__name__
    gates.append(_gate(
        "canonical_identity",
        passed=identity is not None,
        reason_code="sandbox_identity_invalid",
        details=(
            {"error_type": identity_error}
            if identity_error
            else {
                "chat_stream_id": identity.chat_stream_id,
                "chat_type": identity.chat_type,
                "platform": identity.platform,
            }
        ),
    ))

    is_group = identity is not None and identity.chat_type == "group"
    gates.append(_gate(
        "group_enabled",
        passed=not is_group or effective("sandbox.group_enabled"),
        reason_code="sandbox_group_disabled",
        applicable=is_group,
    ))

    repository = SandboxAccessRepository(db)
    grant = (
        repository.get_grant(identity.chat_stream_id)
        if identity is not None
        else None
    )
    try:
        granted = SandboxCapability.parse(
            grant.capability_level if grant is not None else "off"
        )
    except ValueError:
        granted = SandboxCapability.OFF
    grant_passed = bool(
        grant is not None
        and grant.status == "active"
        and granted >= required
    )
    gates.append(_gate(
        "session_grant",
        passed=grant_passed,
        reason_code="sandbox_grant_insufficient",
        details={
            "configured": grant is not None,
            "status": str(grant.status if grant is not None else "missing"),
            "granted_capability": granted.value_name,
            "required_capability": required.value_name,
        },
    ))

    profile_id = str(
        grant.execution_profile if grant is not None else ""
    ).strip()
    profile = None
    try:
        if profile_id:
            profile = load_execution_profile_registry().descriptor(
                profile_id
            )
    except SandboxServiceError:
        profile = None
    gates.append(_gate(
        "execution_profile",
        passed=profile is not None,
        reason_code="sandbox_profile_unavailable",
        details={
            "profile_id": profile_id,
            "execution_mode": (
                str(profile.execution_mode) if profile is not None else ""
            ),
            "network_policy_id": (
                str(profile.network_policy_id) if profile is not None else ""
            ),
        },
    ))

    lease_profile = bool(
        required is SandboxCapability.EXEC
        and profile is not None
        and profile.execution_mode == "lease"
    )
    gates.append(_gate(
        "session_execution_ceiling",
        passed=(
            not lease_profile
            or effective("sandbox.session_execution_allowed")
        ),
        reason_code="sandbox_session_execution_disabled",
        hard_ceiling=True,
        applicable=lease_profile,
    ))

    network_profile = bool(
        required is SandboxCapability.EXEC
        and profile is not None
        and profile.network_policy_id != "none"
    )
    gates.append(_gate(
        "developer_network_ceiling",
        passed=(
            not network_profile
            or effective("sandbox.developer_network_allowed")
        ),
        reason_code="sandbox_developer_network_disabled",
        hard_ceiling=True,
        applicable=network_profile,
    ))

    process_tool = tool_name in LEASE_PROCESS_TOOL_NAMES
    gates.append(_gate(
        "lease_process_support",
        passed=(
            not process_tool
            or (
                profile is not None
                and profile.execution_mode == "lease"
            )
        ),
        reason_code="sandbox_lease_process_unsupported",
        applicable=process_tool,
    ))
    stdin_tool = tool_name == "sandbox_write_stdin"
    gates.append(_gate(
        "stdin_support",
        passed=(
            not stdin_tool
            or (profile is not None and bool(profile.allow_stdin))
        ),
        reason_code="sandbox_stdin_unsupported",
        applicable=stdin_tool,
    ))

    workspace_id = str(grant.workspace_id or "") if grant is not None else ""
    workspace = repository.get_workspace(workspace_id) if workspace_id else None
    quota = (
        repository.get_quota_binding(workspace_id)
        if workspace_id
        else None
    )
    runtime_quota = (
        repository.get_runtime_quota_binding(workspace_id)
        if workspace_id and required is SandboxCapability.EXEC
        else None
    )
    maintenance = (
        repository.get_maintenance_state(workspace_id)
        if workspace_id
        else None
    )
    workspace_ready = bool(
        workspace is not None
        and workspace.status == "active"
        and quota is not None
        and quota.status == "applied"
        and int(quota.applied_quota_bytes or 0) > 0
        and int(quota.applied_quota_bytes or 0)
        == int(quota.desired_quota_bytes or 0)
        and int(workspace.quota_bytes or 0)
        == int(quota.applied_quota_bytes or 0)
        and (
            required is not SandboxCapability.EXEC
            or (
                runtime_quota is not None
                and profile is not None
                and runtime_quota.status == "applied"
                and int(runtime_quota.applied_quota_bytes or 0)
                == int(runtime_quota.desired_quota_bytes or 0)
                and int(runtime_quota.applied_quota_bytes or 0)
                == int(profile.runtime_quota_bytes)
                and int(runtime_quota.generation or 0)
                == int(quota.generation or 0)
            )
        )
        and (
            maintenance is None
            or (
                maintenance.status == "ready"
                and int(maintenance.generation or 0)
                == int(quota.generation or 0)
                and int(maintenance.applied_quota_generation or 0)
                == int(quota.generation or 0)
            )
        )
    )
    gates.append(_gate(
        "workspace_and_quota",
        passed=workspace_ready,
        reason_code="sandbox_workspace_not_ready",
        details={
            "workspace_configured": workspace is not None,
            "workspace_status": str(
                workspace.status if workspace is not None else "missing"
            ),
            "quota_status": str(
                quota.status if quota is not None else "missing"
            ),
            "runtime_quota_status": str(
                runtime_quota.status
                if runtime_quota is not None
                else (
                    "not_applicable"
                    if required is not SandboxCapability.EXEC
                    else "missing"
                )
            ),
            "maintenance_status": str(
                maintenance.status
                if maintenance is not None
                else "not_configured"
            ),
        },
    ))

    decision = SandboxAccessPolicy(db).evaluate(
        tool_name,
        platform=platform,
        chat_type=chat_type,
        session_id=session_id,
    )
    return {
        "tool_name": tool_name,
        "required_capability": required.value_name,
        "gates": gates,
        "final": {
            "allowed": decision.allowed,
            "reason_code": decision.code,
            "reason": decision.reason,
            "granted_capability": decision.granted_capability.value_name,
            "execution_profile": decision.execution_profile,
            "workspace_configured": bool(decision.workspace_id),
        },
    }


__all__ = [
    "sandbox_feature_diagnostics",
    "sandbox_session_access_diagnostic",
    "sandbox_setting_diagnostic",
]
