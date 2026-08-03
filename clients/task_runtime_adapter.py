"""把统一 TaskModelExecutionPort 适配到现有模型路由 transport。"""

from __future__ import annotations

import urllib.error

from clients import classifier_client
from clients.new_api_client import NewAPIClient
from config import NEW_API_KEY
from core.async_bridge import run_awaitable_sync
from core.model_provider.route_registry import (
    ModelRouteExecutionMode,
    require_model_route_descriptor,
)
from core.model_provider.route_runtime import (
    RouteModelRuntimeUnavailableError,
    call_model_route_response,
)
from core.task_runtime import (
    TaskModelCompletion,
    TaskModelExecutionError,
    TaskModelRequest,
)


class RouteTaskModelAdapter:
    @property
    def adapter_id(self) -> str:
        return "route_task_model"

    def complete_task(
        self,
        request: TaskModelRequest,
    ) -> TaskModelCompletion:
        try:
            route = classifier_client.resolve_model_route(
                request.route_key
            )
            descriptor = require_model_route_descriptor(
                request.route_key
            )
            if (
                descriptor.execution_mode
                is ModelRouteExecutionMode.CHAT_COMPLETION
            ):
                route_attempts = (
                    classifier_client.resolve_model_route_attempts(
                        request.route_key
                    )
                )
                route = route_attempts[min(
                    request.attempt_no - 1,
                    len(route_attempts) - 1,
                )]
                return self._complete_chat_task(
                    request,
                    route=route,
                )
            response = call_model_route_response(
                    route_key=request.route_key,
                    messages=[
                        dict(message)
                        for message in request.messages
                    ],
                    max_tokens=request.max_tokens,
                    temperature=request.temperature,
                    timeout=request.timeout_seconds,
                )
        except TaskModelExecutionError:
            raise
        except classifier_client.ModelRouteProviderUnavailableError as exc:
            raise TaskModelExecutionError(
                code="provider_unavailable",
                summary="模型路由 Provider 已禁用",
                retryable=False,
                cause_type=type(exc).__name__,
            ) from exc
        except RouteModelRuntimeUnavailableError as exc:
            raise TaskModelExecutionError(
                code="provider_unavailable",
                summary="模型路由运行时不可用",
                retryable=True,
                cause_type=type(exc).__name__,
            ) from exc
        except TimeoutError as exc:
            raise TaskModelExecutionError(
                code="execution_timeout",
                summary="模型路由请求超时",
                retryable=True,
                cause_type=type(exc).__name__,
            ) from exc
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                failure_code = "authorization_failed"
                retryable = False
            elif exc.code == 429:
                failure_code = "rate_limited"
                retryable = True
            elif exc.code >= 500:
                failure_code = "transient_transport"
                retryable = True
            else:
                failure_code = "permanent_failure"
                retryable = False
            raise TaskModelExecutionError(
                code=failure_code,
                summary="模型路由返回 HTTP 错误",
                retryable=retryable,
                cause_type=type(exc).__name__,
            ) from exc
        except urllib.error.URLError as exc:
            raise TaskModelExecutionError(
                code="provider_unavailable",
                summary="模型路由网络不可用",
                retryable=True,
                cause_type=type(exc).__name__,
            ) from exc
        except (UnicodeError, ValueError) as exc:
            raise TaskModelExecutionError(
                code="provider_error",
                summary="模型路由响应合同无效",
                retryable=True,
                cause_type=type(exc).__name__,
            ) from exc

        return TaskModelCompletion(
            content=response.content,
            route_key=request.route_key,
            provider=str(route.get("provider_id") or ""),
            model=str(
                response.raw_response.get("model")
                or route.get("model")
                or ""
            ),
            usage=response.usage,
            finish_reason=response.finish_reason,
            metadata={
                "requested_model": str(route.get("model") or ""),
            },
        )

    @staticmethod
    def _complete_chat_task(
        request: TaskModelRequest,
        *,
        route: dict,
    ) -> TaskModelCompletion:
        client = NewAPIClient(
            api_key=route.get("api_key") or NEW_API_KEY,
            base_url=route.get("base_url") or "",
        )
        response = run_awaitable_sync(
            client.chat_completion(
                messages=[
                    dict(message)
                    for message in request.messages
                ],
                temperature=(
                    request.temperature
                    if request.temperature is not None
                    else float(route.get("temperature", 0.1))
                ),
                manual_model=route.get("model", ""),
                max_tokens=(
                    request.max_tokens
                    if request.max_tokens is not None
                    else int(route.get("max_tokens", 4096))
                ),
                llm_source=request.route_key,
                enable_thinking=route.get("enable_thinking", "false"),
            )
        )
        if not isinstance(response, dict):
            raise TaskModelExecutionError(
                code="provider_error",
                summary="聊天模型返回了无效响应合同",
                retryable=True,
                cause_type=type(response).__name__,
            )
        if response.get("error"):
            raise TaskModelExecutionError(
                code="provider_error",
                summary="聊天模型调用失败",
                retryable=True,
                cause_type="ModelRouteResponseError",
            )
        try:
            choice = response["choices"][0]
            content = str(choice["message"].get("content") or "")
            finish_reason = str(
                choice.get("finish_reason") or ""
            ).strip().lower()
        except (AttributeError, IndexError, KeyError, TypeError) as exc:
            raise TaskModelExecutionError(
                code="provider_error",
                summary="聊天模型响应缺少正文",
                retryable=True,
                cause_type=type(exc).__name__,
            ) from exc
        if finish_reason in {"length", "max_tokens"}:
            raise TaskModelExecutionError(
                code="output_limit_exceeded",
                summary="聊天模型输出达到容量上限",
                retryable=False,
                cause_type="ModelOutputCapacityError",
            )
        raw_log_id = response.get("_nanobot_request_log_id")
        request_log_id = (
            int(raw_log_id)
            if type(raw_log_id) is int and raw_log_id > 0
            else None
        )
        observed_model = str(
            response.get("model") or ""
        ).strip()
        requested_model = str(
            response.get("_nanobot_requested_model")
            or response.get("_nanobot_model_id")
            or route.get("model")
            or ""
        ).strip()
        return TaskModelCompletion(
            content=content,
            route_key=request.route_key,
            provider=str(route.get("provider_id") or "newapi"),
            model=observed_model or requested_model or "unknown",
            usage=(
                response.get("usage")
                if isinstance(response.get("usage"), dict)
                else {}
            ),
            finish_reason=finish_reason or None,
            metadata={
                "requested_model": requested_model or "unknown",
                "request_log_id": request_log_id,
                "actual_model_observed": bool(observed_model),
            },
        )


__all__ = ["RouteTaskModelAdapter"]
