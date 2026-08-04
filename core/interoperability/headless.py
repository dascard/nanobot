"""基于 Nanobot Run/Event 合同的真实 Headless 执行 Adapter。

Headless 入口不复制 Runtime 状态，也不创建自己的事件仓库。权威事件 handler
始终先执行，Adapter 随后只保留本次调用的不可变事件引用与脱敏摘要，供评测或
CLI 调用方消费。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import inspect
import json
from typing import Any

from core.agent_runtime.contracts import (
    AgentRuntimePort,
    AgentTurnRequest,
    AgentTurnResult,
    RuntimeCapability,
    RuntimeLifecycleState,
    RuntimeRunEvent,
    RuntimeRunEventHandler,
    RuntimeRunEventKind,
    RuntimeRunIdentity,
    RuntimeRunStatus,
    RuntimeUsage,
)
from core.interoperability.contracts import (
    InteroperabilityError,
    require_interoperability_enabled,
)
from core.lifecycle import FeatureEnablementDecision


HEADLESS_FEATURE_ID = "interoperability.headless"


class HeadlessExecutionError(InteroperabilityError):
    """Headless 运行合同失败；不包装模型或工具正文。"""


@dataclass(frozen=True, slots=True)
class HeadlessLimits:
    max_events: int = 10_000
    max_text_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        for name in ("max_events", "max_text_bytes"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"Headless {name} 必须是正整数")


@dataclass(frozen=True, slots=True)
class HeadlessRunEvidence:
    """可导出的脱敏证据；正文、工具参数、结果和 owner/actor 均不在其中。"""

    run_id: str
    turn_id: str
    terminal_status: RuntimeRunStatus
    event_count: int
    event_sha256: str
    text_sha256: str
    text_bytes: int
    artifact_ids: tuple[str, ...]
    usage: RuntimeUsage | None


@dataclass(frozen=True, slots=True)
class HeadlessRunResult:
    """进程内执行结果；原始 Result/Event 仍是 Nanobot 合同对象。"""

    result: AgentTurnResult
    events: tuple[RuntimeRunEvent, ...]
    evidence: HeadlessRunEvidence


def _event_projection(event: RuntimeRunEvent) -> dict[str, object]:
    payload: dict[str, object] = {
        "event_id": event.event_id,
        "sequence": event.sequence,
        "kind": event.kind.value,
        "status": event.status.value,
        "occurred_at": event.occurred_at.isoformat(),
        "attribute_keys": sorted(attribute.key for attribute in event.attributes),
    }
    if event.kind is RuntimeRunEventKind.TEXT_DELTA:
        encoded = event.text_delta.encode("utf-8")
        payload["text_bytes"] = len(encoded)
        payload["text_sha256"] = hashlib.sha256(encoded).hexdigest()
    elif (
        event.kind is RuntimeRunEventKind.TOOL_ACTIVITY and event.tool_call is not None
    ):
        payload["tool_call"] = {
            "call_id": event.tool_call.call_id,
            "name": event.tool_call.name,
            "status": event.tool_call.status.value,
        }
    elif event.kind is RuntimeRunEventKind.USAGE and event.usage is not None:
        payload["usage"] = {
            "input_tokens": event.usage.input_tokens,
            "output_tokens": event.usage.output_tokens,
            "cached_input_tokens": event.usage.cached_input_tokens,
            "reasoning_tokens": event.usage.reasoning_tokens,
            "cost_microunits": event.usage.cost_microunits,
        }
    elif event.kind is RuntimeRunEventKind.ARTIFACT and event.artifact is not None:
        payload["artifact"] = {
            "artifact_id": event.artifact.artifact_id,
            "sha256": event.artifact.sha256,
            "media_type": event.artifact.media_type,
            "size_bytes": event.artifact.size_bytes,
        }
    elif event.kind is RuntimeRunEventKind.ERROR and event.error is not None:
        payload["error"] = {
            "code": event.error.code,
            "retryable": event.error.retryable,
        }
    elif (
        event.kind is RuntimeRunEventKind.CONTEXT_DECISION
        and event.context_decision is not None
    ):
        payload["context_decision"] = {
            "decision_id": event.context_decision.decision_id,
            "action": event.context_decision.action,
            "cause_code": event.context_decision.cause_code,
            "decision_sha256": event.context_decision.decision_sha256,
        }
    return payload


class HeadlessRuntimeAdapter:
    """直接调用一个已运行 ``AgentRuntimePort`` 的有界 Headless 入口。"""

    def __init__(
        self,
        *,
        enablement: FeatureEnablementDecision,
        runtime: AgentRuntimePort,
        authoritative_event_handler: RuntimeRunEventHandler,
        limits: HeadlessLimits = HeadlessLimits(),
    ) -> None:
        require_interoperability_enabled(
            enablement,
            feature_id=HEADLESS_FEATURE_ID,
        )
        if not isinstance(runtime, AgentRuntimePort):
            raise TypeError("runtime 必须是 AgentRuntimePort")
        if not callable(authoritative_event_handler):
            raise TypeError("authoritative_event_handler 必须可调用")
        if not isinstance(limits, HeadlessLimits):
            raise TypeError("limits 必须是 HeadlessLimits")
        self._runtime = runtime
        self._authoritative_handler = authoritative_event_handler
        self._limits = limits
        self._active_task: asyncio.Task[Any] | None = None
        self._active_identity: RuntimeRunIdentity | None = None
        self._lock = asyncio.Lock()

    @property
    def active_run_id(self) -> str:
        identity = self._active_identity
        return identity.run_id if identity is not None else ""

    async def execute(self, request: AgentTurnRequest) -> HeadlessRunResult:
        if not isinstance(request, AgentTurnRequest):
            raise TypeError("request 必须是 AgentTurnRequest")
        if self._runtime.state is not RuntimeLifecycleState.RUNNING:
            raise HeadlessExecutionError(
                "RUNTIME_NOT_RUNNING",
                "Headless Runtime 尚未进入 running",
            )
        if not self._runtime.runtime_capabilities.supports(RuntimeCapability.RUN_EVENT):
            raise HeadlessExecutionError(
                "RUNTIME_CAPABILITY_MISSING",
                "Headless Runtime 不支持事件化执行",
            )
        identity = request.context.execution_identity()
        current_task = asyncio.current_task()
        if current_task is None:
            raise HeadlessExecutionError(
                "ASYNC_CONTEXT_MISSING",
                "Headless 执行必须运行在异步任务中",
            )
        async with self._lock:
            if self._active_task is not None:
                raise HeadlessExecutionError(
                    "RUNTIME_BUSY",
                    "同一 Headless Runtime 不能并发执行多个 Run",
                )
            self._active_task = current_task
            self._active_identity = identity

        events: list[RuntimeRunEvent] = []
        event_hasher = hashlib.sha256()
        text_hasher = hashlib.sha256()
        text_bytes = 0
        expected_sequence = 1
        terminal_status: RuntimeRunStatus | None = None
        artifacts: list[str] = []
        latest_usage: RuntimeUsage | None = None

        async def handle(event: RuntimeRunEvent) -> None:
            nonlocal expected_sequence, text_bytes, terminal_status, latest_usage
            handled = self._authoritative_handler(event)
            if inspect.isawaitable(handled):
                await handled
            if not isinstance(event, RuntimeRunEvent):
                raise HeadlessExecutionError(
                    "EVENT_CONTRACT_INVALID",
                    "Headless Runtime 产生了无效事件",
                )
            if event.identity != identity:
                raise HeadlessExecutionError(
                    "EVENT_IDENTITY_MISMATCH",
                    "Headless Runtime 事件身份与请求不一致",
                )
            if event.sequence != expected_sequence:
                raise HeadlessExecutionError(
                    "EVENT_SEQUENCE_INVALID",
                    "Headless Runtime 事件序号不连续",
                )
            if len(events) >= self._limits.max_events:
                raise HeadlessExecutionError(
                    "EVENT_LIMIT",
                    "Headless Runtime 事件超过上限",
                )
            if terminal_status is not None:
                raise HeadlessExecutionError(
                    "EVENT_AFTER_TERMINAL",
                    "Headless Runtime 在终态后继续产生事件",
                )
            expected_sequence += 1
            if event.kind is RuntimeRunEventKind.TEXT_DELTA:
                encoded = event.text_delta.encode("utf-8")
                text_bytes += len(encoded)
                if text_bytes > self._limits.max_text_bytes:
                    raise HeadlessExecutionError(
                        "TEXT_LIMIT",
                        "Headless Runtime 文本输出超过上限",
                    )
                text_hasher.update(encoded)
            elif (
                event.kind is RuntimeRunEventKind.ARTIFACT
                and event.artifact is not None
            ):
                artifacts.append(event.artifact.artifact_id)
            elif event.kind is RuntimeRunEventKind.USAGE:
                latest_usage = event.usage
            elif event.kind is RuntimeRunEventKind.END:
                terminal_status = event.status
            projection = json.dumps(
                _event_projection(event),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            event_hasher.update(len(projection).to_bytes(8, "big"))
            event_hasher.update(projection)
            events.append(event)

        try:
            result = await self._runtime.run_event(request, handle)
        except asyncio.CancelledError:
            raise
        except HeadlessExecutionError:
            try:
                self._runtime.interrupt(reason="headless_contract_failure")
            except Exception:
                pass
            raise
        except Exception as exc:
            try:
                self._runtime.interrupt(reason="headless_execution_failure")
            except Exception:
                pass
            raise HeadlessExecutionError(
                "RUNTIME_EXECUTION_FAILED",
                "Headless Runtime 执行失败",
            ) from exc
        finally:
            async with self._lock:
                if self._active_task is current_task:
                    self._active_task = None
                    self._active_identity = None

        if not isinstance(result, AgentTurnResult):
            raise HeadlessExecutionError(
                "RESULT_CONTRACT_INVALID",
                "Headless Runtime 返回了无效结果",
            )
        if terminal_status is None or not terminal_status.is_terminal:
            raise HeadlessExecutionError(
                "TERMINAL_EVENT_MISSING",
                "Headless Runtime 未产生合法终态事件",
            )
        return HeadlessRunResult(
            result=result,
            events=tuple(events),
            evidence=HeadlessRunEvidence(
                run_id=identity.run_id,
                turn_id=identity.turn_id,
                terminal_status=terminal_status,
                event_count=len(events),
                event_sha256=event_hasher.hexdigest(),
                text_sha256=text_hasher.hexdigest(),
                text_bytes=text_bytes,
                artifact_ids=tuple(dict.fromkeys(artifacts)),
                usage=latest_usage,
            ),
        )

    async def cancel(self, run_id: str) -> bool:
        normalized = str(run_id or "").strip()
        async with self._lock:
            task = self._active_task
            identity = self._active_identity
            if task is None or identity is None or identity.run_id != normalized:
                return False
            try:
                self._runtime.interrupt(reason="headless_cancel")
            except Exception:
                pass
            task.cancel()
            return True


__all__ = [
    "HEADLESS_FEATURE_ID",
    "HeadlessExecutionError",
    "HeadlessLimits",
    "HeadlessRunEvidence",
    "HeadlessRunResult",
    "HeadlessRuntimeAdapter",
]
