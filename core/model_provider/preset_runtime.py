"""Model Preset 解析 Port 的进程级绑定。"""

from __future__ import annotations

from typing import Protocol


class ModelPresetResolverPort(Protocol):
    def resolve(
        self,
        preset: object,
        selected_variations: dict[str, str],
    ) -> object: ...


_resolver: ModelPresetResolverPort | None = None


def start_model_preset_resolver_runtime(
    resolver: ModelPresetResolverPort,
) -> None:
    global _resolver
    if _resolver is not None:
        raise RuntimeError("Model Preset Resolver Runtime 已启动")
    _resolver = resolver


def stop_model_preset_resolver_runtime() -> None:
    global _resolver
    _resolver = None


def resolve_model_preset_with_runtime(
    preset: object,
    selected_variations: dict[str, str],
) -> object:
    if _resolver is None:
        raise RuntimeError("Model Preset Resolver Runtime 尚未启动")
    return _resolver.resolve(preset, selected_variations)


__all__ = [
    "ModelPresetResolverPort",
    "resolve_model_preset_with_runtime",
    "start_model_preset_resolver_runtime",
    "stop_model_preset_resolver_runtime",
]
