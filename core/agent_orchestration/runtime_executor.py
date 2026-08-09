"""把冻结任务编译为最小权限子 Runtime，并执行严格结构化回传。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import time
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from core.agent_orchestration.contracts import (
    AgentOrchestrationError,
    AgentOrchestrationPlan,
    AgentRoleKind,
    AgentTaskAuthority,
    AgentTaskExecutionContext,
    AgentTaskOutput,
    AgentTaskOutputStatus,
    AgentTaskRuntimePolicy,
    canonical_json_bytes,
)
from core.agent_runtime import (
    AgentRuntimePort,
    AgentTurnRequest,
    AgentTurnResult,
    RequestRuntimeContext,
    RuntimeAccessEnvelope,
    RuntimeAccessGrant,
    RuntimeAccessKind,
    RuntimeActor,
    RuntimeActorType,
    RuntimeBudgetEnvelope,
    RuntimeBudgetLimit,
    RuntimeBudgetScope,
    RuntimeCapability,
    RuntimeChatType,
    RuntimeGovernanceEnvelope,
    RuntimeLifecycleState,
    RuntimeMcpSnapshot,
    RuntimeMcpToolDescriptor,
    RuntimeMessage,
    RuntimeModelRoute,
    RuntimePlanKind,
    RuntimePlanRef,
    RuntimeRunEvent,
    RuntimeRunEventKind,
    RuntimeRunIdentity,
    RuntimeRunStatus,
    RuntimeSkillContent,
    RuntimeSkillDescriptor,
    RuntimeSkillSnapshot,
    RuntimeUsage,
    runtime_model_route_sha256,
)
from core.prompt_v2.task_contracts import (
    TaskOutputContractError,
    parse_task_output,
)
from core.prompt_v2.task_templates import render_task_messages
from core.tool_plan import ToolPlan, restrict_tool_plan, tool_plan_scope
from core.telemetry.contracts import TelemetryCorrelation


AGENT_SUBTASK_PROMPT_KEY = "tasks/agent_subtask"
MAX_CHILD_PROMPT_PAYLOAD_BYTES = 512 * 1024


def _null_scope() -> AbstractContextManager[None]:
    return nullcontext()


@dataclass(frozen=True, slots=True)
class AgentTaskRuntimeEnvironment:
    """由宿主冻结的父请求事实；不会原样传给子 Agent。"""

    parent_context: RequestRuntimeContext
    parent_tool_plan: ToolPlan | None
    model_routes: tuple[RuntimeModelRoute, ...]
    skill_snapshots: tuple[RuntimeSkillSnapshot, ...] = ()
    skill_contents: tuple[RuntimeSkillContent, ...] = ()
    mcp_snapshots: tuple[RuntimeMcpSnapshot, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.parent_context, RequestRuntimeContext):
            raise ValueError("parent_context 无效")
        self.parent_context.execution_identity()
        if self.parent_tool_plan is not None:
            if not isinstance(self.parent_tool_plan, ToolPlan):
                raise ValueError("parent_tool_plan 无效")
            reference = self.parent_context.plan(RuntimePlanKind.TOOL)
            if reference is None or reference.sha256 != self.parent_tool_plan.sha256:
                raise ValueError("parent ToolPlan 与 Runtime context 固定点不一致")
        raw_routes = tuple(self.model_routes)
        if not raw_routes or any(
            not isinstance(item, RuntimeModelRoute) for item in raw_routes
        ):
            raise ValueError("model_routes 无效")
        routes = tuple(sorted(raw_routes, key=lambda item: item.route_id))
        route_ids = [item.route_id for item in routes]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError("model_routes 不能包含重复 route_id")
        object.__setattr__(self, "model_routes", routes)

        raw_skills = tuple(self.skill_snapshots)
        if any(not isinstance(item, RuntimeSkillSnapshot) for item in raw_skills):
            raise ValueError("skill_snapshots 无效")
        skills = tuple(sorted(
            raw_skills,
            key=lambda item: item.provider_id,
        ))
        if len({item.provider_id for item in skills}) != len(skills):
            raise ValueError("skill_snapshots provider_id 重复")
        object.__setattr__(self, "skill_snapshots", skills)
        visible_skills = {
            descriptor.qualified_id: descriptor
            for snapshot in skills
            for descriptor in snapshot.skills
        }
        raw_contents = tuple(self.skill_contents)
        if any(not isinstance(item, RuntimeSkillContent) for item in raw_contents):
            raise ValueError("skill_contents 无效")
        contents = tuple(sorted(
            raw_contents,
            key=lambda item: item.descriptor.qualified_id,
        ))
        content_ids = [item.descriptor.qualified_id for item in contents]
        if len(content_ids) != len(set(content_ids)):
            raise ValueError("skill_contents 不能重复")
        if any(
            visible_skills.get(item.descriptor.qualified_id) != item.descriptor
            for item in contents
        ):
            raise ValueError("skill_contents 不属于父 Skill snapshot")
        object.__setattr__(self, "skill_contents", contents)

        raw_mcp = tuple(self.mcp_snapshots)
        if any(not isinstance(item, RuntimeMcpSnapshot) for item in raw_mcp):
            raise ValueError("mcp_snapshots 无效")
        mcp = tuple(sorted(
            raw_mcp,
            key=lambda item: item.provider_id,
        ))
        if len({item.provider_id for item in mcp}) != len(mcp):
            raise ValueError("mcp_snapshots provider_id 重复")
        wire_names = [
            descriptor.wire_name
            for snapshot in mcp
            for descriptor in snapshot.tools
        ]
        if len(wire_names) != len(set(wire_names)):
            raise ValueError("父 MCP snapshot 的 wire name 冲突")
        object.__setattr__(self, "mcp_snapshots", mcp)

    @property
    def route_by_id(self) -> Mapping[str, RuntimeModelRoute]:
        return MappingProxyType({item.route_id: item for item in self.model_routes})


@dataclass(frozen=True, slots=True)
class ChildAgentRuntimeBinding:
    """工厂可见的完整子 Runtime 固定点，不含父消息和父宽权限。"""

    task_id: str
    context: RequestRuntimeContext
    model_route: RuntimeModelRoute
    tool_plan: ToolPlan | None
    skill_snapshots: tuple[RuntimeSkillSnapshot, ...]
    skill_contents: tuple[RuntimeSkillContent, ...]
    mcp_snapshots: tuple[RuntimeMcpSnapshot, ...]
    initial_messages: tuple[RuntimeMessage, ...]
    request_content: str

    def __post_init__(self) -> None:
        if not str(self.task_id or "").strip():
            raise ValueError("child binding task_id 不能为空")
        if not isinstance(self.context, RequestRuntimeContext):
            raise ValueError("child binding context 无效")
        if not isinstance(self.model_route, RuntimeModelRoute):
            raise ValueError("child binding model_route 无效")
        if self.tool_plan is not None and not isinstance(self.tool_plan, ToolPlan):
            raise ValueError("child binding tool_plan 无效")
        if not self.initial_messages or any(
            not isinstance(item, RuntimeMessage) or item.role != "system"
            for item in self.initial_messages
        ):
            raise ValueError("child binding initial_messages 只能包含 system 消息")
        if not str(self.request_content or "").strip():
            raise ValueError("child binding request_content 不能为空")


@dataclass(frozen=True, slots=True)
class ChildAgentRuntimeLease:
    """一次性隔离 Runtime 与工厂提供的请求级扩展作用域。"""

    runtime: AgentRuntimePort
    scope_factory: Callable[[], AbstractContextManager[None]] = _null_scope

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, AgentRuntimePort):
            raise TypeError("child runtime 必须实现 AgentRuntimePort")
        if not callable(self.scope_factory):
            raise TypeError("child runtime scope_factory 必须可调用")

    def activate(self) -> AbstractContextManager[None]:
        scope = self.scope_factory()
        if not hasattr(scope, "__enter__") or not hasattr(scope, "__exit__"):
            raise TypeError("child runtime scope_factory 必须返回同步上下文管理器")
        return scope


@runtime_checkable
class ChildAgentRuntimeFactory(Protocol):
    async def create(
        self,
        binding: ChildAgentRuntimeBinding,
    ) -> ChildAgentRuntimeLease: ...


@dataclass(slots=True)
class _ObservedEvents:
    identity: RuntimeRunIdentity
    artifacts: dict[str, object]
    usage: list[RuntimeUsage]
    tool_call_ids: set[str]
    terminal_status: RuntimeRunStatus | None = None
    next_sequence: int = 1

    async def handle(self, event: RuntimeRunEvent) -> None:
        if event.identity != self.identity:
            raise AgentOrchestrationError(
                "child_event_identity_mismatch",
                "子 Runtime 事件身份与任务身份不一致",
            )
        if self.terminal_status is not None:
            raise AgentOrchestrationError(
                "child_event_after_terminal",
                "子 Runtime 在终态事件后继续发送事件",
            )
        if event.sequence != self.next_sequence:
            raise AgentOrchestrationError(
                "child_event_sequence_invalid",
                "子 Runtime 事件序号不连续",
            )
        self.next_sequence += 1
        if event.kind is RuntimeRunEventKind.ARTIFACT:
            assert event.artifact is not None
            if event.artifact.artifact_id in self.artifacts:
                raise AgentOrchestrationError(
                    "child_artifact_duplicate",
                    "子 Runtime 重复发布同一 Artifact",
                )
            self.artifacts[event.artifact.artifact_id] = event.artifact
        elif event.kind is RuntimeRunEventKind.USAGE:
            assert event.usage is not None
            self.usage.append(event.usage)
        elif event.kind is RuntimeRunEventKind.TOOL_ACTIVITY:
            assert event.tool_call is not None
            if event.tool_call.call_id in self.tool_call_ids:
                raise AgentOrchestrationError(
                    "child_tool_event_duplicate",
                    "子 Runtime 重复发送同一工具调用事件",
                )
            self.tool_call_ids.add(event.tool_call.call_id)
        elif event.kind is RuntimeRunEventKind.END:
            self.terminal_status = event.status


class AgentRuntimeTaskExecutor:
    """计划绑定的生产执行器：真实调用隔离 AgentRuntimePort。"""

    def __init__(
        self,
        *,
        plan: AgentOrchestrationPlan,
        environment: AgentTaskRuntimeEnvironment,
        runtime_factory: ChildAgentRuntimeFactory,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(plan, AgentOrchestrationPlan):
            raise TypeError("plan 必须是 AgentOrchestrationPlan")
        if any(task.runtime_policy is None for task in plan.tasks):
            raise AgentOrchestrationError(
                "task_runtime_policy_missing",
                "真实子 Runtime 计划必须为每个任务冻结执行策略",
            )
        if not isinstance(environment, AgentTaskRuntimeEnvironment):
            raise TypeError("environment 必须是 AgentTaskRuntimeEnvironment")
        if not isinstance(runtime_factory, ChildAgentRuntimeFactory):
            raise TypeError("runtime_factory 必须实现 ChildAgentRuntimeFactory")
        self._plan = plan
        self._environment = environment
        self._factory = runtime_factory
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._validate_parent_budget()
        self._validate_routes()

    def _validate_parent_budget(self) -> None:
        requested = self._plan.budget
        parent = self._environment.parent_context.governance.budgets.subagent
        limits = (
            ("max_concurrency", requested.max_concurrency, parent.concurrency_limit),
            ("max_model_calls", requested.max_model_calls, parent.model_call_limit),
            ("max_tokens", requested.max_tokens, parent.token_limit),
            (
                "max_cost_microunits",
                requested.max_cost_microunits,
                parent.cost_limit_microunits,
            ),
            ("max_elapsed_ms", requested.max_elapsed_ms, parent.time_limit_ms),
            (
                "max_steps",
                self._plan.maximum_attempts + requested.max_tool_calls,
                parent.step_limit,
            ),
        )
        exceeded = next(
            (
                name
                for name, value, limit in limits
                if limit <= 0 or value > limit
            ),
            "",
        )
        if exceeded:
            raise AgentOrchestrationError(
                "subagent_budget_denied",
                f"子 Runtime 计划 {exceeded} 超过父级显式预算",
                next_actions=("缩小计划预算或由宿主重新批准父级预算",),
                stop_condition="父级预算不足时不得创建子 Runtime",
            )

    def _validate_routes(self) -> None:
        route_by_id = self._environment.route_by_id
        expected_ids = {
            task.runtime_policy.model_route_id
            for task in self._plan.tasks
            if task.runtime_policy is not None
        }
        if set(route_by_id) != expected_ids:
            raise AgentOrchestrationError(
                "child_model_catalog_mismatch",
                "子 Agent 模型目录必须精确覆盖计划引用，不能夹带额外路由",
            )
        for task in self._plan.tasks:
            policy = task.runtime_policy
            assert policy is not None
            route = route_by_id[policy.model_route_id]
            if runtime_model_route_sha256(route) != policy.model_route_sha256:
                raise AgentOrchestrationError(
                    "child_model_route_drift",
                    "子 Agent 模型路由与批准计划摘要不一致",
                )
            parent_models = (
                self._environment.parent_context.governance.budgets.subagent
                .allowed_model_ids
            )
            if parent_models and route.model_id not in parent_models:
                raise AgentOrchestrationError(
                    "child_model_scope_denied",
                    "子 Agent 模型不在父 Run 的显式模型范围内",
                )
            role = self._plan.role_by_id[task.role_id]
            if role.kind is AgentRoleKind.REVIEWER:
                dependency_models = {
                    route_by_id[
                        self._plan.task_by_id[dependency]
                        .runtime_policy.model_route_id
                    ].model_id
                    for dependency in task.dependencies
                }
                if route.model_id in dependency_models:
                    raise AgentOrchestrationError(
                        "review_model_not_independent",
                        "验证或裁判模型必须与其直接证据生产模型不同",
                    )

    async def execute(
        self,
        context: AgentTaskExecutionContext,
    ) -> AgentTaskOutput:
        self._validate_execution_context(context)
        binding = self._compile_binding(context)
        parent = self._environment.parent_context
        parent_identity = parent.execution_identity()
        event_context = TelemetryCorrelation(
            request_id=parent.request_id,
            session_id=parent.session_id,
            turn_id=parent.turn_id,
            trace_id=parent.trace_id or parent_identity.correlation_id,
            run_id=parent_identity.run_id,
            task_id=context.task.task_id,
            task_run_id=binding.context.run_id,
        )
        event_attributes = {
            "child_run_id": binding.context.run_id,
            "task_id": context.task.task_id,
            "role_id": context.role.role_id,
            "attempt_no": context.attempt_no,
            "model": binding.model_route.model_id,
        }
        from core.runtime.event_bus import emit_runtime_event

        event_started = time.perf_counter()
        emit_runtime_event(
            "subagent.execute",
            "started",
            context=event_context,
            attributes=event_attributes,
        )
        try:
            output = await self._execute_bound(context, binding)
        except BaseException as exc:
            failure_code = (
                exc.code
                if isinstance(exc, AgentOrchestrationError)
                else "subagent_execution_interrupted"
                if isinstance(exc, asyncio.CancelledError)
                else "subagent_execution_failed"
            )
            emit_runtime_event(
                "subagent.execute",
                "failed",
                context=event_context,
                attributes={
                    **event_attributes,
                    "status": "cancelled"
                    if isinstance(exc, asyncio.CancelledError)
                    else "error",
                    "latency_ms": (time.perf_counter() - event_started) * 1000,
                    "failure_code": failure_code,
                    "error_type": type(exc).__name__,
                    "retryable": context.task.retry_policy.permits(
                        failure_code,
                        context.attempt_no,
                    ),
                },
            )
            raise
        failed = output.status is AgentTaskOutputStatus.ERROR
        failure_code = str(output.data.get("error_code") or "") if failed else ""
        emit_runtime_event(
            "subagent.execute",
            "failed" if failed else "succeeded",
            context=event_context,
            attributes={
                **event_attributes,
                "status": output.status.value,
                "latency_ms": (time.perf_counter() - event_started) * 1000,
                "model_call_count": output.model_calls,
                "tool_call_count": output.tool_calls,
                "artifact_count": len(output.artifacts),
                "input_tokens": output.usage.input_tokens,
                "output_tokens": output.usage.output_tokens,
                "cached_input_tokens": output.usage.cached_input_tokens,
                "reasoning_tokens": output.usage.reasoning_tokens,
                "cost_microunits": output.usage.cost_microunits,
                "failure_code": failure_code,
                "error_type": "AgentTaskOutputError" if failed else "",
                "retryable": (
                    context.task.retry_policy.permits(
                        failure_code,
                        context.attempt_no,
                    )
                    if failed
                    else False
                ),
            },
        )
        return output

    async def _execute_bound(
        self,
        context: AgentTaskExecutionContext,
        binding: ChildAgentRuntimeBinding,
    ) -> AgentTaskOutput:
        lease = await self._factory.create(binding)
        if not isinstance(lease, ChildAgentRuntimeLease):
            raise AgentOrchestrationError(
                "child_runtime_factory_invalid",
                "子 Runtime 工厂没有返回稳定 Lease",
            )
        runtime = lease.runtime
        if runtime.state is not RuntimeLifecycleState.NEW:
            raise AgentOrchestrationError(
                "child_runtime_not_isolated",
                "子 Runtime 必须是尚未使用的独立实例",
            )
        required_capabilities = frozenset({
            RuntimeCapability.RUN_EVENT,
            RuntimeCapability.CONVERSATION,
            RuntimeCapability.MODEL_ROUTE,
            RuntimeCapability.TOOL_POLICY,
            RuntimeCapability.INTERRUPT,
        })
        if runtime.runtime_capabilities.missing(required_capabilities):
            raise AgentOrchestrationError(
                "child_runtime_capability_missing",
                "子 Runtime 缺少事件、会话、模型、工具策略或中断能力",
            )

        observed = _ObservedEvents(
            identity=binding.context.execution_identity(),
            artifacts={},
            usage=[],
            tool_call_ids=set(),
        )
        result: AgentTurnResult | None = None
        runtime_error: BaseException | None = None
        started = False
        cancelled = False
        with tool_plan_scope(binding.tool_plan), lease.activate():
            try:
                await runtime.start()
                started = True
                runtime.set_model_route(binding.model_route)
                tool_status = runtime.install_tool_policy()
                if not tool_status.ready:
                    raise AgentOrchestrationError(
                        "child_tool_policy_unavailable",
                        "子 Runtime 没有安装 ToolPlan 执行守卫",
                    )
                runtime.replace_conversation(binding.initial_messages)
                result = await runtime.run_event(
                    AgentTurnRequest(
                        context=binding.context,
                        content=binding.request_content,
                        stream=False,
                    ),
                    observed.handle,
                )
            except asyncio.CancelledError:
                cancelled = True
                if started:
                    runtime.interrupt(reason="orchestration_task_cancelled")
            except Exception as exc:
                runtime_error = exc
            finally:
                if started and runtime.state is not RuntimeLifecycleState.STOPPED:
                    try:
                        await runtime.stop()
                    except asyncio.CancelledError:
                        cancelled = True
                    except Exception as exc:
                        raise AgentOrchestrationError(
                            "child_runtime_stop_failed",
                            "子 Runtime 无法确认停止",
                            stop_condition="停止未确认前不得复用子工作区或重放副作用",
                        ) from exc
                    if runtime.state is not RuntimeLifecycleState.STOPPED:
                        raise AgentOrchestrationError(
                            "child_runtime_stop_unconfirmed",
                            "子 Runtime stop 返回后仍未进入 STOPPED 状态",
                            stop_condition="停止未确认前不得复用子工作区或重放副作用",
                        )
        if cancelled:
            raise asyncio.CancelledError
        if runtime_error is not None:
            if not started:
                if isinstance(runtime_error, AgentOrchestrationError):
                    raise runtime_error
                raise AgentOrchestrationError(
                    "child_runtime_start_failed",
                    "子 Runtime 在启动或策略安装前失败",
                ) from runtime_error
            return self._failure_output(
                context,
                runtime_error,
                result=result,
                observed=observed,
            )
        if result is None:
            return self._failure_output(
                context,
                AgentOrchestrationError(
                    "child_runtime_result_missing",
                    "子 Runtime 未返回任务结果",
                ),
                observed=observed,
            )
        if observed.terminal_status is not RuntimeRunStatus.SUCCEEDED:
            return self._failure_output(
                context,
                AgentOrchestrationError(
                    "child_runtime_terminal_invalid",
                    "子 Runtime 没有形成成功终态事件",
                ),
                result=result,
                observed=observed,
            )
        try:
            return self._parse_output(context, result, observed)
        except AgentOrchestrationError as exc:
            return self._failure_output(
                context,
                exc,
                result=result,
                observed=observed,
            )
        except Exception:
            return self._failure_output(
                context,
                AgentOrchestrationError(
                    "child_output_contract_invalid",
                    "子 Runtime 输出无法构造稳定任务合同",
                ),
                result=result,
                observed=observed,
            )

    def _validate_execution_context(
        self,
        context: AgentTaskExecutionContext,
    ) -> None:
        if not isinstance(context, AgentTaskExecutionContext):
            raise TypeError("context 必须是 AgentTaskExecutionContext")
        expected = self._plan.task_by_id.get(context.task.task_id)
        if expected is None or expected != context.task:
            raise AgentOrchestrationError(
                "child_task_plan_mismatch",
                "子任务不属于执行器绑定的冻结计划",
            )
        if context.identity != self._environment.parent_context.execution_identity():
            raise AgentOrchestrationError(
                "child_parent_identity_mismatch",
                "子任务协调者身份与父 Runtime context 不一致",
            )

    def _compile_binding(
        self,
        context: AgentTaskExecutionContext,
    ) -> ChildAgentRuntimeBinding:
        policy = context.task.runtime_policy
        assert policy is not None
        route = self._environment.route_by_id[policy.model_route_id]
        grants = self._derive_access_grants(policy)
        tool_plan = self._derive_tool_plan(policy)
        skill_snapshots, skill_contents = self._select_skills(policy)
        mcp_snapshots = self._select_mcp(policy)
        prompt_messages = self._render_prompt(
            context,
            skill_contents=skill_contents,
            mcp_snapshots=mcp_snapshots,
        )
        prompt_digest = hashlib.sha256(
            str(prompt_messages[0].content).encode("utf-8")
        ).hexdigest()
        child_context = self._child_context(
            context,
            policy=policy,
            route=route,
            grants=grants,
            tool_plan=tool_plan,
            skill_snapshots=skill_snapshots,
            mcp_snapshots=mcp_snapshots,
            prompt_sha256=prompt_digest,
        )
        return ChildAgentRuntimeBinding(
            task_id=context.task.task_id,
            context=child_context,
            model_route=route,
            tool_plan=tool_plan,
            skill_snapshots=skill_snapshots,
            skill_contents=skill_contents,
            mcp_snapshots=mcp_snapshots,
            initial_messages=(prompt_messages[0],),
            request_content=str(prompt_messages[1].content),
        )

    def _derive_access_grants(
        self,
        policy: AgentTaskRuntimePolicy,
    ) -> tuple[RuntimeAccessGrant, ...]:
        parent = self._environment.parent_context.governance.access
        grants: list[RuntimeAccessGrant] = []
        for requirement in policy.authority.access:
            if (
                requirement.kind is RuntimeAccessKind.TOOL
                and (
                    not requirement.resource.startswith("tool:")
                    or requirement.operations != ("execute",)
                )
            ):
                raise AgentOrchestrationError(
                    "child_tool_access_invalid",
                    "子 Agent 工具权限必须使用 tool:<name>/execute 精确声明",
                )
            for operation in requirement.operations:
                if parent.find(
                    requirement.kind,
                    requirement.resource,
                    operation,
                ) is None:
                    raise AgentOrchestrationError(
                        "child_access_scope_denied",
                        "子 Agent 请求的资源或操作不在父权限信封内",
                    )
            grants.append(RuntimeAccessGrant(
                kind=requirement.kind,
                resource=requirement.resource,
                operations=requirement.operations,
                authorization="approved_subagent_plan",
            ))
        return tuple(grants)

    def _derive_tool_plan(
        self,
        policy: AgentTaskRuntimePolicy,
    ) -> ToolPlan | None:
        parent = self._environment.parent_tool_plan
        names = policy.authority.tool_names
        if parent is None:
            if names:
                raise AgentOrchestrationError(
                    "child_tool_plan_missing",
                    "子 Agent 声明了工具，但父请求没有冻结 ToolPlan",
                )
            return None
        for name in names:
            try:
                parent.ensure_executable(name)
            except Exception as exc:
                raise AgentOrchestrationError(
                    "child_tool_scope_denied",
                    "子 Agent 工具不在父 ToolPlan 可执行集合内",
                ) from exc
        return restrict_tool_plan(
            parent,
            names,
            disabled_reason="当前子 Agent 任务未获授权",
        )

    def _select_skills(
        self,
        policy: AgentTaskRuntimePolicy,
    ) -> tuple[tuple[RuntimeSkillSnapshot, ...], tuple[RuntimeSkillContent, ...]]:
        descriptor_sources: dict[str, tuple[RuntimeSkillSnapshot, RuntimeSkillDescriptor]] = {}
        for snapshot in self._environment.skill_snapshots:
            for descriptor in snapshot.skills:
                descriptor_sources[descriptor.qualified_id] = (snapshot, descriptor)
        content_by_id = {
            item.descriptor.qualified_id: item
            for item in self._environment.skill_contents
        }
        selected_by_provider: dict[str, list[RuntimeSkillDescriptor]] = defaultdict(list)
        selected_contents: list[RuntimeSkillContent] = []
        for qualified_id in policy.authority.skill_ids:
            source = descriptor_sources.get(qualified_id)
            content = content_by_id.get(qualified_id)
            if source is None or content is None:
                raise AgentOrchestrationError(
                    "child_skill_scope_denied",
                    "子 Agent Skill 不在父快照内或缺少已固定正文",
                )
            snapshot, descriptor = source
            selected_by_provider[snapshot.provider_id].append(descriptor)
            selected_contents.append(content)

        selected_descriptors = {
            item.descriptor.qualified_id: item.descriptor
            for item in selected_contents
        }
        for descriptor in selected_descriptors.values():
            provider_items = tuple(
                item for item in selected_descriptors.values()
                if item.provider_id == descriptor.provider_id
            )
            available_dependencies = {
                f"{item.skill_id}@{item.version}" for item in provider_items
            }
            if not set(descriptor.dependencies) <= available_dependencies:
                raise AgentOrchestrationError(
                    "child_skill_dependency_missing",
                    "子 Agent Skill 的固定依赖没有一并授权",
                )
            if not set(descriptor.allowed_tools) <= policy.authority.tool_names:
                raise AgentOrchestrationError(
                    "child_skill_tool_missing",
                    "子 Agent 没有授权 Skill 声明的全部工具",
                )
            self._validate_skill_permissions(descriptor, policy.authority)

        snapshots: list[RuntimeSkillSnapshot] = []
        for source in self._environment.skill_snapshots:
            descriptors = selected_by_provider.get(source.provider_id)
            if descriptors:
                snapshots.append(RuntimeSkillSnapshot(
                    provider_id=source.provider_id,
                    revision=f"{source.revision}/child",
                    skills=tuple(descriptors),
                ))
        return tuple(snapshots), tuple(sorted(
            selected_contents,
            key=lambda item: item.descriptor.qualified_id,
        ))

    @staticmethod
    def _validate_skill_permissions(
        descriptor: RuntimeSkillDescriptor,
        authority: AgentTaskAuthority,
    ) -> None:
        access = authority.access
        tool_names = authority.tool_names

        def has_access(
            kind: RuntimeAccessKind,
            operation: str,
            *,
            resource: str = "",
        ) -> bool:
            return any(
                item.kind is kind
                and (not resource or item.resource == resource)
                and operation in item.operations
                for item in access
            )

        for permission in descriptor.required_permissions:
            prefix, separator, value = permission.partition(":")
            allowed = False
            if separator and prefix == "tool":
                allowed = value in tool_names
            elif permission == "network:none":
                allowed = True
            elif permission == "network:search":
                allowed = (
                    "web_search" in tool_names
                    and has_access(
                        RuntimeAccessKind.NETWORK,
                        "request",
                        resource="controlled-provider:web_search",
                    )
                )
            elif permission == "workspace:read":
                allowed = (
                    "workspace_read" in tool_names
                    and has_access(RuntimeAccessKind.FILE, "read")
                )
            elif permission == "workspace:write":
                allowed = (
                    bool({"workspace_write", "workspace_edit"} & tool_names)
                    and has_access(RuntimeAccessKind.FILE, "write")
                )
            elif permission == "sandbox:execute":
                allowed = "sandbox_exec" in tool_names
            elif permission == "skill:resource-read":
                allowed = has_access(RuntimeAccessKind.SKILL, "load")
            elif separator:
                kind_by_prefix = {
                    "file": RuntimeAccessKind.FILE,
                    "network": RuntimeAccessKind.NETWORK,
                    "skill": RuntimeAccessKind.SKILL,
                    "mcp": RuntimeAccessKind.MCP,
                    "memory": RuntimeAccessKind.MEMORY,
                }
                kind = kind_by_prefix.get(prefix)
                allowed = kind is not None and has_access(kind, value)
            if not allowed:
                raise AgentOrchestrationError(
                    "child_skill_permission_missing",
                    "子 Agent 没有满足 Skill 声明的最小权限",
                )

    def _select_mcp(
        self,
        policy: AgentTaskRuntimePolicy,
    ) -> tuple[RuntimeMcpSnapshot, ...]:
        sources: dict[
            str,
            tuple[RuntimeMcpSnapshot, RuntimeMcpToolDescriptor],
        ] = {}
        for snapshot in self._environment.mcp_snapshots:
            for descriptor in snapshot.tools:
                sources[descriptor.wire_name] = (snapshot, descriptor)
        required_mcp_access = {
            (item.resource, operation)
            for item in policy.authority.access
            if item.kind is RuntimeAccessKind.MCP
            for operation in item.operations
        }
        selected: dict[str, list[RuntimeMcpToolDescriptor]] = defaultdict(list)
        for wire_name in policy.authority.mcp_tool_names:
            source = sources.get(wire_name)
            if source is None:
                raise AgentOrchestrationError(
                    "child_mcp_scope_denied",
                    "子 Agent MCP 工具不在父请求快照内",
                )
            snapshot, descriptor = source
            if (f"mcp-server:{descriptor.server_id}", "call") not in required_mcp_access:
                raise AgentOrchestrationError(
                    "child_mcp_server_access_missing",
                    "子 Agent MCP 工具缺少对应 server call 权限",
                )
            selected[snapshot.provider_id].append(descriptor)
        return tuple(
            RuntimeMcpSnapshot(
                provider_id=source.provider_id,
                revision=f"{source.revision}/child",
                tools=tuple(selected[source.provider_id]),
            )
            for source in self._environment.mcp_snapshots
            if selected.get(source.provider_id)
        )

    def _render_prompt(
        self,
        context: AgentTaskExecutionContext,
        *,
        skill_contents: tuple[RuntimeSkillContent, ...],
        mcp_snapshots: tuple[RuntimeMcpSnapshot, ...],
    ) -> tuple[RuntimeMessage, RuntimeMessage]:
        payload = {
            "task_id": context.task.task_id,
            "attempt_no": context.attempt_no,
            "idempotency_key": context.task.retry_policy.idempotency_key,
            "role": {
                "role_id": context.role.role_id,
                "kind": context.role.kind.value,
                "description": context.role.description,
            },
            "purpose": context.task.runtime_policy.purpose.value,
            "description": context.task.description,
            "inputs": context.inputs,
            "dependencies": [
                {
                    "task_id": item.task_id,
                    "state": item.state.value,
                    "output_sha256": item.output_sha256,
                }
                for item in context.dependencies
            ],
            "output_contract": context.task.output_contract.to_dict(),
            "completion": context.task.completion.to_dict(),
            "allowed_tools": sorted(
                context.task.runtime_policy.authority.tool_names
            ),
            "skills": [
                {
                    "qualified_id": item.descriptor.qualified_id,
                    "content_sha256": item.descriptor.content_sha256,
                    "document": item.document.decode("utf-8"),
                }
                for item in skill_contents
            ],
            "mcp_tools": sorted(
                descriptor.wire_name
                for snapshot in mcp_snapshots
                for descriptor in snapshot.tools
            ),
        }
        payload_bytes = canonical_json_bytes(payload)
        if len(payload_bytes) > MAX_CHILD_PROMPT_PAYLOAD_BYTES:
            raise AgentOrchestrationError(
                "child_prompt_payload_too_large",
                "子任务输入与 Skill 正文超过 Prompt Runtime 上限",
                next_actions=("缩小输入或把大内容发布为 Artifact",),
            )
        payload_json = payload_bytes.decode("utf-8")
        rendered = render_task_messages(
            AGENT_SUBTASK_PROMPT_KEY,
            {"message": payload_json},
            fallback_messages=[],
        )
        if (
            len(rendered) != 2
            or rendered[0].get("role") != "system"
            or rendered[1].get("role") != "user"
            or not str(rendered[0].get("content") or "").strip()
            or not str(rendered[1].get("content") or "").strip()
        ):
            raise AgentOrchestrationError(
                "child_prompt_runtime_invalid",
                "canonical 子任务 Prompt Runtime 没有生成 system/user 消息对",
            )
        return (
            RuntimeMessage("system", str(rendered[0]["content"])),
            RuntimeMessage("user", str(rendered[1]["content"])),
        )

    def _child_context(
        self,
        execution: AgentTaskExecutionContext,
        *,
        policy: AgentTaskRuntimePolicy,
        route: RuntimeModelRoute,
        grants: tuple[RuntimeAccessGrant, ...],
        tool_plan: ToolPlan | None,
        skill_snapshots: tuple[RuntimeSkillSnapshot, ...],
        mcp_snapshots: tuple[RuntimeMcpSnapshot, ...],
        prompt_sha256: str,
    ) -> RequestRuntimeContext:
        parent = self._environment.parent_context
        parent_identity = parent.execution_identity()
        task_budget = policy.budget
        allowed_models = (route.model_id,)
        budgets = RuntimeBudgetEnvelope(
            run=RuntimeBudgetLimit(
                RuntimeBudgetScope.RUN,
                model_call_limit=task_budget.model_call_limit,
                token_limit=task_budget.token_limit,
                cost_limit_microunits=task_budget.cost_limit_microunits,
                step_limit=task_budget.step_limit,
                time_limit_ms=task_budget.time_limit_ms,
                concurrency_limit=1,
                allowed_model_ids=allowed_models,
            ),
            turn=RuntimeBudgetLimit(
                RuntimeBudgetScope.TURN,
                model_call_limit=task_budget.model_call_limit,
                token_limit=task_budget.token_limit,
                cost_limit_microunits=task_budget.cost_limit_microunits,
                step_limit=task_budget.step_limit,
                time_limit_ms=task_budget.time_limit_ms,
                concurrency_limit=1,
                allowed_model_ids=allowed_models,
            ),
            tool=RuntimeBudgetLimit(
                RuntimeBudgetScope.TOOL,
                model_call_limit=0,
                token_limit=0,
                cost_limit_microunits=0,
                step_limit=task_budget.tool_call_limit,
                time_limit_ms=task_budget.time_limit_ms,
                concurrency_limit=(1 if task_budget.tool_call_limit else 0),
            ),
            subagent=RuntimeBudgetLimit(
                RuntimeBudgetScope.SUBAGENT,
                model_call_limit=0,
                token_limit=0,
                cost_limit_microunits=0,
                step_limit=0,
                time_limit_ms=0,
                concurrency_limit=0,
            ),
        )
        governance = RuntimeGovernanceEnvelope(
            policy_id="subagent-runtime-governance-v1",
            budgets=budgets,
            access=RuntimeAccessEnvelope(grants),
        )
        plans = [
            RuntimePlanRef(
                RuntimePlanKind.PROMPT,
                f"prompt:{AGENT_SUBTASK_PROMPT_KEY}",
                prompt_sha256,
            ),
            RuntimePlanRef(
                RuntimePlanKind.MODEL,
                f"model-route:{route.route_id}",
                runtime_model_route_sha256(route),
            ),
        ]
        if tool_plan is not None:
            plans.append(RuntimePlanRef(
                RuntimePlanKind.TOOL,
                "tool-plan:subagent",
                tool_plan.sha256,
            ))
        if skill_snapshots:
            plans.append(RuntimePlanRef(
                RuntimePlanKind.SKILL,
                "skill-snapshot:subagent",
                self._snapshot_set_sha256(
                    tuple(item.snapshot_sha256 for item in skill_snapshots)
                ),
            ))
        if mcp_snapshots:
            plans.append(RuntimePlanRef(
                RuntimePlanKind.MCP,
                "mcp-snapshot:subagent",
                self._snapshot_set_sha256(
                    tuple(item.snapshot_sha256 for item in mcp_snapshots)
                ),
            ))
        inherited_plan_kinds: set[RuntimePlanKind] = set()
        for requirement in policy.authority.access:
            if (
                requirement.kind is RuntimeAccessKind.FILE
                and requirement.resource.startswith("workspace:")
            ):
                inherited_plan_kinds.add(RuntimePlanKind.WORKSPACE)
            elif (
                requirement.kind is RuntimeAccessKind.FILE
                and requirement.resource.startswith("assets:")
            ):
                inherited_plan_kinds.add(RuntimePlanKind.ARTIFACT)
            elif requirement.kind is RuntimeAccessKind.MEMORY:
                inherited_plan_kinds.add(RuntimePlanKind.MEMORY)
        for plan_kind in sorted(inherited_plan_kinds, key=lambda item: item.value):
            reference = parent.plan(plan_kind)
            if reference is None:
                raise AgentOrchestrationError(
                    "child_scope_plan_missing",
                    f"父请求缺少 {plan_kind.value} 固定计划，不能授权子 Agent",
                )
            plans.append(reference)

        now = self._now()
        deadline = now + timedelta(milliseconds=task_budget.time_limit_ms)
        if parent.deadline_at is not None:
            deadline = min(deadline, parent.deadline_at)
        suffix = hashlib.sha256(
            (
                f"{execution.orchestration_id}:{execution.task.task_id}:"
                f"{execution.attempt_no}"
            ).encode("utf-8")
        ).hexdigest()[:16]
        child_run_id = f"{parent_identity.run_id}:subagent:{suffix}"
        return RequestRuntimeContext(
            request_id=f"subagent-request:{suffix}",
            agent_id=f"{parent.agent_id}/subagent/{execution.role.role_id}",
            principal=parent.principal,
            session_id=(
                f"subagent:{execution.orchestration_id}:"
                f"{execution.task.task_id}:{execution.attempt_no}"
            ),
            chat_type=RuntimeChatType.TASK,
            trace_id=parent.trace_id or parent_identity.correlation_id,
            run_id=child_run_id,
            turn_id=f"{child_run_id}:turn:1",
            correlation_id=parent_identity.correlation_id,
            actor=RuntimeActor(
                RuntimeActorType.AGENT,
                (
                    f"subagent:{execution.role.role_id}:"
                    f"{execution.task.task_id}:{execution.attempt_no}"
                ),
                parent_actor_id=parent_identity.actor.actor_id,
            ),
            capabilities=frozenset({"bounded_subagent"}),
            features=(),
            plans=tuple(plans),
            deadline_at=deadline,
            governance=governance,
        )

    @staticmethod
    def _snapshot_set_sha256(digests: tuple[str, ...]) -> str:
        return hashlib.sha256(canonical_json_bytes(sorted(digests))).hexdigest()

    def _parse_output(
        self,
        context: AgentTaskExecutionContext,
        result: AgentTurnResult,
        observed: _ObservedEvents,
    ) -> AgentTaskOutput:
        assistant = next((
            message
            for message in reversed(result.messages)
            if message.role == "assistant" and isinstance(message.content, str)
        ), None)
        if assistant is None:
            raise AgentOrchestrationError(
                "child_output_missing",
                "子 Runtime 没有返回 assistant 结构化输出",
            )
        try:
            payload = parse_task_output(
                AGENT_SUBTASK_PROMPT_KEY,
                assistant.content,
            )
        except TaskOutputContractError as exc:
            raise AgentOrchestrationError(
                "child_output_contract_invalid",
                "子 Runtime 输出未通过 canonical 结构化合同",
            ) from exc
        status = AgentTaskOutputStatus(payload["status"])
        try:
            if status is AgentTaskOutputStatus.ERROR:
                data = context.task.output_contract.validate_partial(
                    payload["data"],
                    name="child task error output data",
                )
            else:
                data = context.task.output_contract.validate(
                    payload["data"],
                    name="child task output data",
                )
        except ValueError as exc:
            raise AgentOrchestrationError(
                "child_task_data_invalid",
                "子 Runtime data 未通过计划的动态输出合同",
            ) from exc
        artifact_ids = tuple(payload["artifacts"])
        missing_artifacts = [
            artifact_id for artifact_id in artifact_ids
            if artifact_id not in observed.artifacts
        ]
        if missing_artifacts:
            raise AgentOrchestrationError(
                "child_artifact_unpublished",
                "子 Runtime 输出引用了未通过事件发布的 Artifact",
            )
        policy = context.task.runtime_policy
        assert policy is not None
        if result.model_calls <= 0 or result.usage.total_tokens <= 0:
            raise AgentOrchestrationError(
                "child_usage_contract_missing",
                "子 Runtime 没有回传可计入父预算的模型调用与 token 用量",
            )
        if (
            result.model_calls > policy.budget.model_call_limit
            or result.usage.total_tokens > policy.budget.token_limit
            or result.usage.cost_microunits
            > policy.budget.cost_limit_microunits
            or len(result.tool_calls) > policy.budget.tool_call_limit
        ):
            raise AgentOrchestrationError(
                "child_reported_budget_exceeded",
                "子 Runtime 回传用量超过任务冻结预算",
            )
        if observed.usage != [result.usage]:
            raise AgentOrchestrationError(
                "child_usage_event_mismatch",
                "子 Runtime usage 事件与最终结果不一致",
            )
        result_call_ids = {item.call_id for item in result.tool_calls}
        if len(result_call_ids) != len(result.tool_calls):
            raise AgentOrchestrationError(
                "child_tool_result_duplicate",
                "子 Runtime 结果包含重复工具调用 ID",
            )
        if observed.tool_call_ids != result_call_ids:
            raise AgentOrchestrationError(
                "child_tool_event_mismatch",
                "子 Runtime 工具事件与最终结果不一致",
            )
        output_data = dict(data)
        if status is AgentTaskOutputStatus.ERROR:
            output_data["error_code"] = "child_reported_error"
        return AgentTaskOutput(
            status=status,
            summary=payload["summary"],
            next_actions=tuple(payload["next_actions"]),
            artifacts=tuple(observed.artifacts[item] for item in artifact_ids),
            data=output_data,
            usage=result.usage,
            model_calls=result.model_calls,
            tool_calls=len(result.tool_calls),
        )

    def _failure_output(
        self,
        context: AgentTaskExecutionContext,
        error: BaseException,
        *,
        result: AgentTurnResult | None = None,
        observed: _ObservedEvents | None = None,
    ) -> AgentTaskOutput:
        if isinstance(error, AgentOrchestrationError):
            code = error.code
            summary = error.summary
            next_actions = error.next_actions
        else:
            code = "child_runtime_execution_failed"
            summary = "子 Runtime 执行失败"
            next_actions = ("检查 Runtime 事件与冻结计划后创建新编排",)
        usage, model_calls, tool_calls = self._failure_consumption(
            context,
            result=result,
            observed=observed,
        )
        return AgentTaskOutput(
            status=AgentTaskOutputStatus.ERROR,
            summary=summary,
            next_actions=next_actions,
            data={"error_code": code},
            usage=usage,
            model_calls=model_calls,
            tool_calls=tool_calls,
        )

    @staticmethod
    def _failure_consumption(
        context: AgentTaskExecutionContext,
        *,
        result: AgentTurnResult | None,
        observed: _ObservedEvents | None,
    ) -> tuple[RuntimeUsage, int, int]:
        policy = context.task.runtime_policy
        assert policy is not None
        if result is not None and observed is not None:
            result_call_ids = {item.call_id for item in result.tool_calls}
            if (
                0 < result.model_calls <= policy.budget.model_call_limit
                and 0 < result.usage.total_tokens <= policy.budget.token_limit
                and result.usage.cost_microunits
                <= policy.budget.cost_limit_microunits
                and len(result.tool_calls) <= policy.budget.tool_call_limit
                and len(result_call_ids) == len(result.tool_calls)
                and observed.usage == [result.usage]
                and observed.tool_call_ids == result_call_ids
            ):
                return result.usage, result.model_calls, len(result.tool_calls)
        return (
            RuntimeUsage(
                input_tokens=policy.budget.token_limit,
                cost_microunits=policy.budget.cost_limit_microunits,
            ),
            policy.budget.model_call_limit,
            policy.budget.tool_call_limit,
        )


__all__ = [
    "AGENT_SUBTASK_PROMPT_KEY",
    "AgentRuntimeTaskExecutor",
    "AgentTaskRuntimeEnvironment",
    "ChildAgentRuntimeBinding",
    "ChildAgentRuntimeFactory",
    "ChildAgentRuntimeLease",
]
