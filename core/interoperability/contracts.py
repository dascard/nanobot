"""实验互操作 Adapter 的公共门禁与安全错误。

协议 Adapter 只负责投影或调用既有 Port，不拥有第二份 Run、Event、Artifact
事实。所有外部启用都必须先通过 Feature 生命周期决策。
"""

from __future__ import annotations

from core.lifecycle import FeatureEnablementDecision


class InteroperabilityError(RuntimeError):
    """不携带远端正文、凭据或底层异常文本的稳定错误。"""

    def __init__(self, code: str, safe_message: str) -> None:
        self.code = str(code or "INTEROPERABILITY_ERROR").strip()
        self.safe_message = str(safe_message or "互操作调用失败").strip()
        super().__init__(self.safe_message)


class InteroperabilityDisabledError(InteroperabilityError):
    """实验能力没有获得完整启用门禁。"""


def require_interoperability_enabled(
    decision: FeatureEnablementDecision,
    *,
    feature_id: str,
) -> None:
    """拒绝 ID 错配、未启用或伪造类型的 Feature 决策。"""

    if not isinstance(decision, FeatureEnablementDecision):
        raise TypeError("enablement 必须是 FeatureEnablementDecision")
    if decision.feature_id != feature_id:
        raise InteroperabilityDisabledError(
            "FEATURE_MISMATCH",
            "互操作 Feature 决策与 Adapter 不匹配",
        )
    if not decision.enabled:
        raise InteroperabilityDisabledError(
            "FEATURE_DISABLED",
            "互操作 Feature 未通过全部启用门禁",
        )


__all__ = [
    "InteroperabilityDisabledError",
    "InteroperabilityError",
    "require_interoperability_enabled",
]
