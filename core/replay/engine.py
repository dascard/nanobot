"""冻结 Event、模型和工具结果驱动的确定性语义回放引擎。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.agent_runtime.contracts import RuntimeToolCallStatus
from core.agent_runtime.recovery import (
    RuntimeSideEffectState,
    RuntimeToolEffectClass,
)
from core.replay.contracts import (
    FrozenModelResponse,
    FrozenReplayFixture,
    FrozenToolOutcome,
    REPLAY_MODE,
    REPLAY_SCHEMA_VERSION,
    REQUIRED_FAULT_KINDS,
    ReplayContractError,
    ReplayFault,
    ReplayFaultKind,
    ReplayScript,
    ReplayStatus,
    ReplayUsage,
    ReplayVariant,
    initial_replay_state,
    model_request_sha256,
    sha256_json,
)


class ReplaySafetyError(ReplayContractError):
    """回放可能触发或误判副作用时的 fail-closed 错误。"""


@dataclass(frozen=True, slots=True)
class ReplayTraceEvent:
    event_id: str
    sequence: int
    kind: str
    status: str
    reference_id: str
    payload_sha256: str
    code: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "kind": self.kind,
            "status": self.status,
            "reference_id": self.reference_id,
            "payload_sha256": self.payload_sha256,
            "code": self.code,
        }


class _TraceRecorder:
    def __init__(self, fixture_id: str, variant_sha256: str) -> None:
        self._seed = sha256_json({
            "fixture_id": fixture_id,
            "variant_sha256": variant_sha256,
            "mode": REPLAY_MODE,
        })
        self._events: list[ReplayTraceEvent] = []

    @property
    def events(self) -> tuple[ReplayTraceEvent, ...]:
        return tuple(self._events)

    def append(
        self,
        kind: str,
        status: str,
        *,
        reference_id: str,
        payload_sha256: str = "",
        code: str = "",
    ) -> None:
        sequence = len(self._events) + 1
        normalized_payload = payload_sha256 or sha256_json({
            "kind": kind,
            "status": status,
            "reference_id": reference_id,
            "code": code,
        })
        event_content = {
            "seed": self._seed,
            "sequence": sequence,
            "kind": kind,
            "status": status,
            "reference_id": reference_id,
            "payload_sha256": normalized_payload,
            "code": code,
        }
        self._events.append(ReplayTraceEvent(
            event_id=f"replay-{sha256_json(event_content)[:32]}",
            sequence=sequence,
            kind=kind,
            status=status,
            reference_id=reference_id,
            payload_sha256=normalized_payload,
            code=code,
        ))


class FrozenModelSubstitute:
    """只接受与冻结请求摘要精确匹配的模型替身。"""

    def __init__(self, responses: tuple[FrozenModelResponse, ...]) -> None:
        self._responses = responses
        self._index = 0
        self.calls: list[tuple[str, str]] = []

    @property
    def external_call_count(self) -> int:
        return 0

    def invoke(self, step_id: str, request_sha256: str) -> FrozenModelResponse:
        if self._index >= len(self._responses):
            raise ReplayContractError("冻结模型响应已耗尽")
        response = self._responses[self._index]
        if response.step_id != step_id:
            raise ReplayContractError(
                "冻结模型响应 step_id 与回放顺序不匹配: "
                f"expected={response.step_id}, actual={step_id}"
            )
        if response.request_sha256 != request_sha256:
            raise ReplayContractError(
                "冻结模型响应绑定的请求摘要不匹配: "
                f"step_id={step_id}"
            )
        self._index += 1
        self.calls.append((step_id, request_sha256))
        return response


class FrozenToolSubstitute:
    """复用冻结结果或回执，永不执行真实工具和副作用。"""

    def __init__(self) -> None:
        self.result_calls: list[str] = []
        self.reused_receipt_ids: list[str] = []
        self.side_effect_execution_count = 0

    @property
    def external_call_count(self) -> int:
        return 0

    def consume(self, outcome: FrozenToolOutcome) -> FrozenToolOutcome:
        self.result_calls.append(outcome.tool_call_id)
        if outcome.effect_class is RuntimeToolEffectClass.READ_ONLY:
            return outcome
        if not outcome.receipt_id or outcome.receipt_state is None:
            raise ReplaySafetyError(
                "unsafe_side_effect_missing_receipt: "
                f"{outcome.tool_call_id}"
            )
        if not outcome.receipt_state.terminal:
            raise ReplaySafetyError(
                "unsafe_side_effect_non_terminal_receipt: "
                f"{outcome.tool_call_id}"
            )
        expected_receipt_state = {
            RuntimeToolCallStatus.COMPLETED: RuntimeSideEffectState.COMPLETED,
            RuntimeToolCallStatus.FAILED: RuntimeSideEffectState.FAILED,
            RuntimeToolCallStatus.AMBIGUOUS: RuntimeSideEffectState.AMBIGUOUS,
        }.get(outcome.status)
        if (
            expected_receipt_state is not None
            and outcome.receipt_state is not expected_receipt_state
        ):
            raise ReplaySafetyError(
                "unsafe_side_effect_receipt_state_mismatch: "
                f"{outcome.tool_call_id}"
            )
        self.reused_receipt_ids.append(outcome.receipt_id)
        return outcome


@dataclass(frozen=True, slots=True)
class ReplayRunReport:
    fixture_id: str
    source_run_id: str
    fixture_sha256: str
    script_sha256: str
    variant: ReplayVariant
    status: ReplayStatus
    failure_code: str
    fault: ReplayFault | None
    fault_injected: bool
    events: tuple[ReplayTraceEvent, ...]
    usage: ReplayUsage
    output_sha256: str
    state_sha256: str
    model_call_count: int
    model_external_call_count: int
    tool_result_count: int
    tool_external_call_count: int
    side_effect_execution_count: int
    duplicate_side_effect_execution_count: int
    reused_receipt_ids: tuple[str, ...]
    recovery_count: int
    checkpoint_retry_count: int

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema_version": REPLAY_SCHEMA_VERSION,
            "replay_mode": REPLAY_MODE,
            "offline": True,
            "wire_exact": False,
            "fixture_id": self.fixture_id,
            "source_run_id": self.source_run_id,
            "fixture_sha256": self.fixture_sha256,
            "script_sha256": self.script_sha256,
            "variant": {
                **self.variant.to_dict(),
                "fingerprint": self.variant.fingerprint,
            },
            "status": self.status.value,
            "failure_code": self.failure_code,
            "fault": self.fault.to_dict() if self.fault else None,
            "fault_injected": self.fault_injected,
            "events": [item.to_dict() for item in self.events],
            "trace_sha256": sha256_json([
                item.to_dict() for item in self.events
            ]),
            "usage": self.usage.to_dict(),
            "output_sha256": self.output_sha256,
            "state_sha256": self.state_sha256,
            "substitutes": {
                "model": "frozen",
                "tool": "frozen",
                "model_external_call_count": self.model_external_call_count,
                "tool_external_call_count": self.tool_external_call_count,
            },
            "counts": {
                "model_call_count": self.model_call_count,
                "tool_result_count": self.tool_result_count,
                "side_effect_execution_count": (
                    self.side_effect_execution_count
                ),
                "duplicate_side_effect_execution_count": (
                    self.duplicate_side_effect_execution_count
                ),
                "recovery_count": self.recovery_count,
                "checkpoint_retry_count": self.checkpoint_retry_count,
            },
            "reused_receipt_ids": list(self.reused_receipt_ids),
        }

    def to_dict(self) -> dict[str, object]:
        content = self._content_dict()
        return {**content, "report_sha256": sha256_json(content)}


@dataclass(frozen=True, slots=True)
class ReplayComparisonReport:
    baseline: ReplayRunReport
    candidate: ReplayRunReport

    def _variant_diff(self) -> list[dict[str, object]]:
        return [
            {
                "dimension": name,
                "changed": (
                    getattr(self.baseline.variant, name)
                    != getattr(self.candidate.variant, name)
                ),
                "baseline": getattr(
                    self.baseline.variant,
                    name,
                ).to_dict(),
                "candidate": getattr(
                    self.candidate.variant,
                    name,
                ).to_dict(),
            }
            for name in ReplayVariant.DIMENSIONS
        ]

    def _content_dict(self) -> dict[str, object]:
        variant_diff = self._variant_diff()
        baseline = self.baseline.to_dict()
        candidate = self.candidate.to_dict()
        usage_delta = {
            name: (
                getattr(self.candidate.usage, name)
                - getattr(self.baseline.usage, name)
            )
            for name in (
                "input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "cost_microunits",
            )
        }
        return {
            "schema_version": REPLAY_SCHEMA_VERSION,
            "replay_mode": REPLAY_MODE,
            "offline": True,
            "wire_exact": False,
            "fixture_id": self.baseline.fixture_id,
            "baseline": baseline,
            "candidate": candidate,
            "diff": {
                "variant_dimensions": variant_diff,
                "changed_dimensions": [
                    item["dimension"]
                    for item in variant_diff
                    if item["changed"]
                ],
                "status_changed": (
                    self.baseline.status is not self.candidate.status
                ),
                "output_changed": (
                    self.baseline.output_sha256
                    != self.candidate.output_sha256
                ),
                "state_changed": (
                    self.baseline.state_sha256
                    != self.candidate.state_sha256
                ),
                "trace_changed": (
                    baseline["trace_sha256"]
                    != candidate["trace_sha256"]
                ),
                "usage_delta": usage_delta,
            },
            "quality_judgement": None,
            "requires_quality_evaluation": (
                self.baseline.status is not self.candidate.status
                or self.baseline.output_sha256
                != self.candidate.output_sha256
            ),
        }

    def to_dict(self) -> dict[str, object]:
        content = self._content_dict()
        return {**content, "report_sha256": sha256_json(content)}


def _terminal_code(outcome: FrozenToolOutcome) -> str:
    if outcome.status is RuntimeToolCallStatus.COMPLETED:
        return ""
    if outcome.status is RuntimeToolCallStatus.AMBIGUOUS:
        return "side_effect_ambiguous"
    return f"frozen_tool_{outcome.status.value}"


def run_replay(
    fixture: FrozenReplayFixture,
    script: ReplayScript,
    *,
    fault: ReplayFault | None = None,
    checkpoint_retry_limit: int = 2,
) -> ReplayRunReport:
    """执行一次无网络、无真实工具调用的确定性语义回放。"""

    if type(checkpoint_retry_limit) is not int or checkpoint_retry_limit < 0:
        raise ReplayContractError("checkpoint_retry_limit 必须是非负整数")
    model = FrozenModelSubstitute(script.model_responses)
    tools = FrozenToolSubstitute()
    trace = _TraceRecorder(fixture.fixture_id, script.variant.fingerprint)
    state = list(initial_replay_state(fixture))
    usage = ReplayUsage()
    status = ReplayStatus.SUCCEEDED
    failure_code = ""
    output_sha256 = ""
    fault_injected = False
    recovery_count = 0
    checkpoint_retry_count = 0

    trace.append(
        "replay.started",
        "running",
        reference_id=fixture.fixture_id,
        payload_sha256=fixture.fingerprint,
    )
    for event in fixture.events:
        trace.append(
            "source_event.replayed",
            event.status,
            reference_id=event.event_id,
            payload_sha256=event.payload_sha256,
            code=event.kind,
        )

    def fail(target_status: ReplayStatus, code: str, reference_id: str) -> None:
        nonlocal status, failure_code
        status = target_status
        failure_code = code
        trace.append(
            "replay.fault",
            target_status.value,
            reference_id=reference_id,
            code=code,
        )

    def fault_targets(kind: ReplayFaultKind, reference_id: str) -> bool:
        return (
            fault is not None
            and fault.kind is kind
            and (not fault.target_id or fault.target_id == reference_id)
        )

    def checkpoint(reference_id: str) -> bool:
        nonlocal fault_injected, recovery_count, checkpoint_retry_count
        if not fault_targets(ReplayFaultKind.DB_LOCKED, reference_id):
            trace.append(
                "checkpoint.saved",
                "completed",
                reference_id=reference_id,
                payload_sha256=sha256_json(state),
            )
            return True
        fault_injected = True
        attempts = fault.repeat_count if fault else 1
        for attempt in range(1, attempts + 1):
            checkpoint_retry_count += 1
            trace.append(
                "checkpoint.retry",
                "failed",
                reference_id=reference_id,
                code=f"db_locked:{attempt}",
            )
            if attempt > checkpoint_retry_limit:
                fail(ReplayStatus.FAILED, "db_locked", reference_id)
                return False
        recovery_count += 1
        trace.append(
            "checkpoint.saved",
            "recovered",
            reference_id=reference_id,
            payload_sha256=sha256_json(state),
            code="db_lock_recovered",
        )
        return True

    if fault_targets(ReplayFaultKind.DB_LOCKED, "replay-start"):
        if not checkpoint("replay-start"):
            return _finish_report(
                fixture,
                script,
                trace,
                model,
                tools,
                status,
                failure_code,
                fault,
                fault_injected,
                usage,
                output_sha256,
                state,
                recovery_count,
                checkpoint_retry_count,
            )

    for frozen_response in script.model_responses:
        step_id = frozen_response.step_id
        if fault_targets(ReplayFaultKind.LEASE_LOST, step_id):
            fault_injected = True
            fail(ReplayStatus.CANCELLED, "lease_lost", step_id)
            break
        if fault_targets(ReplayFaultKind.MODEL_TIMEOUT, step_id):
            fault_injected = True
            fail(ReplayStatus.TIMED_OUT, "model_timeout", step_id)
            break

        request_sha256 = model_request_sha256(
            fixture,
            script.variant,
            step_id=step_id,
            state_sha256s=state,
        )
        trace.append(
            "model.requested",
            "running",
            reference_id=step_id,
            payload_sha256=request_sha256,
        )
        response = model.invoke(step_id, request_sha256)
        usage = usage + response.usage

        interrupted = False
        for chunk_index, chunk_sha256 in enumerate(
            response.stream_chunk_sha256s,
            start=1,
        ):
            trace.append(
                "model.stream_chunk",
                "running",
                reference_id=step_id,
                payload_sha256=chunk_sha256,
            )
            if (
                fault_targets(ReplayFaultKind.STREAM_INTERRUPTED, step_id)
                and chunk_index >= max(1, fault.after_count if fault else 1)
            ):
                fault_injected = True
                fail(
                    ReplayStatus.FAILED,
                    "stream_interrupted",
                    step_id,
                )
                interrupted = True
                break
        if interrupted:
            break

        state.append(response.response_sha256)
        output_sha256 = response.response_sha256
        trace.append(
            "model.completed",
            "completed",
            reference_id=step_id,
            payload_sha256=response.response_sha256,
        )

        for outcome in response.tool_outcomes:
            if fault_targets(ReplayFaultKind.TOOL_FAILURE, outcome.tool_call_id):
                fault_injected = True
                fail(
                    ReplayStatus.FAILED,
                    "tool_failure",
                    outcome.tool_call_id,
                )
                break

            if fault_targets(
                ReplayFaultKind.SANDBOX_RESTARTED,
                outcome.tool_call_id,
            ):
                fault_injected = True
                recovery_count += 1
                trace.append(
                    "sandbox.restarted",
                    "recovering",
                    reference_id=outcome.tool_call_id,
                    code="checkpoint_restore_required",
                )
                trace.append(
                    "checkpoint.restored",
                    "recovered",
                    reference_id=outcome.tool_call_id,
                    payload_sha256=sha256_json(state),
                )

            try:
                consumed = tools.consume(outcome)
            except ReplaySafetyError as exc:
                fail(
                    ReplayStatus.FAILED,
                    str(exc).split(":", 1)[0],
                    outcome.tool_call_id,
                )
                break
            state.append(consumed.result_sha256)
            trace.append(
                "tool.frozen_result",
                consumed.status.value,
                reference_id=consumed.tool_call_id,
                payload_sha256=consumed.result_sha256,
                code=(
                    "receipt_reused"
                    if consumed.effect_class.requires_receipt
                    else "read_only_result"
                ),
            )
            terminal_code = _terminal_code(consumed)
            if terminal_code:
                fail(
                    ReplayStatus.FAILED,
                    terminal_code,
                    consumed.tool_call_id,
                )
                break
            if not checkpoint(consumed.tool_call_id):
                break
        if status is not ReplayStatus.SUCCEEDED:
            break

    if fault is not None and not fault_injected and status is ReplayStatus.SUCCEEDED:
        fail(
            ReplayStatus.FAILED,
            "fault_target_not_found",
            fault.target_id or fault.kind.value,
        )

    return _finish_report(
        fixture,
        script,
        trace,
        model,
        tools,
        status,
        failure_code,
        fault,
        fault_injected,
        usage,
        output_sha256,
        state,
        recovery_count,
        checkpoint_retry_count,
    )


def _finish_report(
    fixture: FrozenReplayFixture,
    script: ReplayScript,
    trace: _TraceRecorder,
    model: FrozenModelSubstitute,
    tools: FrozenToolSubstitute,
    status: ReplayStatus,
    failure_code: str,
    fault: ReplayFault | None,
    fault_injected: bool,
    usage: ReplayUsage,
    output_sha256: str,
    state: list[str],
    recovery_count: int,
    checkpoint_retry_count: int,
) -> ReplayRunReport:
    trace.append(
        "replay.ended",
        status.value,
        reference_id=fixture.fixture_id,
        payload_sha256=sha256_json(state),
        code=failure_code,
    )
    reused_receipts = tuple(dict.fromkeys(tools.reused_receipt_ids))
    return ReplayRunReport(
        fixture_id=fixture.fixture_id,
        source_run_id=fixture.source_run_id,
        fixture_sha256=fixture.fingerprint,
        script_sha256=script.fingerprint,
        variant=script.variant,
        status=status,
        failure_code=failure_code,
        fault=fault,
        fault_injected=fault_injected,
        events=trace.events,
        usage=usage,
        output_sha256=output_sha256,
        state_sha256=sha256_json(state),
        model_call_count=len(model.calls),
        model_external_call_count=model.external_call_count,
        tool_result_count=len(tools.result_calls),
        tool_external_call_count=tools.external_call_count,
        side_effect_execution_count=tools.side_effect_execution_count,
        duplicate_side_effect_execution_count=0,
        reused_receipt_ids=reused_receipts,
        recovery_count=recovery_count,
        checkpoint_retry_count=checkpoint_retry_count,
    )


def compare_replays(
    fixture: FrozenReplayFixture,
    baseline: ReplayScript,
    candidate: ReplayScript,
) -> ReplayComparisonReport:
    """对同一冻结 Event 执行两组策略脚本并输出事实差异。"""

    return ReplayComparisonReport(
        baseline=run_replay(fixture, baseline),
        candidate=run_replay(fixture, candidate),
    )


def default_faults(script: ReplayScript) -> tuple[ReplayFault, ...]:
    """为具备流式响应和副作用回执的脚本生成标准六故障矩阵。"""

    first_response = script.model_responses[0]
    streamed = next(
        (
            response
            for response in script.model_responses
            if response.stream_chunk_sha256s
        ),
        None,
    )
    first_tool = next(
        (
            outcome
            for response in script.model_responses
            for outcome in response.tool_outcomes
        ),
        None,
    )
    effectful = next(
        (
            outcome
            for response in script.model_responses
            for outcome in response.tool_outcomes
            if outcome.effect_class.requires_receipt
        ),
        None,
    )
    if streamed is None:
        raise ReplayContractError("标准故障矩阵需要至少一个流式模型响应")
    if first_tool is None:
        raise ReplayContractError("标准故障矩阵需要至少一个冻结工具结果")
    if effectful is None:
        raise ReplayContractError("标准故障矩阵需要至少一个带回执的副作用工具")
    return (
        ReplayFault(ReplayFaultKind.MODEL_TIMEOUT, first_response.step_id),
        ReplayFault(
            ReplayFaultKind.STREAM_INTERRUPTED,
            streamed.step_id,
            after_count=1,
        ),
        ReplayFault(ReplayFaultKind.TOOL_FAILURE, first_tool.tool_call_id),
        ReplayFault(ReplayFaultKind.DB_LOCKED, effectful.tool_call_id),
        ReplayFault(ReplayFaultKind.LEASE_LOST, first_response.step_id),
        ReplayFault(
            ReplayFaultKind.SANDBOX_RESTARTED,
            effectful.tool_call_id,
        ),
    )


def _fault_acceptance(report: ReplayRunReport) -> tuple[bool, list[str]]:
    fault = report.fault
    if fault is None:
        return False, ["missing_fault"]
    errors: list[str] = []
    expected = {
        ReplayFaultKind.MODEL_TIMEOUT: (
            ReplayStatus.TIMED_OUT,
            "model_timeout",
        ),
        ReplayFaultKind.STREAM_INTERRUPTED: (
            ReplayStatus.FAILED,
            "stream_interrupted",
        ),
        ReplayFaultKind.TOOL_FAILURE: (
            ReplayStatus.FAILED,
            "tool_failure",
        ),
        ReplayFaultKind.LEASE_LOST: (
            ReplayStatus.CANCELLED,
            "lease_lost",
        ),
    }
    if fault.kind in expected:
        expected_status, expected_code = expected[fault.kind]
        if report.status is not expected_status:
            errors.append(f"unexpected_status:{report.status.value}")
        if report.failure_code != expected_code:
            errors.append(f"unexpected_code:{report.failure_code}")
    elif fault.kind in {
        ReplayFaultKind.DB_LOCKED,
        ReplayFaultKind.SANDBOX_RESTARTED,
    }:
        if report.status is not ReplayStatus.SUCCEEDED:
            errors.append(f"recovery_failed:{report.failure_code}")
        if report.recovery_count < 1:
            errors.append("recovery_not_observed")
        if not report.reused_receipt_ids:
            errors.append("side_effect_receipt_not_reused")
    if not report.fault_injected:
        errors.append("fault_not_injected")
    if report.side_effect_execution_count != 0:
        errors.append("side_effect_executed")
    if report.duplicate_side_effect_execution_count != 0:
        errors.append("duplicate_side_effect_executed")
    return not errors, errors


def run_fault_matrix(
    fixture: FrozenReplayFixture,
    script: ReplayScript,
    *,
    faults: tuple[ReplayFault, ...] | None = None,
) -> dict[str, object]:
    """分别运行故障，避免前一个终态掩盖后续故障覆盖。"""

    selected = faults if faults is not None else default_faults(script)
    if not selected:
        raise ReplayContractError("faults 不能为空")
    if len({item.kind for item in selected}) != len(selected):
        raise ReplayContractError("faults 中同一种故障只能出现一次")

    results: list[dict[str, object]] = []
    passed = 0
    for injected_fault in selected:
        report = run_replay(fixture, script, fault=injected_fault)
        accepted, errors = _fault_acceptance(report)
        if accepted:
            passed += 1
        results.append({
            "fault_kind": injected_fault.kind.value,
            "passed": accepted,
            "errors": errors,
            "report": report.to_dict(),
        })

    covered = {item.kind for item in selected}
    missing = [
        item.value for item in REQUIRED_FAULT_KINDS if item not in covered
    ]
    content: dict[str, Any] = {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "replay_mode": REPLAY_MODE,
        "offline": True,
        "wire_exact": False,
        "fixture_id": fixture.fixture_id,
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "required_fault_kinds": [
            item.value for item in REQUIRED_FAULT_KINDS
        ],
        "covered_fault_kinds": sorted(item.value for item in covered),
        "missing_fault_kinds": missing,
        "complete_coverage": not missing,
        "duplicate_side_effect_execution_count": sum(
            int(item["report"]["counts"][
                "duplicate_side_effect_execution_count"
            ])
            for item in results
        ),
        "results": results,
    }
    return {**content, "report_sha256": sha256_json(content)}


__all__ = [
    "FrozenModelSubstitute",
    "FrozenToolSubstitute",
    "ReplayComparisonReport",
    "ReplayRunReport",
    "ReplaySafetyError",
    "ReplayTraceEvent",
    "compare_replays",
    "default_faults",
    "run_fault_matrix",
    "run_replay",
]
