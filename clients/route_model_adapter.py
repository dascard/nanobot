"""把兼容 classifier 路由客户端适配为核心 RouteModel Port。"""

from __future__ import annotations

from clients import classifier_client
from core.model_provider.contracts import ModelProviderResponse
from core.model_provider.route_runtime import RouteModelRequest


class ClassifierRouteModelAdapter:
    """复用现有配置解析与 OpenAI-compatible transport 的生产 Adapter。"""

    @property
    def adapter_id(self) -> str:
        return "classifier_route_model"

    def complete_route(self, request: RouteModelRequest) -> ModelProviderResponse:
        response = classifier_client.call_model_route_response(
            route_key=request.route_key,
            messages=[dict(message) for message in request.messages] or None,
            system_prompt=request.system_prompt,
            user_message=request.user_message,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            timeout=request.timeout_seconds,
        )
        if isinstance(response, ModelProviderResponse):
            return response
        return ModelProviderResponse(
            content=str(response.content or ""),
            reasoning_content=str(response.reasoning_content or ""),
            finish_reason=response.finish_reason,
            usage=dict(response.usage),
            raw_response=dict(response.raw_response),
        )


__all__ = ["ClassifierRouteModelAdapter"]
