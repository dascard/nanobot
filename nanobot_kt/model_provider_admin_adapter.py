"""KT Provider 管理能力到 Core 控制面 Port 的适配器。"""

from __future__ import annotations

from collections.abc import Mapping
import inspect

from core.model_provider.admin_runtime import ModelPresetProbeResult
from core.model_provider.route_plan import ReplyRoutePlan


class KtModelProviderAdminAdapter:
    def list_native_tools(self) -> tuple[Mapping[str, object], ...]:
        from kohakuterrarium.studio.identity.llm_native_tools import (
            list_native_tools,
        )

        return tuple(
            dict(item) for item in list_native_tools() if isinstance(item, dict)
        )

    async def probe_preset(
        self,
        plan: ReplyRoutePlan,
        *,
        prompt: str,
    ) -> ModelPresetProbeResult:
        from nanobot_kt.model_provider_adapter import create_kt_provider

        provider = create_kt_provider(plan)
        try:
            response = await provider.chat_complete(
                [
                    {"role": "system", "content": "你是 Nanobot 模型连通性测试。"},
                    {"role": "user", "content": prompt},
                ]
            )
            return ModelPresetProbeResult(
                content=str(response.content or ""),
                usage=dict(response.usage or {}),
            )
        finally:
            close = getattr(provider, "close", None)
            if callable(close):
                try:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    pass


__all__ = ["KtModelProviderAdminAdapter"]
