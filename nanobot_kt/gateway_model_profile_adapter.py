"""KT reply Route 到 Gateway 模型 Profile Port 的 Adapter。"""

from __future__ import annotations

from config import NEW_API_BASE_URL, NEW_API_KEY
from core.gateway_control.model_profiles import (
    GatewayModelProfileDescriptor,
)
from nanobot_kt.model_runtime import (
    reply_model_profile_descriptors,
    resolve_reply_route_plans,
)


class KtGatewayModelProfileAdapter:
    """仅公开已通过 KT reply Route 校验的非敏感描述。"""

    def list_profiles(self) -> tuple[GatewayModelProfileDescriptor, ...]:
        plans = resolve_reply_route_plans(
            default_base_url=NEW_API_BASE_URL,
            default_api_key=NEW_API_KEY,
            session_id="gateway-control",
        )
        return tuple(
            GatewayModelProfileDescriptor(
                profile_id=str(item.get("profile_id") or ""),
                model=str(item.get("model") or ""),
                provider_id=str(item.get("provider_id") or ""),
                provider_name=str(item.get("provider_name") or ""),
                supports_tools=bool(item.get("supports_tools")),
                supports_image=bool(item.get("supports_image")),
            )
            for item in reply_model_profile_descriptors(plans)
        )


__all__ = ["KtGatewayModelProfileAdapter"]
