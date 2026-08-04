"""图片摘要与生成 Provider 的进程级框架无关 Port。"""

from __future__ import annotations

from collections.abc import Mapping
from threading import RLock
from typing import Protocol, runtime_checkable


class MediaToolRuntimeUnavailableError(RuntimeError):
    """图片工具 Provider 尚未绑定或已经停止。"""


@runtime_checkable
class ImageSummaryProviderPort(Protocol):
    def summarize(self, files: tuple[str, ...], focus: str) -> str: ...

    def invalidate_route_cache(self) -> None: ...


@runtime_checkable
class ImageGenerationProviderPort(Protocol):
    def generate(
        self,
        *,
        prompt: str,
        size: str,
        quality: str,
        background: str,
    ) -> Mapping[str, object]: ...


_lock = RLock()
_summary_provider: ImageSummaryProviderPort | None = None
_generation_provider: ImageGenerationProviderPort | None = None


def bind_media_tool_providers(
    *,
    summary: ImageSummaryProviderPort,
    generation: ImageGenerationProviderPort,
) -> None:
    if not isinstance(summary, ImageSummaryProviderPort):
        raise TypeError("summary 未实现 ImageSummaryProviderPort")
    if not isinstance(generation, ImageGenerationProviderPort):
        raise TypeError("generation 未实现 ImageGenerationProviderPort")
    global _summary_provider, _generation_provider
    with _lock:
        if _summary_provider is not None or _generation_provider is not None:
            raise RuntimeError("Media Tool Runtime 已绑定")
        _summary_provider = summary
        _generation_provider = generation


def clear_media_tool_providers() -> None:
    global _summary_provider, _generation_provider
    with _lock:
        _summary_provider = None
        _generation_provider = None


def get_image_summary_provider() -> ImageSummaryProviderPort:
    with _lock:
        provider = _summary_provider
    if provider is None:
        raise MediaToolRuntimeUnavailableError("图片摘要 Provider 当前不可用")
    return provider


def get_image_generation_provider() -> ImageGenerationProviderPort:
    with _lock:
        provider = _generation_provider
    if provider is None:
        raise MediaToolRuntimeUnavailableError("图片生成 Provider 当前不可用")
    return provider


__all__ = [
    "ImageGenerationProviderPort",
    "ImageSummaryProviderPort",
    "MediaToolRuntimeUnavailableError",
    "bind_media_tool_providers",
    "clear_media_tool_providers",
    "get_image_generation_provider",
    "get_image_summary_provider",
]
