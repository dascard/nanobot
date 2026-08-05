"""多 Agent 计划与 checkpoint 的严格、可逆 JSON 编解码。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import json

from core.agent_orchestration.contracts import (
    ORCHESTRATION_SCHEMA_VERSION,
    AgentModelClass,
    AgentOrchestrationBudget,
    AgentOrchestrationCheckpoint,
    AgentOrchestrationPlan,
    AgentOrchestrationUsage,
    AgentRoleDefinition,
    AgentRoleKind,
    AgentTaskAccessRequirement,
    AgentTaskAuthority,
    AgentTaskCompletionCondition,
    AgentTaskDefinition,
    AgentTaskExecutionReceipt,
    AgentTaskInputBinding,
    AgentTaskOutput,
    AgentTaskOutputStatus,
    AgentTaskPurpose,
    AgentTaskRetryPolicy,
    AgentTaskRuntimeBudget,
    AgentTaskRuntimePolicy,
    AgentTaskState,
    JsonObjectContract,
    canonical_json_bytes,
)
from core.agent_runtime.contracts import (
    RuntimeActor,
    RuntimeActorType,
    RuntimeArtifactRef,
    RuntimeOwnerType,
    RuntimePrincipal,
    RuntimeRunIdentity,
    RuntimeUsage,
)
from core.agent_runtime.governance_contracts import RuntimeAccessKind


def _object(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} 必须是 JSON 对象")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} 的 JSON key 必须是字符串")
    return dict(value)


def _array(value: object, name: str) -> tuple[object, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} 必须是 JSON 数组")
    return tuple(value)


def _exact(
    value: object,
    name: str,
    fields: frozenset[str],
) -> Mapping[str, object]:
    payload = _object(value, name)
    keys = frozenset(payload)
    if keys != fields:
        missing = sorted(fields - keys)
        unknown = sorted(keys - fields)
        raise ValueError(
            f"{name} 字段不匹配：missing={missing}, unknown={unknown}"
        )
    return payload


def _contract(value: object, name: str) -> JsonObjectContract:
    payload = _exact(
        value,
        name,
        frozenset({"required_keys", "optional_keys", "max_bytes"}),
    )
    return JsonObjectContract(
        required_keys=tuple(_array(payload["required_keys"], f"{name}.required_keys")),
        optional_keys=tuple(_array(payload["optional_keys"], f"{name}.optional_keys")),
        max_bytes=payload["max_bytes"],
    )


def _runtime_usage(value: object, name: str) -> RuntimeUsage:
    payload = _exact(
        value,
        name,
        frozenset({
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "reasoning_tokens",
            "cost_microunits",
        }),
    )
    return RuntimeUsage(
        input_tokens=payload["input_tokens"],
        output_tokens=payload["output_tokens"],
        cached_input_tokens=payload["cached_input_tokens"],
        reasoning_tokens=payload["reasoning_tokens"],
        cost_microunits=payload["cost_microunits"],
    )


def _runtime_policy(
    value: object,
    name: str,
) -> AgentTaskRuntimePolicy | None:
    if value is None:
        return None
    payload = _exact(
        value,
        name,
        frozenset({
            "purpose",
            "model_class",
            "model_route_id",
            "model_route_sha256",
            "authority",
            "budget",
        }),
    )
    authority_payload = _exact(
        payload["authority"],
        f"{name}.authority",
        frozenset({"access", "skill_ids", "mcp_tool_names"}),
    )
    access = []
    for index, item in enumerate(_array(
        authority_payload["access"],
        f"{name}.authority.access",
    )):
        access_payload = _exact(
            item,
            f"{name}.authority.access[{index}]",
            frozenset({"kind", "resource", "operations"}),
        )
        access.append(AgentTaskAccessRequirement(
            kind=RuntimeAccessKind(str(access_payload["kind"])),
            resource=access_payload["resource"],
            operations=tuple(_array(
                access_payload["operations"],
                f"{name}.authority.access[{index}].operations",
            )),
        ))
    budget_payload = _exact(
        payload["budget"],
        f"{name}.budget",
        frozenset({
            "model_call_limit",
            "token_limit",
            "cost_limit_microunits",
            "tool_call_limit",
            "time_limit_ms",
        }),
    )
    return AgentTaskRuntimePolicy(
        purpose=AgentTaskPurpose(str(payload["purpose"])),
        model_class=AgentModelClass(str(payload["model_class"])),
        model_route_id=payload["model_route_id"],
        model_route_sha256=payload["model_route_sha256"],
        authority=AgentTaskAuthority(
            access=tuple(access),
            skill_ids=tuple(_array(
                authority_payload["skill_ids"],
                f"{name}.authority.skill_ids",
            )),
            mcp_tool_names=tuple(_array(
                authority_payload["mcp_tool_names"],
                f"{name}.authority.mcp_tool_names",
            )),
        ),
        budget=AgentTaskRuntimeBudget(**budget_payload),
    )


def agent_orchestration_plan_from_dict(
    value: object,
) -> AgentOrchestrationPlan:
    """从持久 JSON 重建计划；任何未知或缺失字段均失败关闭。"""

    payload = _exact(
        value,
        "orchestration plan",
        frozenset({
            "schema_version",
            "plan_id",
            "revision",
            "roles",
            "tasks",
            "root_input_contract",
            "aggregation_task_id",
            "budget",
            "communication_mode",
            "content_sha256",
        }),
    )
    if payload["schema_version"] != ORCHESTRATION_SCHEMA_VERSION:
        raise ValueError("orchestration plan schema_version 不受支持")
    if payload["communication_mode"] != "coordinator_mediated":
        raise ValueError("orchestration communication_mode 不受支持")
    roles = []
    for index, item in enumerate(_array(payload["roles"], "plan.roles")):
        role = _exact(
            item,
            f"plan.roles[{index}]",
            frozenset({"role_id", "kind", "description", "capabilities"}),
        )
        roles.append(AgentRoleDefinition(
            role_id=role["role_id"],
            kind=AgentRoleKind(str(role["kind"])),
            description=role["description"],
            capabilities=tuple(_array(
                role["capabilities"],
                f"plan.roles[{index}].capabilities",
            )),
        ))
    tasks = []
    task_fields = frozenset({
        "task_id",
        "role_id",
        "description",
        "dependencies",
        "input_contract",
        "input_bindings",
        "output_contract",
        "completion",
        "timeout_ms",
        "runtime_policy",
        "retry_policy",
    })
    for index, item in enumerate(_array(payload["tasks"], "plan.tasks")):
        task = _exact(item, f"plan.tasks[{index}]", task_fields)
        bindings = []
        for binding_index, raw_binding in enumerate(_array(
            task["input_bindings"],
            f"plan.tasks[{index}].input_bindings",
        )):
            binding = _exact(
                raw_binding,
                f"plan.tasks[{index}].input_bindings[{binding_index}]",
                frozenset({
                    "target_key",
                    "source_key",
                    "source_task_id",
                    "required",
                }),
            )
            bindings.append(AgentTaskInputBinding(**binding))
        completion = _exact(
            task["completion"],
            f"plan.tasks[{index}].completion",
            frozenset({
                "accepted_statuses",
                "required_data_keys",
                "minimum_artifacts",
            }),
        )
        retry = _exact(
            task["retry_policy"],
            f"plan.tasks[{index}].retry_policy",
            frozenset({
                "max_attempts",
                "retryable_error_codes",
                "backoff_ms",
                "idempotency_key",
            }),
        )
        tasks.append(AgentTaskDefinition(
            task_id=task["task_id"],
            role_id=task["role_id"],
            description=task["description"],
            dependencies=tuple(_array(
                task["dependencies"],
                f"plan.tasks[{index}].dependencies",
            )),
            input_contract=_contract(
                task["input_contract"],
                f"plan.tasks[{index}].input_contract",
            ),
            input_bindings=tuple(bindings),
            output_contract=_contract(
                task["output_contract"],
                f"plan.tasks[{index}].output_contract",
            ),
            completion=AgentTaskCompletionCondition(
                accepted_statuses=tuple(
                    AgentTaskOutputStatus(str(status))
                    for status in _array(
                        completion["accepted_statuses"],
                        f"plan.tasks[{index}].completion.accepted_statuses",
                    )
                ),
                required_data_keys=tuple(_array(
                    completion["required_data_keys"],
                    f"plan.tasks[{index}].completion.required_data_keys",
                )),
                minimum_artifacts=completion["minimum_artifacts"],
            ),
            timeout_ms=task["timeout_ms"],
            runtime_policy=_runtime_policy(
                task["runtime_policy"],
                f"plan.tasks[{index}].runtime_policy",
            ),
            retry_policy=AgentTaskRetryPolicy(
                max_attempts=retry["max_attempts"],
                retryable_error_codes=tuple(_array(
                    retry["retryable_error_codes"],
                    f"plan.tasks[{index}].retry_policy.retryable_error_codes",
                )),
                backoff_ms=tuple(_array(
                    retry["backoff_ms"],
                    f"plan.tasks[{index}].retry_policy.backoff_ms",
                )),
                idempotency_key=retry["idempotency_key"],
            ),
        ))
    budget = _exact(
        payload["budget"],
        "plan.budget",
        frozenset({
            "max_tasks",
            "max_concurrency",
            "max_model_calls",
            "max_tokens",
            "max_cost_microunits",
            "max_elapsed_ms",
            "max_output_bytes",
            "max_checkpoints",
            "max_spawn_depth",
            "max_tool_calls",
        }),
    )
    return AgentOrchestrationPlan(
        plan_id=payload["plan_id"],
        revision=payload["revision"],
        roles=tuple(roles),
        tasks=tuple(tasks),
        root_input_contract=_contract(
            payload["root_input_contract"],
            "plan.root_input_contract",
        ),
        aggregation_task_id=payload["aggregation_task_id"],
        budget=AgentOrchestrationBudget(**budget),
        content_sha256=payload["content_sha256"],
    )


def encode_agent_orchestration_plan(plan: AgentOrchestrationPlan) -> str:
    if not isinstance(plan, AgentOrchestrationPlan):
        raise TypeError("plan 必须是 AgentOrchestrationPlan")
    return canonical_json_bytes(plan.to_dict()).decode("utf-8")


def decode_agent_orchestration_plan(value: str) -> AgentOrchestrationPlan:
    try:
        payload = json.loads(str(value or ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("orchestration plan JSON 无效") from exc
    return agent_orchestration_plan_from_dict(payload)


def _task_output(value: object, name: str) -> AgentTaskOutput:
    payload = _exact(
        value,
        name,
        frozenset({
            "status",
            "summary",
            "next_actions",
            "artifacts",
            "data",
            "usage",
            "model_calls",
            "tool_calls",
        }),
    )
    artifacts = []
    for index, item in enumerate(_array(payload["artifacts"], f"{name}.artifacts")):
        artifact = _exact(
            item,
            f"{name}.artifacts[{index}]",
            frozenset({
                "artifact_id",
                "uri",
                "sha256",
                "media_type",
                "size_bytes",
                "version",
                "source_run_id",
            }),
        )
        artifacts.append(RuntimeArtifactRef(**artifact))
    return AgentTaskOutput(
        status=AgentTaskOutputStatus(str(payload["status"])),
        summary=payload["summary"],
        next_actions=tuple(_array(payload["next_actions"], f"{name}.next_actions")),
        artifacts=tuple(artifacts),
        data=_object(payload["data"], f"{name}.data"),
        usage=_runtime_usage(payload["usage"], f"{name}.usage"),
        model_calls=payload["model_calls"],
        tool_calls=payload["tool_calls"],
    )


def _receipt(value: object, name: str) -> AgentTaskExecutionReceipt:
    payload = _exact(
        value,
        name,
        frozenset({
            "task_id",
            "role_id",
            "state",
            "attempt_no",
            "dependency_ids",
            "output_sha256",
            "output_size_bytes",
            "error_code",
            "started_at",
            "finished_at",
            "duration_ms",
            "reservation_id",
            "receipt_sha256",
        }),
    )
    return AgentTaskExecutionReceipt(
        task_id=payload["task_id"],
        role_id=payload["role_id"],
        state=AgentTaskState(str(payload["state"])),
        attempt_no=payload["attempt_no"],
        dependency_ids=tuple(_array(
            payload["dependency_ids"],
            f"{name}.dependency_ids",
        )),
        output_sha256=payload["output_sha256"],
        output_size_bytes=payload["output_size_bytes"],
        error_code=payload["error_code"],
        started_at=datetime.fromisoformat(str(payload["started_at"])),
        finished_at=datetime.fromisoformat(str(payload["finished_at"])),
        duration_ms=payload["duration_ms"],
        reservation_id=payload["reservation_id"],
        receipt_sha256=payload["receipt_sha256"],
    )


def _identity(value: object) -> RuntimeRunIdentity:
    payload = _exact(
        value,
        "checkpoint.identity",
        frozenset({"run_id", "turn_id", "correlation_id", "actor", "owner"}),
    )
    actor = _exact(
        payload["actor"],
        "checkpoint.identity.actor",
        frozenset({"actor_type", "actor_id", "parent_actor_id"}),
    )
    owner = _exact(
        payload["owner"],
        "checkpoint.identity.owner",
        frozenset({"platform", "owner_type", "owner_id"}),
    )
    return RuntimeRunIdentity(
        run_id=payload["run_id"],
        turn_id=payload["turn_id"],
        correlation_id=payload["correlation_id"],
        actor=RuntimeActor(
            RuntimeActorType(str(actor["actor_type"])),
            actor["actor_id"],
            actor["parent_actor_id"],
        ),
        owner=RuntimePrincipal(
            owner["platform"],
            RuntimeOwnerType(str(owner["owner_type"])),
            owner["owner_id"],
        ),
    )


def agent_orchestration_checkpoint_from_dict(
    value: object,
) -> AgentOrchestrationCheckpoint:
    payload = _exact(
        value,
        "orchestration checkpoint",
        frozenset({
            "schema_version",
            "checkpoint_id",
            "orchestration_id",
            "run_id",
            "owner",
            "identity",
            "plan_id",
            "plan_revision",
            "plan_sha256",
            "freeze_id",
            "sequence",
            "parent_checkpoint_id",
            "barrier_id",
            "task_states",
            "outputs",
            "receipts",
            "cumulative_usage",
            "created_at",
            "state_sha256",
        }),
    )
    if payload["schema_version"] != ORCHESTRATION_SCHEMA_VERSION:
        raise ValueError("orchestration checkpoint schema_version 不受支持")
    identity = _identity(payload["identity"])
    if payload["run_id"] != identity.run_id or payload["owner"] != identity.owner.canonical_id:
        raise ValueError("checkpoint 投影身份与完整身份不一致")
    states_payload = _object(payload["task_states"], "checkpoint.task_states")
    outputs_payload = _object(payload["outputs"], "checkpoint.outputs")
    usage_payload = _exact(
        payload["cumulative_usage"],
        "checkpoint.cumulative_usage",
        frozenset({
            "usage",
            "model_calls",
            "tool_calls",
            "task_attempts",
            "output_bytes",
        }),
    )
    return AgentOrchestrationCheckpoint(
        checkpoint_id=payload["checkpoint_id"],
        orchestration_id=payload["orchestration_id"],
        identity=identity,
        plan_id=payload["plan_id"],
        plan_revision=payload["plan_revision"],
        plan_sha256=payload["plan_sha256"],
        freeze_id=payload["freeze_id"],
        sequence=payload["sequence"],
        parent_checkpoint_id=payload["parent_checkpoint_id"],
        barrier_id=payload["barrier_id"],
        task_states={
            task_id: AgentTaskState(str(state))
            for task_id, state in states_payload.items()
        },
        outputs={
            task_id: _task_output(output, f"checkpoint.outputs.{task_id}")
            for task_id, output in outputs_payload.items()
        },
        receipts=tuple(
            _receipt(item, f"checkpoint.receipts[{index}]")
            for index, item in enumerate(_array(
                payload["receipts"],
                "checkpoint.receipts",
            ))
        ),
        cumulative_usage=AgentOrchestrationUsage(
            usage=_runtime_usage(
                usage_payload["usage"],
                "checkpoint.cumulative_usage.usage",
            ),
            model_calls=usage_payload["model_calls"],
            tool_calls=usage_payload["tool_calls"],
            task_attempts=usage_payload["task_attempts"],
            output_bytes=usage_payload["output_bytes"],
        ),
        created_at=datetime.fromisoformat(str(payload["created_at"])),
        state_sha256=payload["state_sha256"],
    )


def encode_agent_orchestration_checkpoint(
    checkpoint: AgentOrchestrationCheckpoint,
) -> str:
    if not isinstance(checkpoint, AgentOrchestrationCheckpoint):
        raise TypeError("checkpoint 必须是 AgentOrchestrationCheckpoint")
    return canonical_json_bytes(checkpoint.to_dict()).decode("utf-8")


def decode_agent_orchestration_checkpoint(
    value: str,
) -> AgentOrchestrationCheckpoint:
    try:
        payload = json.loads(str(value or ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("orchestration checkpoint JSON 无效") from exc
    return agent_orchestration_checkpoint_from_dict(payload)


__all__ = [
    "agent_orchestration_checkpoint_from_dict",
    "agent_orchestration_plan_from_dict",
    "decode_agent_orchestration_checkpoint",
    "decode_agent_orchestration_plan",
    "encode_agent_orchestration_checkpoint",
    "encode_agent_orchestration_plan",
]
