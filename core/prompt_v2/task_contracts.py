from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

from core.prompt_v2.template_registry import resolve_template_key
from core.prompt_v2.variables import (
    is_empty_task_call_value,
    referenced_variable_names,
    validate_scoped_template,
)


TaskRenderMode = Literal[
    "user_prompt",
    "system_with_user_ref",
    "paired_messages",
    "code_fallback_only",
]
TaskRenderApi = Literal[
    "prompt",
    "messages",
    "paired_messages",
    "code_fallback_only",
]


class TaskContractError(ValueError):
    """任务模板或调用值不满足代码侧契约。"""


class TaskOutputContractError(ValueError):
    """模型输出无法通过任务输出契约。"""


class TaskCallValueError(TaskContractError):
    """任务调用方没有提供满足合同的动态值。"""


@dataclass(frozen=True)
class TaskContract:
    task_key: str
    required_variables: frozenset[str]
    required_call_values: frozenset[str]
    non_empty_call_values: frozenset[str]
    payload_variables: frozenset[str]
    render_mode: TaskRenderMode
    output_contract_id: str
    output_schema: dict[str, Any]
    template_failure_policy: str
    output_failure_policy: str

    def to_dict(self) -> dict:
        data = asdict(self)
        data["required_variables"] = sorted(self.required_variables)
        data["required_call_values"] = sorted(self.required_call_values)
        data["non_empty_call_values"] = sorted(self.non_empty_call_values)
        data["payload_variables"] = sorted(self.payload_variables)
        return data


@dataclass(frozen=True)
class TaskInvocationSpec:
    invocation_id: str
    template_keys: tuple[str, ...]
    render_api: TaskRenderApi
    output_parser_owner: str


def _contract(
    task_key: str,
    *,
    required: tuple[str, ...] = (),
    required_call: tuple[str, ...] | None = None,
    non_empty: tuple[str, ...] = (),
    payload: tuple[str, ...] = (),
    render_mode: TaskRenderMode,
    output_contract_id: str,
    output_schema: dict[str, Any] | None = None,
    template_failure_policy: str = "runtime_default_code_fallback",
    output_failure_policy: str = "fail_closed",
) -> TaskContract:
    required_call_values = frozenset(
        required if required_call is None else required_call
    )
    non_empty_call_values = frozenset(non_empty)
    if not non_empty_call_values <= required_call_values:
        missing = ", ".join(sorted(non_empty_call_values - required_call_values))
        raise ValueError(f"non_empty_call_values 必须同时是 required_call_values: {missing}")
    return TaskContract(
        task_key=resolve_template_key(task_key),
        required_variables=frozenset(required),
        required_call_values=required_call_values,
        non_empty_call_values=non_empty_call_values,
        payload_variables=frozenset(payload),
        render_mode=render_mode,
        output_contract_id=output_contract_id,
        output_schema=copy.deepcopy(output_schema or {}),
        template_failure_policy=template_failure_policy,
        output_failure_policy=output_failure_policy,
    )


_TASK_CONTRACTS = {
    contract.task_key: contract
    for contract in (
        _contract(
            "tasks/classifier_legacy",
            required=("system_prompt", "message"),
            non_empty=("system_prompt", "message"),
            payload=("message",),
            render_mode="system_with_user_ref",
            output_contract_id="legacy_reply_v1",
        ),
        _contract(
            "tasks/private_decision",
            render_mode="code_fallback_only",
            output_contract_id="private_decision_v1",
        ),
        _contract(
            "tasks/timing_gate",
            required=("pending_text",),
            non_empty=("pending_text",),
            payload=("pending_text",),
            render_mode="system_with_user_ref",
            output_contract_id="timing_gate_v1",
            output_failure_policy="retry_once_then_no_reply",
        ),
        _contract(
            "tasks/memory_extract",
            required=("conversation", "existing_memory"),
            non_empty=("conversation",),
            payload=("conversation", "existing_memory"),
            render_mode="user_prompt",
            output_contract_id="memory_candidates_v1",
            output_failure_policy="retry_once_keep_unprocessed",
        ),
        _contract(
            "tasks/reply_contract_retry",
            render_mode="code_fallback_only",
            output_contract_id="verified_final_action_v1",
        ),
        _contract(
            "tasks/outreach_extract",
            required_call=("message",),
            non_empty=("message",),
            payload=("message",),
            render_mode="system_with_user_ref",
            output_contract_id="outreach_threads_v1",
        ),
        _contract(
            "tasks/outreach_judge",
            required_call=("message",),
            non_empty=("message",),
            payload=("message",),
            render_mode="system_with_user_ref",
            output_contract_id="outreach_judge_v1",
            output_schema={
                "type": "object",
                "properties": {
                    "should_reach_out": {"type": "boolean"},
                    "reason": {"type": "string"},
                    "next_check_in_hours": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                    },
                    "next_check_at": {"type": "string"},
                    "next_intent": {"type": "string"},
                    "outreach_kind": {
                        "type": "string",
                        "enum": ["message", "research"],
                    },
                    "research_query": {"type": "string"},
                },
                "required": [
                    "should_reach_out",
                    "reason",
                    "next_intent",
                    "outreach_kind",
                    "research_query",
                ],
                "oneOf": [
                    {"required": ["next_check_in_hours"]},
                    {"required": ["next_check_at"]},
                ],
                "additionalProperties": False,
            },
        ),
        _contract(
            "tasks/outreach_generate",
            required_call=("message",),
            non_empty=("message",),
            payload=("message",),
            render_mode="system_with_user_ref",
            output_contract_id="outreach_message_v1",
        ),
        _contract(
            "tasks/proactive_research",
            required=("pending_text",),
            non_empty=("pending_text",),
            payload=("pending_text",),
            render_mode="user_prompt",
            output_contract_id="verified_research_final_action_v1",
            output_failure_policy="block",
        ),
        _contract(
            "tasks/memory_digest_system",
            render_mode="paired_messages",
            output_contract_id="memory_digest_v2",
            template_failure_policy="runtime_default_fail_closed",
        ),
        _contract(
            "tasks/memory_digest_user",
            required=(
                "date",
                "session_id",
                "source_id",
                "source_type",
                "source_range",
                "message_count",
                "digest_source",
            ),
            non_empty=(
                "session_id",
                "source_id",
                "source_type",
                "source_range",
                "digest_source",
            ),
            render_mode="paired_messages",
            output_contract_id="memory_digest_v2",
            template_failure_policy="runtime_default_fail_closed",
        ),
    )
}


