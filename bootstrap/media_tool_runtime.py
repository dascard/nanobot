"""图片工具 Provider 的 Composition Root Adapter。"""

from __future__ import annotations

from collections.abc import Mapping


class ImageSummaryProviderAdapter:
    def __init__(self) -> None:
        from nanobot_kt.tools.image_summary import ImageSummaryTool

        self._tool = ImageSummaryTool()

    def summarize(self, files: tuple[str, ...], focus: str) -> str:
        return self._tool._call_qwen(list(files), focus)

    def invalidate_route_cache(self) -> None:
        from nanobot_kt.tools.image_summary import _get_image_summary_route

        if hasattr(_get_image_summary_route, "_cache"):
            delattr(_get_image_summary_route, "_cache")


class ImageGenerationProviderAdapter:
    def __init__(self) -> None:
        from nanobot_kt.tools.image_generation import ImageGenerationTool

        self._tool = ImageGenerationTool()

    def generate(
        self,
        *,
        prompt: str,
        size: str,
        quality: str,
        background: str,
    ) -> Mapping[str, object]:
        return self._tool._call_new_api(
            prompt=prompt,
            size=size,
            quality=quality,
            background=background,
        )


def bind_media_tool_runtime() -> None:
    from core.media_tool_runtime import bind_media_tool_providers

    bind_media_tool_providers(
        summary=ImageSummaryProviderAdapter(),
        generation=ImageGenerationProviderAdapter(),
    )


def clear_media_tool_runtime() -> None:
    from core.media_tool_runtime import clear_media_tool_providers

    clear_media_tool_providers()


__all__ = ["bind_media_tool_runtime", "clear_media_tool_runtime"]
