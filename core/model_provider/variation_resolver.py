"""Model Preset variation 的框架无关解析器。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

from core.model_provider.preset_config import (
    ModelPreset,
    ResolvedModelPreset,
    validate_variation_patch_map,
)


_SHORTHAND_SELECTION_KEY = "__option__"


def normalize_variation_selections(
    selection_map: dict[str, str],
    preset: ModelPreset,
) -> dict[str, str]:
    groups = preset.variation_groups or {}
    selections = dict(selection_map or {})
    normalized: dict[str, str] = {}

    shorthand = selections.pop(_SHORTHAND_SELECTION_KEY, "")
    if shorthand:
        matching_groups = [
            group_name
            for group_name, options in groups.items()
            if shorthand in (options or {})
        ]
        if not matching_groups:
            raise ValueError(
                f"Preset {preset.id} 不存在 variation 选项 {shorthand}"
            )
        if len(matching_groups) > 1:
            choices = ", ".join(
                f"{group}={shorthand}" for group in matching_groups
            )
            raise ValueError(
                f"Preset {preset.id} 的 variation 选项 {shorthand} 不唯一；"
                f"请指定 {choices}"
            )
        normalized[matching_groups[0]] = shorthand

    for group_name, option_name in selections.items():
        if group_name not in groups:
            raise ValueError(
                f"Preset {preset.id} 不存在 variation 分组 {group_name}"
            )
        options = groups[group_name] or {}
        if option_name not in options:
            raise ValueError(
                f"Preset {preset.id} 的分组 {group_name} "
                f"不存在选项 {option_name}"
            )
        normalized[group_name] = option_name
    return normalized


def _set_dotted_path(
    target: dict[str, Any],
    path: str,
    value: Any,
) -> None:
    cursor = target
    parts = path.split(".")
    for part in parts[:-1]:
        existing = cursor.get(part)
        if existing is None:
            existing = {}
            cursor[part] = existing
        if not isinstance(existing, dict):
            raise ValueError(
                f"Variation patch 路径冲突：{path} 的 {part} 不是对象"
            )
        cursor = existing
    cursor[parts[-1]] = deepcopy(value)


class ModelPresetVariationResolver:
    """解析选择项并返回新的不可变 Preset，不依赖 KT Profile 类型。"""

    def resolve(
        self,
        preset: object,
        selected_variations: dict[str, str],
    ) -> ResolvedModelPreset:
        if not isinstance(preset, ModelPreset):
            raise TypeError("preset 必须是 ModelPreset")
        normalized = normalize_variation_selections(
            selected_variations,
            preset,
        )
        values = {
            "temperature": preset.temperature,
            "reasoning_effort": preset.reasoning_effort,
            "service_tier": preset.service_tier,
            "max_context": preset.max_context,
            "max_output": preset.max_output,
            "extra_body": deepcopy(preset.extra_body),
            "retry_policy": deepcopy(preset.retry_policy),
        }
        written_paths: dict[str, tuple[str, str]] = {}
        for group_name, option_name in normalized.items():
            patch = (
                (preset.variation_groups.get(group_name) or {}).get(option_name)
                or {}
            )
            validate_variation_patch_map(patch)
            for path, value in patch.items():
                previous = written_paths.get(path)
                if previous is not None:
                    raise ValueError(
                        f"Variation 选择在 {path} 冲突："
                        f"{previous[0]}={previous[1]} 与 "
                        f"{group_name}={option_name}"
                    )
                written_paths[path] = (group_name, option_name)
                _set_dotted_path(values, path, value)
        resolved = replace(preset, **values)
        if resolved.max_output > resolved.max_context:
            raise ValueError("Preset max_output 不能大于 max_context")
        return ResolvedModelPreset(resolved, normalized)


__all__ = [
    "ModelPresetVariationResolver",
    "normalize_variation_selections",
]
