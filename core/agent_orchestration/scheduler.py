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
    AgentOrchestrationUsage,
    AgentTaskBarrier,
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
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    cost_microunits: int = 0
    task_attempts: int = 0
    output_bytes: int = 0

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def snapshot(self) -> AgentOrchestrationUsage:
        return AgentOrchestrationUsage(
            usage=RuntimeUsage(
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                cached_input_tokens=self.cached_input_tokens,
                reasoning_tokens=self.reasoning_tokens,
                cost_microunits=self.cost_microunits,
            ),
            model_calls=self.model_calls,
            tool_calls=self.tool_calls,
            task_attempts=self.task_attempts,
            output_bytes=self.output_bytes,
        )


@dataclass(frozen=True, slots=True)
class _TaskOutcome:
    receipt: AgentTaskExecutionReceipt
    output: AgentTaskOutput


@dataclass(frozen=True, slots=True)
class _TaskSeries:
    outcomes: tuple[_TaskOutcome, ...]

    def __post_init__(self) -> None:
        if not self.outcomes:
            raise ValueError("task series 不能为空")
        task_ids = {item.receipt.task_id for item in self.outcomes}
        attempts = tuple(item.receipt.attempt_no for item in self.outcomes)
        if len(task_ids) != 1 or attempts != tuple(range(1, len(attempts) + 1)):
            raise ValueError("task series 必须属于同一任务且尝试连续")

    @property
    def final(self) -> _TaskOutcome:
        return self.outcomes[-1]


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

        try:
            existing = await self._checkpoint_store.load_latest(
                request.orchestration_id,
                owner_id=request.identity.owner.canonical_id,
            )
        except Exception as exc:
            raise AgentOrchestrationError(
                "checkpoint_store_failed",
                "无法确认编排是否已经开始",
                next_actions=("修复持久 checkpoint Store 后重新读取",),
                stop_condition="checkpoint 状态不明时禁止重复派发任务",
            ) from exc
        if existing is not None:
            return self._existing_checkpoint_result(request, existing)

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

        for barrier in request.plan.execution_barriers():
            if cancel.requested:
                series = [
                    _TaskSeries((self._terminal_outcome(
                        request.plan.task_by_id[task_id],
                        state=AgentTaskState.CANCELLED,
                        error_code=cancel.reason_code,
                    ),))
                    for task_id in barrier.task_ids
                ]
            elif self._monotonic() >= deadline:
                series = [
                    _TaskSeries((self._terminal_outcome(
                        request.plan.task_by_id[task_id],
                        state=AgentTaskState.TIMED_OUT,
                        error_code="orchestration_time_limit",
                    ),))
                    for task_id in barrier.task_ids
                ]
            else:
                series = await self._execute_batch(
                    request,
                    barrier=barrier,
                    task_states=task_states,
                    outputs=outputs,
                    receipts=receipt_by_id,
                    usage=usage,
                    deadline=deadline,
                    cancellation=cancel,
                )

            for task_series in sorted(
                series,
                key=lambda item: item.final.receipt.task_id,
            ):
                for outcome in task_series.outcomes:
                    receipts.append(outcome.receipt)
                    usage.task_attempts += 1
                final = task_series.final
                receipt = final.receipt
                task_states[receipt.task_id] = receipt.state
                outputs[receipt.task_id] = final.output
                receipt_by_id[receipt.task_id] = receipt
            try:
                checkpoint = await self._save_checkpoint(
                    request,
                    barrier=barrier,
                    task_states=task_states,
                    outputs=outputs,
                    receipts=receipts,
                    usage=usage,
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
            and len(receipt_by_id) == len(request.plan.tasks)
            and all(
                item.state is AgentTaskState.SUCCEEDED
                for item in receipt_by_id.values()
            )
        ):
            state = AgentOrchestrationState.SUCCEEDED
            failure_code = ""
        else:
            state = AgentOrchestrationState.FAILED
            aggregate = None
            failure_code = next(
                (
                    item.error_code
                    for item in receipt_by_id.values()
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

    @staticmethod
    def _existing_checkpoint_result(
        request: AgentOrchestrationRequest,
        checkpoint: AgentOrchestrationCheckpoint,
    ) -> AgentOrchestrationResult:
        """只重放已持久化终态；中间屏障必须通过新计划修复。"""

        barriers = request.plan.execution_barriers()
        if (
            checkpoint.orchestration_id != request.orchestration_id
            or checkpoint.identity != request.identity
            or checkpoint.plan_id != request.plan.plan_id
            or checkpoint.plan_revision != request.plan.revision
            or checkpoint.plan_sha256 != request.plan.content_sha256
            or checkpoint.freeze_id != request.freeze.freeze_id
            or checkpoint.sequence > len(barriers)
            or checkpoint.barrier_id
            != barriers[checkpoint.sequence - 1].barrier_id
        ):
            raise AgentOrchestrationError(
                "checkpoint_identity_mismatch",
                "已有 checkpoint 与当前冻结计划或身份不一致",
                next_actions=("使用原 owner 和原冻结计划读取 checkpoint",),
                stop_condition="不得覆盖或重解释已有 checkpoint",
            )
        if checkpoint.sequence != len(barriers):
            raise AgentOrchestrationError(
                "plan_repair_required",
                "编排停在已持久化的任务屏障，禁止自动重放后续任务",
                next_actions=("基于该 checkpoint 预览并批准新的计划修订",),
                stop_condition="未批准 append-only repair 前保持停放",
            )
        task_ids = set(request.plan.task_by_id)
        if set(checkpoint.task_states) != task_ids or any(
            not state.terminal for state in checkpoint.task_states.values()
        ):
            raise AgentOrchestrationError(
                "checkpoint_state_invalid",
                "最终 checkpoint 的任务状态不完整",
                next_actions=("审计 checkpoint 摘要链与任务回执",),
            )
        final_receipts: dict[str, AgentTaskExecutionReceipt] = {}
        for receipt in checkpoint.receipts:
            task = request.plan.task_by_id.get(receipt.task_id)
            if (
                task is None
                or receipt.role_id != task.role_id
                or receipt.attempt_no > task.retry_policy.max_attempts
            ):
                raise AgentOrchestrationError(
                    "checkpoint_receipt_invalid",
                    "checkpoint 含不属于冻结计划的任务回执",
                    next_actions=("审计持久 Store 并停止该编排",),
                )
            previous = final_receipts.get(receipt.task_id)
            if previous is None or receipt.attempt_no > previous.attempt_no:
                final_receipts[receipt.task_id] = receipt
        if set(final_receipts) != task_ids:
            raise AgentOrchestrationError(
                "checkpoint_receipt_invalid",
                "最终 checkpoint 缺少任务回执",
                next_actions=("审计持久 Store 并停止该编排",),
            )
        aggregate = checkpoint.outputs.get(
            request.plan.aggregation_task_id
        )
        if all(
            receipt.state is AgentTaskState.SUCCEEDED
            for receipt in final_receipts.values()
        ) and aggregate is not None:
            state = AgentOrchestrationState.SUCCEEDED
            failure_code = ""
        elif any(
            receipt.state is AgentTaskState.CANCELLED
            for receipt in final_receipts.values()
        ):
            state = AgentOrchestrationState.CANCELLED
            aggregate = None
            failure_code = next(
                receipt.error_code
                for receipt in final_receipts.values()
                if receipt.state is AgentTaskState.CANCELLED
            )
        else:
            state = AgentOrchestrationState.FAILED
            aggregate = None
            failure_code = next(
                (
                    receipt.error_code
                    for receipt in final_receipts.values()
                    if receipt.state is not AgentTaskState.SUCCEEDED
                ),
                "completion_condition_failed",
            )
        return AgentOrchestrationResult(
            orchestration_id=request.orchestration_id,
            state=state,
            plan_sha256=request.plan.content_sha256,
            receipts=checkpoint.receipts,
            outputs=checkpoint.outputs,
            aggregate_output=aggregate,
            latest_checkpoint_id=checkpoint.checkpoint_id,
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
        if (
            request.plan.maximum_attempts + plan_budget.max_tool_calls
            > parent.step_limit
        ):
            raise AgentOrchestrationError(
                "subagent_budget_denied",
                "计划最大尝试次数与子工具调用合计超过父 Run 的 subagent step 预算",
                next_actions=("减少任务或工具调用预算后重新批准计划",),
                stop_condition="没有足够父级 step 预算时禁止 spawn",
            )

    async def _execute_batch(
        self,
        request: AgentOrchestrationRequest,
        *,
        barrier: AgentTaskBarrier,
        task_states: Mapping[str, AgentTaskState],
        outputs: Mapping[str, AgentTaskOutput],
        receipts: Mapping[str, AgentTaskExecutionReceipt],
        usage: _UsageTotals,
        deadline: float,
        cancellation: AgentOrchestrationCancellation,
    ) -> list[_TaskSeries]:
        pending: list[asyncio.Task[_TaskSeries]] = []
        immediate: list[_TaskSeries] = []
        for task_id in barrier.task_ids:
            task = request.plan.task_by_id[task_id]
            if any(
                task_states[dependency] is not AgentTaskState.SUCCEEDED
                for dependency in task.dependencies
            ):
                immediate.append(_TaskSeries((self._terminal_outcome(
                    task,
                    state=AgentTaskState.BLOCKED,
                    error_code="dependency_not_succeeded",
                ),)))
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
    ) -> _TaskSeries:
        outcomes: list[_TaskOutcome] = []
        for attempt_no in range(1, task.retry_policy.max_attempts + 1):
            outcome = await self._execute_task_attempt(
                request,
                task=task,
                outputs=outputs,
                receipts=receipts,
                previous_attempts=tuple(
                    item.receipt for item in outcomes
                ),
                attempt_no=attempt_no,
                usage=usage,
                deadline=deadline,
                cancellation=cancellation,
            )
            outcomes.append(outcome)
            receipt = outcome.receipt
            if receipt.state is AgentTaskState.SUCCEEDED:
                break
            if not task.retry_policy.permits(
                receipt.error_code,
                attempt_no,
            ):
                break
            delay = task.retry_policy.delay_seconds(attempt_no)
            if cancellation.requested or self._monotonic() + delay >= deadline:
                break
            if delay > 0:
                try:
                    await asyncio.wait_for(cancellation.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                if cancellation.requested:
                    break
        return _TaskSeries(tuple(outcomes))

    async def _execute_task_attempt(
        self,
        request: AgentOrchestrationRequest,
        *,
        task: AgentTaskDefinition,
        outputs: Mapping[str, AgentTaskOutput],
        receipts: Mapping[str, AgentTaskExecutionReceipt],
        previous_attempts: tuple[AgentTaskExecutionReceipt, ...],
        attempt_no: int,
        usage: _UsageTotals,
        deadline: float,
        cancellation: AgentOrchestrationCancellation,
    ) -> _TaskOutcome:
        started_at = self._now()
        started_clock = self._monotonic()
        reservation = None
        try:
            reservation = self._budget.reserve_subagent(
                f"{task.task_id}:attempt:{attempt_no}"
            )
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
                attempt_no=attempt_no,
                previous_attempts=previous_attempts,
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
            if output.status is AgentTaskOutputStatus.ERROR:
                task.output_contract.validate_partial(
                    output.data,
                    name="task error output data",
                    extra_keys=("error_code",),
                )
            else:
                task.output_contract.validate(output.data, name="task output data")
            self._record_usage(request, usage, output)
            if not task.completion.matches(output):
                error_code = (
                    self._reported_task_error_code(output)
                    if output.status is AgentTaskOutputStatus.ERROR
                    else "completion_condition_failed"
                )
                return self._outcome(
                    task,
                    state=AgentTaskState.FAILED,
                    output=output,
                    error_code=error_code,
                    started_at=started_at,
                    started_clock=started_clock,
                    reservation_id=reservation.reservation_id,
                    attempt_no=attempt_no,
                )
            return self._outcome(
                task,
                state=AgentTaskState.SUCCEEDED,
                output=output,
                error_code="",
                started_at=started_at,
                started_clock=started_clock,
                reservation_id=reservation.reservation_id,
                attempt_no=attempt_no,
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
                attempt_no=attempt_no,
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
                attempt_no=attempt_no,
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
                attempt_no=attempt_no,
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
                attempt_no=attempt_no,
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
        projected = _UsageTotals(
            input_tokens=usage.input_tokens + output.usage.input_tokens,
            output_tokens=usage.output_tokens + output.usage.output_tokens,
            cached_input_tokens=(
                usage.cached_input_tokens
                + output.usage.cached_input_tokens
            ),
            reasoning_tokens=(
                usage.reasoning_tokens + output.usage.reasoning_tokens
            ),
            model_calls=usage.model_calls + output.model_calls,
            tool_calls=usage.tool_calls + output.tool_calls,
            cost_microunits=(
                usage.cost_microunits + output.usage.cost_microunits
            ),
            task_attempts=usage.task_attempts,
            output_bytes=usage.output_bytes + output.size_bytes,
        )
        # Worker 已经产生这些物理消费；即使随后触发父预算或计划预算拒绝，
        # 屏障 checkpoint 也必须保留实际累计值，不能回退为“未消费”。
        usage.model_calls = projected.model_calls
        usage.tool_calls = projected.tool_calls
        usage.input_tokens = projected.input_tokens
        usage.output_tokens = projected.output_tokens
        usage.cached_input_tokens = projected.cached_input_tokens
        usage.reasoning_tokens = projected.reasoning_tokens
        usage.cost_microunits = projected.cost_microunits
        usage.output_bytes = projected.output_bytes
        self._budget.record_subagent_usage(
            output.usage,
            model_calls=output.model_calls,
            tool_calls=output.tool_calls,
        )
        budget = request.plan.budget
        exceeded = next((
            name
            for name, value, limit in (
                ("max_model_calls", projected.model_calls, budget.max_model_calls),
                ("max_tool_calls", projected.tool_calls, budget.max_tool_calls),
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
        attempt_no: int,
    ) -> _TaskOutcome:
        finished_at = self._now()
        receipt = AgentTaskExecutionReceipt(
            task_id=task.task_id,
            role_id=task.role_id,
            state=state,
            attempt_no=attempt_no,
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
    def _reported_task_error_code(output: AgentTaskOutput) -> str:
        value = output.data.get("error_code")
        normalized = str(value or "").strip()
        if (
            not normalized
            or len(normalized) > 128
            or any(character.isspace() for character in normalized)
        ):
            return "task_reported_error"
        return normalized

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
        barrier: AgentTaskBarrier,
        task_states: Mapping[str, AgentTaskState],
        outputs: Mapping[str, AgentTaskOutput],
        receipts: list[AgentTaskExecutionReceipt],
        usage: _UsageTotals,
        parent_checkpoint_id: str,
    ) -> AgentOrchestrationCheckpoint:
        sequence = barrier.sequence
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
            plan_id=request.plan.plan_id,
            plan_revision=request.plan.revision,
            plan_sha256=request.plan.content_sha256,
            freeze_id=request.freeze.freeze_id,
            sequence=sequence,
            parent_checkpoint_id=parent_checkpoint_id,
            barrier_id=barrier.barrier_id,
            task_states=task_states,
            outputs=outputs,
            receipts=tuple(receipts),
            cumulative_usage=usage.snapshot(),
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
