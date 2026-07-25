"""现有分类器实现到核心 DecisionModelPort 的 Adapter。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from clients import classifier_client
from core.private_timing_contracts import (
    PRIVATE_DECISION_CONTRACT_VERSION,
)
from core.settings_service import settings
from core.task_runtime import (
    TaskInvocation,
    TaskResult,
    execute_task,
)


def _private_result_payload(result: TaskResult) -> dict[str, Any]:
    if result.ok:
        payload = dict(result.parsed_value)
        payload.update({
            "parse_quality": "schema_valid",
            "error_type": None,
            "contract_version": result.contract_version,
            "task_run_id": result.run_id,
        })
        return payload
    failure = result.failure
    failure_code = (
        failure.code.value if failure is not None else "provider_error"
    )
    return {
        "action": "reply_now",
        "effort": "short",
        "intent": "other",
        "response_mode": "agent",
        "confidence": 0.0,
        "parse_quality": "invalid",
        "error_type": failure_code,
        "conflicting_signals": [],
        "material_state": "unknown",
        "reason_code": "ambiguous_input",
        "contract_version": (
            result.contract_version
            or PRIVATE_DECISION_CONTRACT_VERSION
        ),
        "task_run_id": result.run_id,
    }


def execute_private_decision_task(
    message: str,
    has_files: bool = False,
) -> dict[str, Any]:
    text = str(message or "").strip()
    if not text and not has_files:
        return {
            "action": "no_reply",
            "effort": "short",
            "intent": "other",
            "response_mode": "none",
            "confidence": 1.0,
            "parse_quality": "schema_valid",
            "error_type": None,
            "conflicting_signals": [],
            "material_state": "none",
            "reason_code": "no_conversation_intent",
            "contract_version": PRIVATE_DECISION_CONTRACT_VERSION,
            "task_run_id": "",
        }
    result = execute_task(TaskInvocation(
        invocation_id="private_decision",
        route_key="private_decision",
        input_values={
            "message": text or "（无文本消息）",
            "has_files": "true" if has_files else "false",
        },
        contract_version=PRIVATE_DECISION_CONTRACT_VERSION,
        request_context={
            "template_confidence_threshold": settings.get_float(
                "private_timing.template_confidence_threshold",
                0.85,
            ),
        },
        timeout_budget_seconds=15.0,
        max_tokens=120,
    ))
    return _private_result_payload(result)


class ClassifierDecisionModelAdapter:
    @property
    def adapter_id(self) -> str:
        return "classifier_decision_model"

    def classify_private(
        self,
        message: str,
        has_files: bool = False,
    ) -> Mapping[str, Any]:
        return execute_private_decision_task(message, has_files)

    def judge_group_timing(self, context: str) -> Mapping[str, Any]:
        return classifier_client.get_timing_gate().judge(context)

    def judge_group_proactive(self, context: str) -> Mapping[str, Any]:
        return classifier_client.judge_proactive(context)


__all__ = [
    "ClassifierDecisionModelAdapter",
    "execute_private_decision_task",
]
