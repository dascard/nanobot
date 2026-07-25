"""Feature 与 Compatibility 生命周期的稳定公开接口。"""

from core.lifecycle.compatibility_registry import (
    COMPATIBILITY_REGISTRY,
    CompatibilityDescriptor,
    CompatibilityKind,
    CompatibilityRegistry,
    CompatibilityRemovalGate,
    CompatibilityResolution,
    CompatibilityTombstoneBehavior,
    CompatibilityUsage,
    CompatibilityWarningPolicy,
    InMemoryCompatibilityUsageRecorder,
    get_compatibility_usage_snapshot,
    record_compatibility_usage,
    resolve_compatibility_alias,
)
from core.lifecycle.feature_registry import (
    FEATURE_LIFECYCLE_REGISTRY,
    FeatureDecisionCode,
    FeatureEnablementDecision,
    FeatureLifecycleDescriptor,
    FeatureLifecycleRegistry,
    FeatureLifecycleState,
    FeatureRollbackBehavior,
    FeatureScope,
    evaluate_feature_enablement,
)


__all__ = [
    "COMPATIBILITY_REGISTRY",
    "FEATURE_LIFECYCLE_REGISTRY",
    "CompatibilityDescriptor",
    "CompatibilityKind",
    "CompatibilityRegistry",
    "CompatibilityRemovalGate",
    "CompatibilityResolution",
    "CompatibilityTombstoneBehavior",
    "CompatibilityUsage",
    "CompatibilityWarningPolicy",
    "FeatureDecisionCode",
    "FeatureEnablementDecision",
    "FeatureLifecycleDescriptor",
    "FeatureLifecycleRegistry",
    "FeatureLifecycleState",
    "FeatureRollbackBehavior",
    "FeatureScope",
    "InMemoryCompatibilityUsageRecorder",
    "evaluate_feature_enablement",
    "get_compatibility_usage_snapshot",
    "record_compatibility_usage",
    "resolve_compatibility_alias",
]
