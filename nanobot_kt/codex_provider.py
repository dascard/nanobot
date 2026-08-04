"""Nanobot 账号绑定的 Codex Responses Provider。

该实现遵循 KT 公开的 ``LLMProvider`` 调用合同，但凭据、客户端和重试状态
均由 Nanobot 持有，不依赖 ``CodexOAuthProvider`` 的私有字段。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
from openai import AsyncOpenAI

from kohakuterrarium.llm.base import (
    ChatResponse,
    LLMConfig,
    NativeToolCall,
    ToolSchema,
)
from kohakuterrarium.llm.codex_auth import CodexTokens
from kohakuterrarium.llm.codex_format import (
    fix_tool_call_pairing,
    maybe_capture_stream_rate_limit,
    to_responses_input,
)
from kohakuterrarium.llm.codex_image_gen import (
    build_image_part,
    translate_image_gen_tool,
)
from kohakuterrarium.llm.codex_rate_limits import (
    UsageSnapshot,
    capture_from_headers,
    parse_rate_limit_event,
    set_cached,
)
from kohakuterrarium.llm.openai_sanitize import strip_surrogates
from kohakuterrarium.llm.recovery import (
    ErrorClass,
    RetryPolicy,
    backoff_delay,
    classify_openai_error,
    drop_last_tool_round,
)


logger = logging.getLogger("nanobot.kt.codex_provider")
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"


async def _capture_rate_limit_headers(response: Any) -> None:
    """从真实响应头更新 KT 已有的进程级 Codex 限额快照。"""

    try:
        snapshot = capture_from_headers(response.headers)
        set_cached(snapshot)
    except Exception as exc:  # pragma: no cover - 观测能力必须 fail-open
        logger.debug("Codex 限额响应头解析失败: %s", exc)


class AccountBoundCodexOAuthProvider:
    """只使用指定 Nanobot 账号凭据的 Codex Provider。"""

    provider_name = "codex"
    provider_native_tools = frozenset({"image_gen"})

    def __init__(
        self,
        model: str = "gpt-5.4",
        *,
        account_id: str,
        reasoning_effort: str = "medium",
        service_tier: str | None = None,
        timeout: float = 300.0,
        max_retries: int = 2,
        retry_policy: RetryPolicy | dict[str, Any] | None = None,
    ) -> None:
        from nanobot_kt.codex_accounts import normalize_codex_account_id

        self.codex_account_id = normalize_codex_account_id(account_id)
        self.model = str(model or "gpt-5.4")
        self.config = LLMConfig(model=self.model, retry_policy=retry_policy)
        self.reasoning_effort = str(reasoning_effort or "medium")
        self.service_tier = service_tier
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.retry_policy = RetryPolicy.from_value(retry_policy)
        self.prompt_cache_key: str | None = None
        self.nanobot_profile_id = ""
        self.nanobot_provider_id = "codex"
        self.nanobot_config_fingerprint = ""

        self._credentials: CodexTokens | None = None
        self._codex_client: Any = None
        self._emergency_drop_handlers: list[
            Callable[[list[dict[str, Any]]], None]
        ] = []
        self._last_tool_calls: list[NativeToolCall] = []
        self._last_usage: dict[str, int] = {}
        self._last_assistant_parts: list[Any] = []

    @property
    def last_tool_calls(self) -> list[NativeToolCall]:
        return list(self._last_tool_calls)

    @property
    def is_authenticated(self) -> bool:
        return self._credentials is not None

    @property
    def last_usage(self) -> dict[str, int]:
        return dict(self._last_usage)

    @property
    def last_assistant_content_parts(self) -> list[Any] | None:
        return list(self._last_assistant_parts) or None

    @property
    def last_assistant_extra_fields(self) -> dict[str, Any]:
        return {}

    def on_emergency_drop(
        self,
        callback: Callable[[list[dict[str, Any]]], None],
    ) -> None:
        self._emergency_drop_handlers.append(callback)

    def translate_provider_native_tool(self, tool: Any) -> dict | None:
        return translate_image_gen_tool(tool)

    async def ensure_authenticated(self) -> None:
        """加载指定账号；过期时刷新，绝不启动交互式 OAuth。"""

        from nanobot_kt.codex_accounts import (
            load_codex_account_tokens,
            refresh_codex_account_tokens,
        )

        credentials = load_codex_account_tokens(self.codex_account_id)
        if credentials.is_expired():
            credentials = await refresh_codex_account_tokens(
                self.codex_account_id
            )
        self._credentials = credentials
        await self._rebuild_codex_connection()

    async def _ensure_valid_credentials(self) -> None:
        if self._credentials is None:
            await self.ensure_authenticated()
            return
        if not self._credentials.is_expired():
            return
        from nanobot_kt.codex_accounts import refresh_codex_account_tokens

        self._credentials = await refresh_codex_account_tokens(
            self.codex_account_id
        )
        await self._rebuild_codex_connection()

    async def _rebuild_codex_connection(self) -> None:
        credentials = self._credentials
        if credentials is None:
            return
        previous = self._codex_client
        http_client = httpx.AsyncClient(
            event_hooks={"response": [_capture_rate_limit_headers]},
            timeout=self.timeout,
        )
        self._codex_client = AsyncOpenAI(
            api_key=credentials.access_token,
            base_url=CODEX_BASE_URL,
            timeout=self.timeout,
            max_retries=self.max_retries,
            http_client=http_client,
        )
        if previous is not None:
            close = getattr(previous, "close", None)
            if callable(close):
                result = close()
                if hasattr(result, "__await__"):
                    await result

    async def chat(
        self,
        messages: list[Any],
        *,
        stream: bool = True,
        tools: list[ToolSchema] | None = None,
        provider_native_tools: list[Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        normalized = [
            dict(message)
            if isinstance(message, dict)
            else message.to_dict()
            for message in messages
        ]
        if stream:
            async for chunk in self._stream_with_recovery(
                normalized,
                tools=tools,
                provider_native_tools=provider_native_tools,
                **kwargs,
            ):
                yield chunk
            return

        parts = [
            chunk
            async for chunk in self._stream_with_recovery(
                normalized,
                tools=tools,
                provider_native_tools=provider_native_tools,
                **kwargs,
            )
        ]
        yield "".join(parts)

    async def chat_complete(
        self,
        messages: list[Any],
        **kwargs: Any,
    ) -> ChatResponse:
        content = "".join(
            [chunk async for chunk in self.chat(messages, **kwargs)]
        )
        return ChatResponse(
            content=content,
            finish_reason="stop",
            usage=self.last_usage,
            model=self.model,
        )

    async def _stream_with_recovery(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[ToolSchema] | None,
        provider_native_tools: list[Any] | None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        current = messages
        attempt = 0
        overflow_recovered = False
        while True:
            try:
                async for chunk in self._stream_once(
                    current,
                    tools=tools,
                    provider_native_tools=provider_native_tools,
                    **kwargs,
                ):
                    yield chunk
                return
            except Exception as exc:
                error_class = classify_openai_error(exc)
                if error_class is ErrorClass.OVERFLOW and not overflow_recovered:
                    dropped, recovered = drop_last_tool_round(current)
                    if dropped:
                        overflow_recovered = True
                        current = recovered
                        self._notify_emergency_drop(recovered)
                        logger.warning(
                            "Codex 上下文溢出，已丢弃最近工具轮次 count=%d",
                            dropped,
                        )
                        continue
                if (
                    error_class in self.retry_policy.retry_classes
                    and attempt < self.retry_policy.max_retries
                ):
                    attempt += 1
                    delay = backoff_delay(attempt, self.retry_policy)
                    logger.warning(
                        "Codex 请求重试 attempt=%d class=%s delay=%.2f",
                        attempt,
                        error_class.value,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise

    async def _stream_once(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[ToolSchema] | None,
        provider_native_tools: list[Any] | None,
        **_kwargs: Any,
    ) -> AsyncIterator[str]:
        self._last_tool_calls = []
        self._last_usage = {}
        self._last_assistant_parts = []
        await self._ensure_valid_credentials()
        if self._codex_client is None:
            raise RuntimeError("Codex 客户端初始化失败")

        instructions = ""
        input_messages: list[dict[str, Any]] = []
        for message in messages:
            if message.get("role") == "system":
                instructions = str(message.get("content") or "")
            else:
                input_messages.append(message)
        api_input = fix_tool_call_pairing(to_responses_input(input_messages))

        api_tools: list[dict[str, Any]] = []
        for schema in tools or []:
            api_tools.append(
                {
                    "type": "function",
                    "name": schema.name,
                    "description": schema.description,
                    "parameters": schema.parameters,
                }
            )

        image_output_format = "png"
        for native_tool in provider_native_tools or []:
            spec = self.translate_provider_native_tool(native_tool)
            if spec is None:
                continue
            api_tools.append(spec)
            if spec.get("type") == "image_generation":
                image_output_format = str(spec.get("output_format") or "png")

        instruction_text = instructions or "You are a helpful assistant."
        cache_key = self.prompt_cache_key or hashlib.sha256(
            instruction_text.encode("utf-8")
        ).hexdigest()[:32]
        extra_params: dict[str, Any] = {}
        if self.reasoning_effort and self.reasoning_effort != "none":
            extra_params["reasoning"] = {"effort": self.reasoning_effort}
        if self.service_tier:
            extra_params["service_tier"] = self.service_tier

        stream = await self._codex_client.responses.create(
            model=self.model,
            instructions=instruction_text,
            input=api_input,
            tools=api_tools or None,
            store=False,
            stream=True,
            prompt_cache_key=cache_key,
            extra_headers={"session_id": cache_key},
            **extra_params,
        )
        collected_calls: list[NativeToolCall] = []
        async for event in stream:
            maybe_capture_stream_rate_limit(
                event,
                parse_rate_limit_event,
                UsageSnapshot,
                set_cached,
            )
            event_type = str(getattr(event, "type", "") or "")
            if event_type == "response.output_text.delta":
                yield strip_surrogates(str(getattr(event, "delta", "") or ""))
            elif event_type == "response.output_item.done":
                item = getattr(event, "item", None)
                item_type = str(getattr(item, "type", "") or "")
                if item_type == "function_call":
                    collected_calls.append(
                        NativeToolCall(
                            id=str(getattr(item, "call_id", "") or ""),
                            name=str(getattr(item, "name", "") or ""),
                            arguments=str(
                                getattr(item, "arguments", "") or ""
                            ),
                        )
                    )
                elif item_type == "image_generation_call":
                    part = build_image_part(item, image_output_format)
                    if part is not None:
                        self._last_assistant_parts.append(part)
            elif event_type == "response.completed":
                self._capture_usage(getattr(event, "response", None))
        self._last_tool_calls = collected_calls

    def _capture_usage(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        details = getattr(usage, "input_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) if details else 0
        self._last_usage = {
            "prompt_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "completion_tokens": int(
                getattr(usage, "output_tokens", 0) or 0
            ),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            "cached_tokens": int(cached or 0),
        }

    def _notify_emergency_drop(self, messages: list[dict[str, Any]]) -> None:
        for callback in tuple(self._emergency_drop_handlers):
            try:
                callback(messages)
            except Exception as exc:  # pragma: no cover - 防御性隔离
                logger.debug("Codex 上下文恢复回调失败: %s", exc)

    def with_model(self, name: str) -> "AccountBoundCodexOAuthProvider":
        if not name or name == self.model:
            return self
        clone = AccountBoundCodexOAuthProvider(
            model=name,
            account_id=self.codex_account_id,
            reasoning_effort=self.reasoning_effort,
            service_tier=self.service_tier,
            timeout=self.timeout,
            max_retries=self.max_retries,
            retry_policy=self.retry_policy,
        )
        clone._credentials = self._credentials
        clone._codex_client = self._codex_client
        clone._emergency_drop_handlers = list(self._emergency_drop_handlers)
        clone.prompt_cache_key = self.prompt_cache_key
        clone.provider_name = self.provider_name
        clone.provider_native_tools = self.provider_native_tools
        clone.nanobot_profile_id = self.nanobot_profile_id
        clone.nanobot_provider_id = self.nanobot_provider_id
        clone.nanobot_config_fingerprint = self.nanobot_config_fingerprint
        return clone

    async def close(self) -> None:
        client = self._codex_client
        self._codex_client = None
        if client is None:
            return
        close = getattr(client, "close", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result


__all__ = ["AccountBoundCodexOAuthProvider", "CODEX_BASE_URL"]
