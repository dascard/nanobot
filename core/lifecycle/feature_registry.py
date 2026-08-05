"""Feature 生命周期的冻结事实源与启用决策。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from core.registry import RegistryBuilder, RegistrySnapshot


class FeatureLifecycleState(str, Enum):
    """产品能力的固定生命周期。"""

    EXPERIMENTAL = "experimental"
    HIDDEN = "hidden"
    PREVIEW = "preview"
    STABLE = "stable"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class FeatureScope(str, Enum):
    """Feature 可被评估的稳定作用域。"""

    GLOBAL = "global"
    PRIVATE_SESSION = "private_session"
    GROUP_SESSION = "group_session"
    ADMIN = "admin"


class FeatureRollbackBehavior(str, Enum):
    """关闭 Feature 时允许执行的回滚动作。"""

    DISABLE_PRESERVE_DATA = "disable_preserve_data"
    DISABLE_REVERT_CONFIG = "disable_revert_config"


class FeatureDecisionCode(str, Enum):
    """Feature 启用评估的稳定结果码。"""

    ENABLED = "enabled"
    NOT_REQUESTED = "not_requested"
    RETIRED = "retired"
    UNSUPPORTED_SCOPE = "unsupported_scope"
    MISSING_GATES = "missing_gates"


@dataclass(frozen=True, slots=True)
class FeatureLifecycleDescriptor:
    """一个 Feature 的版本化生命周期声明。"""

    feature_id: str
    state: FeatureLifecycleState
    owner_module: str
    default_enabled: bool
    supported_scopes: tuple[FeatureScope, ...]
    data_migrations: tuple[str, ...]
    rollback_behavior: FeatureRollbackBehavior
    enablement_gates: tuple[str, ...]
    removal_conditions: tuple[str, ...]

    def __post_init__(self) -> None:
        feature_id = str(self.feature_id or "").strip()
        owner_module = str(self.owner_module or "").strip()
        if not feature_id:
            raise ValueError("Feature lifecycle ID 不能为空")
        if not owner_module:
            raise ValueError(f"Feature {feature_id} 的 owner 不能为空")
        if not self.supported_scopes:
            raise ValueError(
                f"Feature {feature_id} 至少声明一个 supported scope"
            )
        if len(self.supported_scopes) != len(set(self.supported_scopes)):
            raise ValueError(f"Feature {feature_id} 的 scope 不能重复")
        if len(self.enablement_gates) != len(set(self.enablement_gates)):
            raise ValueError(
                f"Feature {feature_id} 的 enablement gate 不能重复"
            )
        if not self.removal_conditions:
            raise ValueError(
                f"Feature {feature_id} 的 removal conditions 不能为空"
            )
        for field_name, values in (
            ("data migration", self.data_migrations),
            ("enablement gate", self.enablement_gates),
            ("removal condition", self.removal_conditions),
        ):
            if any(
                not str(value or "").strip()
                or any(ord(char) < 32 for char in str(value))
                for value in values
            ):
                raise ValueError(
                    f"Feature {feature_id} 的 {field_name} 不合法"
                )
        object.__setattr__(self, "feature_id", feature_id)
        object.__setattr__(self, "owner_module", owner_module)

    @property
    def registry_namespace(self) -> str:
        return "feature_lifecycle"

    @property
    def registry_id(self) -> str:
        return self.feature_id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "feature_id": self.feature_id,
            "state": self.state.value,
            "owner_module": self.owner_module,
            "default_enabled": self.default_enabled,
            "supported_scopes": [
                scope.value for scope in self.supported_scopes
            ],
            "data_migrations": list(self.data_migrations),
            "rollback_behavior": self.rollback_behavior.value,
            "enablement_gates": list(self.enablement_gates),
            "removal_conditions": list(self.removal_conditions),
        }


class FeatureLifecycleRegistry:
    """构建后冻结的 Feature 生命周期目录。"""

    def __init__(
        self,
        descriptors: Iterable[FeatureLifecycleDescriptor],
    ) -> None:
        builder = RegistryBuilder[FeatureLifecycleDescriptor](
            "feature_lifecycle"
        )
        for descriptor in descriptors:
            builder.register(descriptor)
        self._snapshot = builder.freeze()

    @property
    def registry_snapshot(
        self,
    ) -> RegistrySnapshot[FeatureLifecycleDescriptor]:
        return self._snapshot

    def get(
        self,
        feature_id: str,
    ) -> FeatureLifecycleDescriptor | None:
        return self._snapshot.get(str(feature_id or "").strip())

    def require(
        self,
        feature_id: str,
    ) -> FeatureLifecycleDescriptor:
        normalized = str(feature_id or "").strip()
        descriptor = self.get(normalized)
        if descriptor is None:
            raise KeyError(f"未登记的 Feature lifecycle: {normalized}")
        return descriptor

    def descriptors(self) -> tuple[FeatureLifecycleDescriptor, ...]:
        return tuple(self._snapshot)


@dataclass(frozen=True, slots=True)
class FeatureEnablementDecision:
    """Feature 启用评估结果，不携带请求正文或身份值。"""

    feature_id: str
    enabled: bool
    code: FeatureDecisionCode
    missing_gates: tuple[str, ...] = ()


def _tool_feature_descriptors() -> tuple[FeatureLifecycleDescriptor, ...]:
    """从 Tool Descriptor 单一事实源投影工具 Feature。"""

    from core.tool_registry import (
        SANDBOX_TOOL_NAMES,
        list_tool_descriptors,
    )

    descriptors: list[FeatureLifecycleDescriptor] = [
        FeatureLifecycleDescriptor(
            feature_id="sandbox",
            state=FeatureLifecycleState.EXPERIMENTAL,
            owner_module="sandbox.control_plane",
            default_enabled=False,
            supported_scopes=(
                FeatureScope.GLOBAL,
                FeatureScope.PRIVATE_SESSION,
                FeatureScope.ADMIN,
            ),
            data_migrations=(
                "20260722_sandbox_control_plane_tables",
            ),
            rollback_behavior=(
                FeatureRollbackBehavior.DISABLE_PRESERVE_DATA
            ),
            enablement_gates=(
                "infrastructure_allowed",
                "sandboxd_ready",
                "apparmor_loaded",
                "fixed_image_digest",
                "workspace_quota_ready",
                "explicit_session_grant",
            ),
            removal_conditions=(
                "workspace_and_asset_data_preserved",
                "all_session_grants_reconciled",
            ),
        )
    ]
    for tool in list_tool_descriptors():
        if (
            tool.name in SANDBOX_TOOL_NAMES
            and tool.availability_policy != "force_disabled"
        ):
            continue
        if tool.availability_policy == "force_disabled":
            state = FeatureLifecycleState.RETIRED
        elif tool.framework_owned:
            state = FeatureLifecycleState.HIDDEN
        else:
            state = FeatureLifecycleState.STABLE
        descriptors.append(
            FeatureLifecycleDescriptor(
                feature_id=f"tool.{tool.name}",
                state=state,
                owner_module=tool.owner_module,
                default_enabled=bool(
                    tool.definition.private_default
                    or tool.definition.group_default
                )
                and state is not FeatureLifecycleState.RETIRED,
                supported_scopes=(
                    (FeatureScope.GLOBAL, FeatureScope.ADMIN)
                    if tool.framework_owned
                    else (
                        FeatureScope.GLOBAL,
                        FeatureScope.PRIVATE_SESSION,
                        FeatureScope.GROUP_SESSION,
                        FeatureScope.ADMIN,
                    )
                ),
                data_migrations=(),
                rollback_behavior=(
                    FeatureRollbackBehavior.DISABLE_REVERT_CONFIG
                ),
                enablement_gates=(
                    ()
                    if state
                    in {
                        FeatureLifecycleState.STABLE,
                        FeatureLifecycleState.HIDDEN,
                        FeatureLifecycleState.RETIRED,
                    }
                    else ("operator_approval",)
                ),
                removal_conditions=(
                    "compatibility_usage_gate_satisfied",
                    "replacement_or_retirement_documented",
                ),
            )
        )
    return tuple(descriptors)


def _semantic_feature_descriptors(
) -> tuple[FeatureLifecycleDescriptor, ...]:
    return (
        FeatureLifecycleDescriptor(
            feature_id="multi_agent_orchestration_v1",
            state=FeatureLifecycleState.EXPERIMENTAL,
            owner_module="core.agent_orchestration",
            default_enabled=False,
            supported_scopes=(
                FeatureScope.PRIVATE_SESSION,
                FeatureScope.GROUP_SESSION,
                FeatureScope.ADMIN,
            ),
            data_migrations=(),
            rollback_behavior=(
                FeatureRollbackBehavior.DISABLE_PRESERVE_DATA
            ),
            enablement_gates=(
                "explicit_operator_enablement",
                "approved_frozen_plan",
                "runtime_governance_ready",
                "checkpoint_store_ready",
                "event_ledger_ready",
            ),
            removal_conditions=(
                "active_orchestrations_settled",
                "checkpoint_and_receipt_facts_preserved",
            ),
        ),
        FeatureLifecycleDescriptor(
            feature_id="interoperability.acp_v1",
            state=FeatureLifecycleState.EXPERIMENTAL,
            owner_module="core.interoperability.acp",
            default_enabled=False,
            supported_scopes=(FeatureScope.ADMIN,),
            data_migrations=(),
            rollback_behavior=(
                FeatureRollbackBehavior.DISABLE_REVERT_CONFIG
            ),
            enablement_gates=(
                "protocol_compatibility_passed",
                "security_tests_passed",
                "trusted_principal_binding",
                "explicit_operator_enablement",
            ),
            removal_conditions=(
                "all_acp_sessions_closed",
                "external_transport_disabled",
            ),
        ),
        FeatureLifecycleDescriptor(
            feature_id="interoperability.a2a_v1_client",
            state=FeatureLifecycleState.EXPERIMENTAL,
            owner_module="core.interoperability.a2a",
            default_enabled=False,
            supported_scopes=(FeatureScope.ADMIN,),
            data_migrations=(),
            rollback_behavior=(
                FeatureRollbackBehavior.DISABLE_REVERT_CONFIG
            ),
            enablement_gates=(
                "protocol_compatibility_passed",
                "security_tests_passed",
                "endpoint_allowlist_ready",
                "credential_boundary_ready",
                "explicit_operator_enablement",
            ),
            removal_conditions=(
                "all_a2a_dispatches_settled",
                "remote_credentials_revoked",
            ),
        ),
        FeatureLifecycleDescriptor(
            feature_id="interoperability.headless",
            state=FeatureLifecycleState.EXPERIMENTAL,
            owner_module="core.interoperability.headless",
            default_enabled=False,
            supported_scopes=(FeatureScope.ADMIN,),
            data_migrations=(),
            rollback_behavior=(
                FeatureRollbackBehavior.DISABLE_PRESERVE_DATA
            ),
            enablement_gates=(
                "compatibility_tests_passed",
                "security_tests_passed",
                "event_ledger_ready",
                "explicit_operator_enablement",
            ),
            removal_conditions=(
                "active_headless_runs_settled",
                "runtime_facts_preserved",
            ),
        ),
        FeatureLifecycleDescriptor(
            feature_id="private_timing_v2",
            state=FeatureLifecycleState.PREVIEW,
            owner_module="core.private_timing",
            default_enabled=False,
            supported_scopes=(
                FeatureScope.PRIVATE_SESSION,
                FeatureScope.ADMIN,
            ),
            data_migrations=(),
            rollback_behavior=(
                FeatureRollbackBehavior.DISABLE_REVERT_CONFIG
            ),
            enablement_gates=(
                "offline_eval_passed",
                "task_slo_activation_ready",
                "token_observability_ready",
                "explicit_session_allowlist",
                "operator_approval",
            ),
            removal_conditions=(
                "replacement_or_retirement_documented",
                "all_session_overrides_reconciled",
            ),
        ),
        FeatureLifecycleDescriptor(
            feature_id="news_relevance_review",
            state=FeatureLifecycleState.PREVIEW,
            owner_module="core.news",
            default_enabled=False,
            supported_scopes=(
                FeatureScope.GLOBAL,
                FeatureScope.ADMIN,
            ),
            data_migrations=(),
            rollback_behavior=(
                FeatureRollbackBehavior.DISABLE_REVERT_CONFIG
            ),
            enablement_gates=(
                "offline_eval_passed",
                "task_slo_activation_ready",
                "token_observability_ready",
                "operator_approval",
            ),
            removal_conditions=(
                "replacement_or_retirement_documented",
                "source_and_ranking_policy_reconciled",
            ),
        ),
        FeatureLifecycleDescriptor(
            feature_id="group_learning",
            state=FeatureLifecycleState.EXPERIMENTAL,
            owner_module="core.group_learning",
            default_enabled=False,
            supported_scopes=(
                FeatureScope.GROUP_SESSION,
                FeatureScope.ADMIN,
            ),
            data_migrations=(
                "20260723_group_learning_stage7a_schema",
                "20260723_group_learning_stage7b_review_fields",
                "20260724_group_learning_stage7c_schedule_fencing",
                "20260724_group_learning_stage7d_legacy_read_only",
            ),
            rollback_behavior=(
                FeatureRollbackBehavior.DISABLE_PRESERVE_DATA
            ),
            enablement_gates=(
                "schema_ready",
                "explicit_session_schedule",
                "candidate_writer_exclusive",
                "model_review_observation_passed",
                "evidence_policy_ready",
                "operator_approval",
            ),
            removal_conditions=(
                "candidate_and_memory_data_preserved",
                "legacy_learning_migration_reconciled",
            ),
        ),
    )


FEATURE_LIFECYCLE_REGISTRY = FeatureLifecycleRegistry(
    (
        *_semantic_feature_descriptors(),
        *_tool_feature_descriptors(),
    )
)


def evaluate_feature_enablement(
    feature_id: str,
    *,
    requested: bool,
    scope: FeatureScope,
    satisfied_gates: frozenset[str] = frozenset(),
) -> FeatureEnablementDecision:
    """按冻结 Descriptor 评估启用；退休能力始终 fail closed。"""

    descriptor = FEATURE_LIFECYCLE_REGISTRY.require(feature_id)
    if descriptor.state is FeatureLifecycleState.RETIRED:
        return FeatureEnablementDecision(
            feature_id=descriptor.feature_id,
            enabled=False,
            code=FeatureDecisionCode.RETIRED,
        )
    if not requested:
        return FeatureEnablementDecision(
            feature_id=descriptor.feature_id,
            enabled=False,
            code=FeatureDecisionCode.NOT_REQUESTED,
        )
    if scope not in descriptor.supported_scopes:
        return FeatureEnablementDecision(
            feature_id=descriptor.feature_id,
            enabled=False,
            code=FeatureDecisionCode.UNSUPPORTED_SCOPE,
        )
    missing_gates = tuple(sorted(
        set(descriptor.enablement_gates) - set(satisfied_gates)
    ))
    if missing_gates:
        return FeatureEnablementDecision(
            feature_id=descriptor.feature_id,
            enabled=False,
            code=FeatureDecisionCode.MISSING_GATES,
            missing_gates=missing_gates,
        )
    return FeatureEnablementDecision(
        feature_id=descriptor.feature_id,
        enabled=True,
        code=FeatureDecisionCode.ENABLED,
    )


__all__ = [
    "FEATURE_LIFECYCLE_REGISTRY",
    "FeatureDecisionCode",
    "FeatureEnablementDecision",
    "FeatureLifecycleDescriptor",
    "FeatureLifecycleRegistry",
    "FeatureLifecycleState",
    "FeatureRollbackBehavior",
    "FeatureScope",
    "evaluate_feature_enablement",
]
