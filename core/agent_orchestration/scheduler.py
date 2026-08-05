"""依赖优先、协调者集中派发的有界 DAG 调度器。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Callable, Mapping

from core.agent_orchestration.contracts import (
    MULTI_AGENT_FEATURE_ID,
    AgentOrchestrationCheckpoint,
    AgentOrchestrationCheckpointStore,
    AgentOrchestrationError,
    AgentOrchestrationRequest,
    AgentOrchestrationResult,
    AgentOrchestrationState,
    AgentTaskDefinition,
    AgentTaskDependencyReceipt,
    AgentTaskExecutionContext,
    AgentTaskExecutionReceipt,
    AgentTaskExecutor,
    AgentTaskOutput,
    AgentTaskOutputStatus,
    AgentTaskState,
)
from core.agent_orchestration.scope import (
    current_orchestration_depth,
    orchestration_worker_scope,
)
from core.agent_runtime import (
    AgentRuntimeBudgetExceededError,
    RuntimeBudgetAccount,
    RuntimeUsage,
)
from core.lifecycle.feature_registry import (
    FeatureDecisionCode,
    FeatureEnablementDecision,
)


@dataclass(slots=True)
class _UsageTotals:
    model_calls: int = 0
    tokens: int = 0
    cost_microunits: int = 0
    output_bytes: int = 0


@dataclass(frozen=True, slots=True)
class _TaskOutcome:
    receipt: AgentTaskExecutionReceipt
    output: AgentTaskOutput


class AgentOrchestrationCancellation:
    """由宿主持有的幂等取消信号；模型不能构造权限。"""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason_code = ""

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    @property
    def reason_code(self) -> str:
        return self._reason_code or "cancel_requested"

    def request(self, reason_code: str = "cancel_requested") -> bool:
        normalized = str(reason_code or "cancel_requested").strip()
        if (
            not normalized
            or len(normalized) > 128
            or any(character.isspace() for character in normalized)
        ):
            raise ValueError("cancel reason_code 无效")
        if self._event.is_set():
            return False
        self._reason_code = normalized
        self._event.set()
        return True

    async def wait(self) -> None:
        await self._event.wait()


class AgentDagOrchestrator:
    """只允许协调者按冻结 DAG 派发单层 Worker。"""

    def __init__(
        self,
        *,
        executor: AgentTaskExecutor,
        checkpoint_store: AgentOrchestrationCheckpointStore,
        budget_account: RuntimeBudgetAccount,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(executor, AgentTaskExecutor):
            raise TypeError("executor 必须实现 AgentTaskExecutor")
        if not isinstance(checkpoint_store, AgentOrchestrationCheckpointStore):
            raise TypeError("checkpoint_store 必须实现稳定 Store 合同")
        if not isinstance(budget_account, RuntimeBudgetAccount):
            raise TypeError("budget_account 必须是 RuntimeBudgetAccount")
        if not callable(monotonic):
            raise TypeError("monotonic 必须可调用")
        self._executor = executor
        self._checkpoint_store = checkpoint_store
        self._budget = budget_account
        self._monotonic = monotonic
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def execute(
        self,
        request: AgentOrchestrationRequest,
        *,
        feature_decision: FeatureEnablementDecision,
        cancellation: AgentOrchestrationCancellation | None = None,
    ) -> AgentOrchestrationResult:
        if not isinstance(request, AgentOrchestrationRequest):
            raise TypeError("request 必须是 AgentOrchestrationRequest")
        self._admit(request, feature_decision)
        cancel = cancellation or AgentOrchestrationCancellation()
        if not isinstance(cancel, AgentOrchestrationCancellation):
            raise TypeError("cancellation 无效")

        started = self._monotonic()
        deadline = started + request.plan.budget.max_elapsed_ms / 1000
        task_states = {
            task.task_id: AgentTaskState.PENDING for task in request.plan.tasks
        }
        outputs: dict[str, AgentTaskOutput] = {}
        receipts: list[AgentTaskExecutionReceipt] = []
        receipt_by_id: dict[str, AgentTaskExecutionReceipt] = {}
        usage = _UsageTotals()
        latest_checkpoint_id = ""

        for batch in request.plan.execution_batches():
            if cancel.requested:
                outcomes = [
                    self._terminal_outcome(
                        request.plan.task_by_id[task_id],
                        state=AgentTaskState.CANCELLED,
                        error_code=cancel.reason_code,
                    )
                    for task_id in batch
                ]
            elif self._monotonic() >= deadline:
                outcomes = [
                    self._terminal_outcome(
                        request.plan.task_by_id[task_id],
                        state=AgentTaskState.TIMED_OUT,
                        error_code="orchestration_time_limit",
                    )
                    for task_id in batch
                ]
            else:
                outcomes = await self._execute_batch(
                    request,
                    batch=batch,
                    task_states=task_states,
                    outputs=outputs,
                    receipts=receipt_by_id,
                    usage=usage,
                    deadline=deadline,
                    cancellation=cancel,
                )

            for outcome in sorted(outcomes, key=lambda item: item.receipt.task_id):
                receipt = outcome.receipt
                task_states[receipt.task_id] = receipt.state
                outputs[receipt.task_id] = outcome.output
                receipts.append(receipt)
                receipt_by_id[receipt.task_id] = receipt
                try:
                    checkpoint = await self._save_checkpoint(
                        request,
                        task_states=task_states,
                        outputs=outputs,
                        receipts=receipts,
                        parent_checkpoint_id=latest_checkpoint_id,
                    )
                except Exception as exc:
                    return AgentOrchestrationResult(
                        orchestration_id=request.orchestration_id,
                        state=AgentOrchestrationState.FAILED,
                        plan_sha256=request.plan.content_sha256,
                        receipts=tuple(receipts),
                        outputs=outputs,
                        aggregate_output=None,
                        latest_checkpoint_id=latest_checkpoint_id,
                        failure_code=(
                            exc.code
                            if isinstance(exc, AgentOrchestrationError)
                            else "checkpoint_store_failed"
                        ),
                    )
                latest_checkpoint_id = checkpoint.checkpoint_id

        aggregate = outputs.get(request.plan.aggregation_task_id)
        aggregate_receipt = receipt_by_id.get(request.plan.aggregation_task_id)
        if cancel.requested:
            state = AgentOrchestrationState.CANCELLED
            failure_code = cancel.reason_code
            aggregate = None
        elif (
            aggregate_receipt is not None
            and aggregate_receipt.state is AgentTaskState.SUCCEEDED
            and len(receipts) == len(request.plan.tasks)
            and all(item.state is AgentTaskState.SUCCEEDED for item in receipts)
        ):
            state = AgentOrchestrationState.SUCCEEDED
            failure_code = ""
        else:
            state = AgentOrchestrationState.FAILED
            aggregate = None
            failure_code = next(
                (
                    item.error_code
                    for item in receipts
                    if item.state is not AgentTaskState.SUCCEEDED
                ),
                "completion_condition_failed",
            )
        return AgentOrchestrationResult(
            orchestration_id=request.orchestration_id,
            state=state,
            plan_sha256=request.plan.content_sha256,
            receipts=tuple(receipts),
            outputs=outputs,
            aggregate_output=aggregate,
            latest_checkpoint_id=latest_checkpoint_id,
            failure_code=failure_code,
        )

    def _admit(
        self,
        request: AgentOrchestrationRequest,
        feature_decision: FeatureEnablementDecision,
    ) -> None:
        if (
            not isinstance(feature_decision, FeatureEnablementDecision)
            or feature_decision.feature_id != MULTI_AGENT_FEATURE_ID
            or not feature_decision.enabled
            or feature_decision.code is not FeatureDecisionCode.ENABLED
        ):
            raise AgentOrchestrationError(
                "multi_agent_disabled",
                "多 Agent 编排默认关闭，当前请求没有通过 Feature 门禁",
                next_actions=("由管理员满足全部 enablement gate 后重新批准计划",),
                stop_condition="未取得显式启用决定时保持单 Agent 主链路",
            )
        if request.identity != self._budget.identity:
            raise AgentOrchestrationError(
                "budget_identity_mismatch",
                "编排身份与当前预算账户不一致",
                next_actions=("重新绑定当前 Run、Turn 与 owner 的预算账户",),
            )
        if request.nesting_depth != 0 or current_orchestration_depth() != 0:
            raise AgentOrchestrationError(
                "recursive_spawn_denied",
                "首版多 Agent 只允许协调者创建一层 Worker",
                next_actions=("把嵌套任务展开为同一冻结 DAG 的显式节点",),
                stop_condition="Worker 上下文中不得再次调用编排器",
            )
        plan_budget = request.plan.budget
        parent = self._budget.governance.budgets.subagent
        parent_limits = {
            "max_tasks": parent.step_limit,
            "max_concurrency": parent.concurrency_limit,
            "max_model_calls": parent.model_call_limit,
            "max_tokens": parent.token_limit,
            "max_cost_microunits": parent.cost_limit_microunits,
            "max_elapsed_ms": parent.time_limit_ms,
        }
        for field_name, parent_limit in parent_limits.items():
            requested = getattr(plan_budget, field_name)
            if parent_limit <= 0 or requested > parent_limit:
                raise AgentOrchestrationError(
                    "subagent_budget_denied",
                    f"计划 {field_name} 超过父 Run 的显式 subagent 预算",
                    next_actions=("缩小计划预算或由宿主下发非零且不扩张的父预算",),
                    stop_condition="没有显式父预算时禁止 spawn",
                )

    async def _execute_batch(
        self,
        request: AgentOrchestrationRequest,
        *,
        batch: tuple[str, ...],
        task_states: Mapping[str, AgentTaskState],
        outputs: Mapping[str, AgentTaskOutput],
        receipts: Mapping[str, AgentTaskExecutionReceipt],
        usage: _UsageTotals,
        deadline: float,
        cancellation: AgentOrchestrationCancellation,
    ) -> list[_TaskOutcome]:
        pending: list[asyncio.Task[_TaskOutcome]] = []
        immediate: list[_TaskOutcome] = []
        for task_id in batch:
            task = request.plan.task_by_id[task_id]
            if any(
                task_states[dependency] is not AgentTaskState.SUCCEEDED
                for dependency in task.dependencies
            ):
                immediate.append(self._terminal_outcome(
                    task,
                    state=AgentTaskState.BLOCKED,
                    error_code="dependency_not_succeeded",
                ))
                continue
            pending.append(asyncio.create_task(
                self._execute_task(
                    request,
                    task=task,
                    outputs=outputs,
                    receipts=receipts,
                    usage=usage,
                    deadline=deadline,
                    cancellation=cancellation,
                ),
                name=f"agent-dag:{request.orchestration_id}:{task_id}",
            ))
        if not pending:
            return immediate
        completed = await asyncio.gather(*pending)
        return [*immediate, *completed]

    async def _execute_task(
        self,
        request: AgentOrchestrationRequest,
        *,
        task: AgentTaskDefinition,
        outputs: Mapping[str, AgentTaskOutput],
        receipts: Mapping[str, AgentTaskExecutionReceipt],
        usage: _UsageTotals,
        deadline: float,
        cancellation: AgentOrchestrationCancellation,
    ) -> _TaskOutcome:
        started_at = self._now()
        started_clock = self._monotonic()
        reservation = None
        try:
            reservation = self._budget.reserve_subagent(task.task_id)
            inputs = self._resolve_inputs(request, task, outputs)
            dependency_receipts = tuple(
                AgentTaskDependencyReceipt(
                    task_id=dependency,
                    state=receipts[dependency].state,
                    output_sha256=receipts[dependency].output_sha256,
                )
                for dependency in task.dependencies
            )
            context = AgentTaskExecutionContext(
                orchestration_id=request.orchestration_id,
                identity=request.identity,
                task=task,
                role=request.plan.role_by_id[task.role_id],
                inputs=inputs,
                dependencies=dependency_receipts,
            )
            timeout_seconds = min(
                task.timeout_ms / 1000,
                max(0.001, deadline - self._monotonic()),
                self._budget.subagent_remaining_time_seconds(),
            )
            output = await self._await_executor(
                context,
                timeout_seconds=timeout_seconds,
                cancellation=cancellation,
            )
            task.output_contract.validate(output.data, name="task output data")
            self._record_usage(request, usage, output)
            if not task.completion.matches(output):
                return self._outcome(
                    task,
                    state=AgentTaskState.FAILED,
                    output=output,
                    error_code="completion_condition_failed",
                    started_at=started_at,
                    started_clock=started_clock,
                    reservation_id=reservation.reservation_id,
                )
            return self._outcome(
                task,
                state=AgentTaskState.SUCCEEDED,
                output=output,
                error_code="",
                started_at=started_at,
                started_clock=started_clock,
                reservation_id=reservation.reservation_id,
            )
        except asyncio.TimeoutError:
            return self._outcome(
                task,
                state=AgentTaskState.TIMED_OUT,
                output=self._error_output(
                    "task_timeout",
                    "子任务执行超时",
                    "缩小任务或提高已批准计划内的单任务超时",
                ),
                error_code="task_timeout",
                started_at=started_at,
                started_clock=started_clock,
                reservation_id=(reservation.reservation_id if reservation else ""),
            )
        except AgentOrchestrationError as exc:
            return self._outcome(
                task,
                state=(
                    AgentTaskState.CANCELLED
                    if exc.code == "task_cancelled"
                    else AgentTaskState.FAILED
                ),
                output=self._error_output(
                    exc.code,
                    exc.summary,
                    exc.next_actions[0] if exc.next_actions else exc.stop_condition,
                ),
                error_code=exc.code,
                started_at=started_at,
                started_clock=started_clock,
                reservation_id=(reservation.reservation_id if reservation else ""),
            )
        except AgentRuntimeBudgetExceededError:
            return self._outcome(
                task,
                state=AgentTaskState.FAILED,
                output=self._error_output(
                    "runtime_budget_exceeded",
                    "父 Runtime 拒绝了子任务预算消费",
                    "检查当前 Run 的剩余预算后提交更小的新计划",
                ),
                error_code="runtime_budget_exceeded",
                started_at=started_at,
                started_clock=started_clock,
                reservation_id=(reservation.reservation_id if reservation else ""),
            )
        except Exception:
            return self._outcome(
                task,
                state=AgentTaskState.FAILED,
                output=self._error_output(
                    "task_executor_failed",
                    "子任务执行器返回异常",
                    "检查执行器日志与冻结输入摘要后再决定是否重试",
                ),
                error_code="task_executor_failed",
                started_at=started_at,
                started_clock=started_clock,
                reservation_id=(reservation.reservation_id if reservation else ""),
            )
        finally:
            if reservation is not None:
                self._budget.release(reservation)

    async def _await_executor(
        self,
        context: AgentTaskExecutionContext,
        *,
        timeout_seconds: float,
        cancellation: AgentOrchestrationCancellation,
    ) -> AgentTaskOutput:
        async def invoke() -> AgentTaskOutput:
            with orchestration_worker_scope():
                result = await self._executor.execute(context)
            if not isinstance(result, AgentTaskOutput):
                raise TypeError("executor 必须返回 AgentTaskOutput")
            return result

        work = asyncio.create_task(invoke())
        cancel_wait = asyncio.create_task(cancellation.wait())
        try:
            done, _ = await asyncio.wait(
                {work, cancel_wait},
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancellation.requested:
                await self._cancel_work(work)
                raise AgentOrchestrationError(
                    "task_cancelled",
                    "协调者已取消子任务",
                    next_actions=("检查最新 checkpoint 后决定是否创建新计划",),
                    stop_condition="取消后不得继续发布任务结果",
                )
            if work in done:
                return work.result()
            await self._cancel_work(work)
            raise asyncio.TimeoutError
        finally:
            cancel_wait.cancel()
            await asyncio.gather(cancel_wait, return_exceptions=True)

    @staticmethod
    async def _cancel_work(work: asyncio.Task[AgentTaskOutput]) -> None:
        if work.done():
            return
        work.cancel()
        done, pending = await asyncio.wait({work}, timeout=1.0)
        if pending:
            work.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
            raise AgentOrchestrationError(
                "task_cancel_unconfirmed",
                "子任务在取消宽限期后仍未确认停止",
                next_actions=("中断底层 Runtime 并停放本次编排",),
                stop_condition="未确认停止前不得重用工作区或重放副作用",
            )
        await asyncio.gather(*done, return_exceptions=True)

    @staticmethod
    def _resolve_inputs(
        request: AgentOrchestrationRequest,
        task: AgentTaskDefinition,
        outputs: Mapping[str, AgentTaskOutput],
    ) -> Mapping[str, object]:
        resolved: dict[str, object] = {}
        for binding in task.input_bindings:
            source: Mapping[str, object]
            if binding.from_root:
                source = request.root_input
            else:
                source_output = outputs.get(binding.source_task_id)
                if source_output is None:
                    raise AgentOrchestrationError(
                        "dependency_output_missing",
                        "协调者没有找到依赖任务的结构化输出",
                        next_actions=("检查 checkpoint 与 dependency receipt 是否一致",),
                    )
                source = source_output.data
            if binding.source_key not in source:
                if binding.required:
                    raise AgentOrchestrationError(
                        "task_input_missing",
                        "任务必需输入没有由根输入或依赖输出提供",
                        next_actions=("修正冻结计划的 input binding",),
                    )
                continue
            resolved[binding.target_key] = source[binding.source_key]
        return task.input_contract.validate(resolved, name="task inputs")

    def _record_usage(
        self,
        request: AgentOrchestrationRequest,
        usage: _UsageTotals,
        output: AgentTaskOutput,
    ) -> None:
        self._budget.record_subagent_usage(
            output.usage,
            model_calls=output.model_calls,
        )
        projected = _UsageTotals(
            model_calls=usage.model_calls + output.model_calls,
            tokens=usage.tokens + output.usage.total_tokens,
            cost_microunits=(
                usage.cost_microunits + output.usage.cost_microunits
            ),
            output_bytes=usage.output_bytes + output.size_bytes,
        )
        budget = request.plan.budget
        exceeded = next((
            name
            for name, value, limit in (
                ("max_model_calls", projected.model_calls, budget.max_model_calls),
                ("max_tokens", projected.tokens, budget.max_tokens),
                (
                    "max_cost_microunits",
                    projected.cost_microunits,
                    budget.max_cost_microunits,
                ),
                ("max_output_bytes", projected.output_bytes, budget.max_output_bytes),
            )
            if value > limit
        ), "")
        if exceeded:
            raise AgentOrchestrationError(
                "orchestration_budget_exceeded",
                f"子任务结果超过计划 {exceeded}",
                next_actions=("缩小任务输出或提交预算更小的新计划",),
                stop_condition="预算超限后停止调度后续任务",
            )
        usage.model_calls = projected.model_calls
        usage.tokens = projected.tokens
        usage.cost_microunits = projected.cost_microunits
        usage.output_bytes = projected.output_bytes

    def _outcome(
        self,
        task: AgentTaskDefinition,
        *,
        state: AgentTaskState,
        output: AgentTaskOutput,
        error_code: str,
        started_at: datetime,
        started_clock: float,
        reservation_id: str,
    ) -> _TaskOutcome:
        finished_at = self._now()
        receipt = AgentTaskExecutionReceipt(
            task_id=task.task_id,
            role_id=task.role_id,
            state=state,
            attempt_no=1,
            dependency_ids=task.dependencies,
            output_sha256=output.content_sha256,
            output_size_bytes=output.size_bytes,
            error_code=error_code,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=max(0, int((self._monotonic() - started_clock) * 1000)),
            reservation_id=reservation_id,
        )
        return _TaskOutcome(receipt, output)

    def _terminal_outcome(
        self,
        task: AgentTaskDefinition,
        *,
        state: AgentTaskState,
        error_code: str,
    ) -> _TaskOutcome:
        now = self._now()
        output = self._error_output(
            error_code,
            "任务未执行",
            "检查依赖、取消状态与最新 checkpoint",
        )
        return _TaskOutcome(
            AgentTaskExecutionReceipt(
                task_id=task.task_id,
                role_id=task.role_id,
                state=state,
                attempt_no=1,
                dependency_ids=task.dependencies,
                output_sha256=output.content_sha256,
                output_size_bytes=output.size_bytes,
                error_code=error_code,
                started_at=now,
                finished_at=now,
                duration_ms=0,
                reservation_id="",
            ),
            output,
        )

    @staticmethod
    def _error_output(code: str, summary: str, next_action: str) -> AgentTaskOutput:
        return AgentTaskOutput(
            status=AgentTaskOutputStatus.ERROR,
            summary=summary,
            next_actions=(next_action,),
            data={"error_code": code},
            usage=RuntimeUsage(),
            model_calls=0,
        )

    async def _save_checkpoint(
        self,
        request: AgentOrchestrationRequest,
        *,
        task_states: Mapping[str, AgentTaskState],
        outputs: Mapping[str, AgentTaskOutput],
        receipts: list[AgentTaskExecutionReceipt],
        parent_checkpoint_id: str,
    ) -> AgentOrchestrationCheckpoint:
        sequence = len(receipts)
        if sequence > request.plan.budget.max_checkpoints:
            raise AgentOrchestrationError(
                "checkpoint_budget_exceeded",
                "checkpoint 数量超过冻结计划预算",
                next_actions=("减少任务节点后提交新计划",),
            )
        checkpoint = AgentOrchestrationCheckpoint(
            checkpoint_id=(
                f"orchestration:{request.orchestration_id}:checkpoint:{sequence}"
            ),
            orchestration_id=request.orchestration_id,
            identity=request.identity,
            plan_sha256=request.plan.content_sha256,
            sequence=sequence,
            parent_checkpoint_id=parent_checkpoint_id,
            task_states=task_states,
            outputs=outputs,
            receipt_sha256s=tuple(item.receipt_sha256 for item in receipts),
            created_at=self._now(),
        )
        if checkpoint.size_bytes > request.plan.budget.max_output_bytes:
            raise AgentOrchestrationError(
                "checkpoint_size_exceeded",
                "checkpoint 超过冻结计划的输出预算",
                next_actions=("把大结果发布为 Artifact，只在任务输出中保留引用",),
            )
        return await self._checkpoint_store.save(checkpoint)


__all__ = [
    "AgentDagOrchestrator",
    "AgentOrchestrationCancellation",
]