_TASK_INVOCATION_SPECS: tuple[TaskInvocationSpec, ...] = (
    TaskInvocationSpec(
        "classifier_legacy",
        ("tasks/classifier_legacy",),
        "messages",
        "clients.classifier_client.Guardrail._validate_output",
    ),
    TaskInvocationSpec(
        "private_decision",
        ("tasks/private_decision",),
        "code_fallback_only",
        "clients.classifier_client.PrivateDecisionClassifier._parse",
    ),
    TaskInvocationSpec(
        "timing_gate",
        ("tasks/timing_gate",),
        "messages",
        "core.prompt_v2.task_contracts.parse_task_output",
    ),
    TaskInvocationSpec(
        "memory_extract",
        ("tasks/memory_extract",),
        "prompt",
        "core.prompt_v2.task_contracts.parse_task_output",
    ),
    TaskInvocationSpec(
        "reply_contract_retry",
        ("tasks/reply_contract_retry",),
        "code_fallback_only",
        "nanobot_kt.reply_contract.parse_structured_final_action",
    ),
    TaskInvocationSpec(
        "outreach_extract",
        ("tasks/outreach_extract",),
        "messages",
        "core.proactive_outreach.extract_recent_threads",
    ),
    TaskInvocationSpec(
        "outreach_judge",
        ("tasks/outreach_judge",),
        "messages",
        "core.proactive_outreach._parse_outreach_judge_contract",
    ),
    TaskInvocationSpec(
        "outreach_generate",
        ("tasks/outreach_generate",),
        "messages",
        "core.proactive_outreach.generate_outreach_message",
    ),
    TaskInvocationSpec(
        "proactive_research",
        ("tasks/proactive_research",),
        "prompt",
        "nanobot_kt.reply_contract.parse_structured_final_action",
    ),
    TaskInvocationSpec(
        "memory_digest",
        ("tasks/memory_digest_system", "tasks/memory_digest_user"),
        "paired_messages",
        "app.memory_digest.llm_builder.parse_llm_digest_response",
    ),
)


def get_task_contract(task_key: str) -> TaskContract | None:
    contract = _TASK_CONTRACTS.get(resolve_template_key(task_key))
    return copy.deepcopy(contract) if contract is not None else None


def list_task_contract_keys() -> list[str]:
    return sorted(_TASK_CONTRACTS)


def list_task_invocation_specs() -> list[TaskInvocationSpec]:
    return copy.deepcopy(list(_TASK_INVOCATION_SPECS))


def get_task_invocation_spec(invocation_id: str) -> TaskInvocationSpec | None:
    target = str(invocation_id or "").strip()
    for spec in _TASK_INVOCATION_SPECS:
        if spec.invocation_id == target:
            return copy.deepcopy(spec)
    return None


def get_task_invocation_for_template(task_key: str) -> TaskInvocationSpec | None:
    key = resolve_template_key(task_key)
    matches = [spec for spec in _TASK_INVOCATION_SPECS if key in spec.template_keys]
    if len(matches) > 1:
        raise TaskContractError(f"task {key} registered by multiple invocations")
    return copy.deepcopy(matches[0]) if matches else None


