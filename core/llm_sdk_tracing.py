"""OpenAI SDK 请求出口追踪。

用于不可直接修改第三方 provider 代码的场景：在主仓侧幂等包装
`chat.completions.create`，记录传给 SDK 的真实请求参数。
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Mapping
from typing import Any

from core.llm_stream_trace import LLMStreamTraceAccumulator

logger = logging.getLogger(__name__)


def _request_payload_from_sdk_kwargs(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    payload = dict(kwargs)
    extra_body = payload.pop("extra_body", None)
    if isinstance(extra_body, dict):
        payload.update(extra_body)
    if args:
        payload["_args"] = list(args)
    return payload


def _safe_sdk_response(result: Any) -> Any:
    if result is None:
        return {}
    if hasattr(result, "model_dump"):
        try:
            return result.model_dump()
        except Exception:
            pass
    if hasattr(result, "dict"):
        try:
            return result.dict()
        except Exception:
            pass
    if isinstance(result, (dict, list, str, int, float, bool)):
        return result
    return {"repr": repr(result)[:4000]}


def _tool_calls_from_container(container: Any) -> list[Any]:
    if not isinstance(container, Mapping):
        return []
    value = container.get("tool_calls")
    return list(value) if isinstance(value, list) else []


def _tool_call_names(payload: Any) -> tuple[bool, set[str]]:
    """提取响应中的工具调用名称，兼容非流式响应和聚合后的 chunk 样本。"""

    if not isinstance(payload, Mapping):
        return False, set()
    saw_tool_call = False
    names: set[str] = set()
    containers: list[Any] = [payload]
    for choice in payload.get("choices") or []:
        if not isinstance(choice, Mapping):
            continue
        containers.extend((choice, choice.get("message"), choice.get("delta")))
    for chunk in payload.get("chunks_sample") or []:
        if not isinstance(chunk, Mapping):
            continue
        containers.append(chunk)
        for choice in chunk.get("choices") or []:
            if not isinstance(choice, Mapping):
                continue
            containers.extend((choice, choice.get("message"), choice.get("delta")))
    for container in containers:
        for call in _tool_calls_from_container(container):
            saw_tool_call = True
            if not isinstance(call, Mapping):
                continue
            function = call.get("function")
            if isinstance(function, Mapping):
                name = str(function.get("name") or "").strip()
                if name:
                    names.add(name)
    return saw_tool_call, names


def _resolved_agent_phase(
    initial_phase: str,
    response: Any,
    *,
    streamed_tool_call_seen: bool | None = None,
    streamed_tool_names: set[str] | None = None,
) -> str:
    """把正常 Agent 轮次按真实模型输出收敛为工具轮次或最终动作。"""

    if initial_phase != "agent.tool_round":
        return initial_phase
    saw_tool_call, names = _tool_call_names(response)
    if streamed_tool_call_seen is not None:
        saw_tool_call = streamed_tool_call_seen
        names = set(streamed_tool_names or ())
    if not saw_tool_call:
        return "agent.final_action"
    if names and names <= {"reply", "no_reply"}:
        return "agent.final_action"
    return initial_phase


class _TracedStreamProxy:
    """延迟到流式响应消费完成后记录聚合响应。"""

    def __init__(
        self,
        stream: Any,
        *,
        log_id: int,
        started: float,
        phase: str,
    ) -> None:
        self._stream = stream
        self._log_id = log_id
        self._started = started
        self._phase = phase
        self._finished = False
        self._accumulator = LLMStreamTraceAccumulator(started=started)
        self._async_iter: Any = None
        self._sync_iter: Any = None
        self._saw_tool_call = False
        self._tool_names: set[str] = set()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    def _record_chunk(self, chunk: Any) -> None:
        payload = _safe_sdk_response(chunk)
        self._accumulator.record_chunk(payload)
        saw_tool_call, names = _tool_call_names(payload)
        self._saw_tool_call = self._saw_tool_call or saw_tool_call
        self._tool_names.update(names)

    def _finish(self, *, status: str, response_status: int = 200, error: str = "") -> None:
        if self._finished:
            return
        self._finished = True
        try:
            from core.tracing import LLMRequestTracer

            response = self._accumulator.build_response()
            resolved_phase = (
                _resolved_agent_phase(
                    self._phase,
                    response,
                    streamed_tool_call_seen=self._saw_tool_call,
                    streamed_tool_names=self._tool_names,
                )
                if status == "stream_success"
                else self._phase
            )
            LLMRequestTracer.finish_request(
                log_id=self._log_id,
                response=response,
                response_status=response_status,
                status=status,
                error=error,
                latency_ms=int((time.time() - self._started) * 1000),
                phase=resolved_phase,
            )
        except Exception as exc:
            logger.debug("OpenAI SDK stream finish tracing skipped: %s", exc)

    def __aiter__(self) -> "_TracedStreamProxy":
        self._async_iter = self._stream.__aiter__()
        return self

    async def __anext__(self) -> Any:
        if self._async_iter is None:
            self._async_iter = self._stream.__aiter__()
        try:
            chunk = await self._async_iter.__anext__()
        except StopAsyncIteration:
            self._finish(status="stream_success", response_status=200)
            raise
        except Exception as exc:
            self._finish(status="stream_error", response_status=0, error=str(exc))
            raise
        self._record_chunk(chunk)
        return chunk

    def __iter__(self) -> "_TracedStreamProxy":
        self._sync_iter = iter(self._stream)
        return self

    def __next__(self) -> Any:
        if self._sync_iter is None:
            self._sync_iter = iter(self._stream)
        try:
            chunk = next(self._sync_iter)
        except StopIteration:
            self._finish(status="stream_success", response_status=200)
            raise
        except Exception as exc:
            self._finish(status="stream_error", response_status=0, error=str(exc))
            raise
        self._record_chunk(chunk)
        return chunk


def install_openai_chat_completion_tracer(
    llm: Any,
    *,
    provider: str = "",
    base_url: str = "",
) -> bool:
    """幂等包装 OpenAI SDK `chat.completions.create`，记录真实出站参数。"""
    try:
        client = getattr(llm, "_client", None)
        completions = getattr(getattr(client, "chat", None), "completions", None)
        if completions is None or not hasattr(completions, "create"):
            return False

        current_create = completions.create
        original_create = getattr(current_create, "__dict__", {}).get(
            "_nanobot_original_create",
            current_create,
        )
        target_provider = str(provider or getattr(llm, "provider_name", "") or "unknown")
        target_base_url = str(
            base_url
            or getattr(llm, "base_url", "")
            or getattr(llm, "_base_url_input", "")
            or ""
        ).rstrip("/")

        async def _traced_create(*args: Any, _orig=original_create, **kwargs: Any) -> Any:
            started = time.time()
            log_id = 0
            request_payload: dict[str, Any] = {}
            filtered_kwargs = kwargs
            phase = ""
            try:
                from core.llm_trace_context import (
                    get_llm_trace_execution_vars,
                    get_llm_trace_vars,
                )
                from core.tracing import LLMRequestTracer
                from core.final_tools import filter_sdk_kwargs
                from core.llm_request_sanitizer import sanitize_sdk_kwargs
                from core.tool_execution_policy import (
                    get_current_tool_execution_state,
                )

                trace_id, run_id, source = get_llm_trace_vars()
                phase, route_attempt_index = get_llm_trace_execution_vars()
                tool_execution_state = get_current_tool_execution_state()
                round_index = (
                    tool_execution_state.next_llm_round()
                    if tool_execution_state is not None
                    else 0
                )
                filtered_kwargs = filter_sdk_kwargs(kwargs)
                filtered_kwargs = sanitize_sdk_kwargs(filtered_kwargs)
                request_payload = _request_payload_from_sdk_kwargs(args, filtered_kwargs)
                headers = {"Content-Type": "application/json"}
                api_key = str(getattr(llm, "_api_key", "") or "")
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                extra_headers = getattr(llm, "_extra_headers", {}) or {}
                if isinstance(extra_headers, dict):
                    headers.update(extra_headers)

                log_id = LLMRequestTracer.record_request(
                    trace_id=trace_id,
                    run_id=run_id,
                    source=source or "unknown",
                    phase=phase,
                    round_index=round_index,
                    route_attempt_index=route_attempt_index,
                    provider=target_provider,
                    model=str(request_payload.get("model", "") or ""),
                    url=f"{target_base_url}/chat/completions" if target_base_url else "",
                    method="POST",
                    headers=headers,
                    request=request_payload,
                    status="created",
                )
            except Exception as exc:
                logger.debug("OpenAI SDK request tracing skipped: %s", exc)

            try:
                result = _orig(*args, **filtered_kwargs)
                if inspect.isawaitable(result):
                    result = await result
                try:
                    from core.tracing import LLMRequestTracer

                    if request_payload.get("stream"):
                        return _TracedStreamProxy(
                            result,
                            log_id=log_id,
                            started=started,
                            phase=phase,
                        )
                    else:
                        response = _safe_sdk_response(result)
                        LLMRequestTracer.finish_request(
                            log_id=log_id,
                            response=response,
                            response_status=200,
                            status="success",
                            latency_ms=int((time.time() - started) * 1000),
                            phase=_resolved_agent_phase(phase, response),
                        )
                except Exception as exc:
                    logger.debug("OpenAI SDK response tracing skipped: %s", exc)
                return result
            except Exception as exc:
                try:
                    from core.tracing import LLMRequestTracer

                    LLMRequestTracer.finish_request(
                        log_id=log_id,
                        response={},
                        response_status=0,
                        status="error",
                        error=str(exc),
                        latency_ms=int((time.time() - started) * 1000),
                    )
                except Exception:
                    pass
                raise

        setattr(_traced_create, "_nanobot_original_create", original_create)
        completions.create = _traced_create  # type: ignore[method-assign]
        return True
    except Exception as exc:
        logger.warning("install OpenAI SDK request tracer failed: %s", exc)
        return False
