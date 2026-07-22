"""现有分类器实现到核心 DecisionModelPort 的 Adapter。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from clients import classifier_client


class ClassifierDecisionModelAdapter:
    @property
    def adapter_id(self) -> str:
        return "classifier_decision_model"

    def classify_private(
        self,
        message: str,
        has_files: bool = False,
    ) -> Mapping[str, Any]:
        return classifier_client.get_private_decision_classifier().classify(
            message,
            has_files,
        )

    def judge_group_timing(self, context: str) -> Mapping[str, Any]:
        return classifier_client.get_timing_gate().judge(context)

    def judge_group_proactive(self, context: str) -> Mapping[str, Any]:
        return classifier_client.judge_proactive(context)


__all__ = ["ClassifierDecisionModelAdapter"]