def validate_task_invocation_specs() -> None:
    expected_api: dict[TaskRenderMode, TaskRenderApi] = {
        "user_prompt": "prompt",
        "system_with_user_ref": "messages",
        "paired_messages": "paired_messages",
        "code_fallback_only": "code_fallback_only",
    }
    seen: set[str] = set()
    for spec in _TASK_INVOCATION_SPECS:
        if not spec.invocation_id or not spec.output_parser_owner.strip():
            raise TaskContractError("task invocation 缺少 ID 或 output parser owner")
        if not spec.template_keys:
            raise TaskContractError(f"task invocation {spec.invocation_id} 没有模板")
        for key in spec.template_keys:
            canonical = resolve_template_key(key)
            if canonical in seen:
                raise TaskContractError(f"task {canonical} invocation 重复登记")
            seen.add(canonical)
            contract = get_task_contract(canonical)
            if contract is None:
                raise TaskContractError(f"task {canonical} invocation 没有合同")
            if expected_api[contract.render_mode] != spec.render_api:
                raise TaskContractError(
                    f"task {canonical} render mode 与 invocation 不一致"
                )
    missing = sorted(set(_TASK_CONTRACTS) - seen)
    if missing:
        raise TaskContractError(
            "task contracts missing invocation: " + ", ".join(missing)
        )


def validate_task_template(task_key: str, body: str) -> TaskContract | None:
    key = resolve_template_key(task_key)
    contract = get_task_contract(key)
    validate_scoped_template(key, body)
    if contract is None or contract.render_mode == "code_fallback_only":
        return contract
    referenced = referenced_variable_names(body)
    missing = sorted(contract.required_variables - referenced)
    if missing:
        raise TaskContractError(
            f"task {key} missing required variables: {', '.join(missing)}"
        )
    return contract


def validate_task_call_values(task_key: str, values: dict) -> TaskContract | None:
    key = resolve_template_key(task_key)
    contract = get_task_contract(key)
    if contract is None:
        return None
    missing = sorted(
        name
        for name in contract.required_call_values
        if name not in values or values.get(name) is None
    )
    if missing:
        raise TaskCallValueError(
            f"task {key} missing required call values: {', '.join(missing)}"
        )
    empty = sorted(
        name
        for name in contract.non_empty_call_values
        if is_empty_task_call_value(values.get(name))
    )
    if empty:
        raise TaskCallValueError(
            f"task {key} empty required call values: {', '.join(empty)}"
        )
    return contract


def _parse_json_object(raw: str, *, contract_id: str) -> dict:
    text = str(raw or "").strip()
    if not text:
        raise TaskOutputContractError(f"{contract_id}: empty_output")
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise TaskOutputContractError(f"{contract_id}: invalid_json") from exc
    if not isinstance(value, dict):
        raise TaskOutputContractError(f"{contract_id}: root_must_be_object")
    return value


def _parse_memory_candidates(raw: str) -> dict:
    value = _parse_json_object(raw, contract_id="memory_candidates_v1")
    if "candidates" not in value:
        raise TaskOutputContractError("memory_candidates_v1: missing_candidates")
    candidates = value["candidates"]
    if not isinstance(candidates, list):
        raise TaskOutputContractError("memory_candidates_v1: candidates_must_be_list")
    if any(not isinstance(item, dict) for item in candidates):
        raise TaskOutputContractError("memory_candidates_v1: candidate_must_be_object")
    return {"candidates": candidates}


def _parse_timing_gate(raw: str) -> dict:
    value = _parse_json_object(raw, contract_id="timing_gate_v1")
    allowed = {"action", "delay_seconds", "reason"}
    if set(value) - allowed:
        raise TaskOutputContractError("timing_gate_v1: unsupported_fields")
    action = value.get("action")
    if action not in {"continue", "wait", "no_reply"}:
        raise TaskOutputContractError("timing_gate_v1: invalid_action")
    reason = value.get("reason", "")
    if not isinstance(reason, str):
        raise TaskOutputContractError("timing_gate_v1: reason_must_be_string")
    delay = value.get("delay_seconds")
    if action == "wait":
        if isinstance(delay, bool) or not isinstance(delay, int) or not 3 <= delay <= 15:
            raise TaskOutputContractError("timing_gate_v1: invalid_wait_delay")
    elif delay is not None:
        raise TaskOutputContractError("timing_gate_v1: delay_only_allowed_for_wait")
    return {
        "action": action,
        "delay_seconds": delay if action == "wait" else None,
        "reason": reason[:200],
    }


def parse_task_output(task_key: str, raw: str) -> dict:
    contract = get_task_contract(task_key)
    if contract is None:
        raise TaskOutputContractError("unregistered_task_contract")
    if contract.output_contract_id == "memory_candidates_v1":
        return _parse_memory_candidates(raw)
    if contract.output_contract_id == "timing_gate_v1":
        return _parse_timing_gate(raw)
    raise TaskOutputContractError(
        f"{contract.output_contract_id}: parser_not_implemented"
    )
