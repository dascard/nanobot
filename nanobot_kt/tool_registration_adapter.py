"""把 Nanobot 工具注册快照投影为 KT 配置。

KT creature YAML 中的 ``tools`` 仅保留为兼容输入；生产启动时会被本
Adapter 的冻结投影覆盖，因此 YAML 顺序或遗留条目不能改变实际加载能力。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable

from kohakuterrarium.core.config_types import ToolConfigItem

from core.tool_registration import (
    TOOL_REGISTRATION_REGISTRY,
    list_active_tool_registrations,
    list_tool_registrations,
)


class ToolRegistrationDriftError(RuntimeError):
    """KT 执行目录与 Nanobot 冻结注册快照发生漂移。"""


@dataclass(frozen=True, slots=True)
class KtExecutionBinding:
    module: str
    class_name: str
    tool_type: str = "package"


@dataclass(frozen=True, slots=True)
class KtExecutionBindingValidation:
    active_tool_count: int
    missing_port_ids: tuple[str, ...]
    orphan_port_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ToolRegistrationProjection:
    declared_names: tuple[str, ...]
    projected_names: tuple[str, ...]
    removed_declared_names: tuple[str, ...]
    added_projected_names: tuple[str, ...]
    generation: int
    sha256: str


_KT_EXECUTION_BINDINGS = MappingProxyType({
    "tool.sql_analysis.execute": KtExecutionBinding(
        "nanobot_kt.tools.sql_analysis",
        "SQLAnalysisTool",
    ),
    "tool.ai_daily.execute": KtExecutionBinding(
        "nanobot_kt.tools.ai_daily",
        "AiDailyTool",
    ),
    "tool.memory_query.execute": KtExecutionBinding(
        "nanobot_kt.tools.memory_query",
        "MemoryQueryTool",
    ),
    "tool.knowledge_query.execute": KtExecutionBinding(
        "nanobot_kt.tools.knowledge_query",
        "KnowledgeQueryTool",
    ),
    "tool.web_search.execute": KtExecutionBinding(
        "nanobot_kt.tools.web_search",
        "WebSearchTool",
    ),
    "tool.image_summary.execute": KtExecutionBinding(
        "nanobot_kt.tools.image_summary",
        "ImageSummaryTool",
    ),
    "tool.image_generation.execute": KtExecutionBinding(
        "nanobot_kt.tools.image_generation",
        "ImageGenerationTool",
    ),
    "tool.persona_update.execute": KtExecutionBinding(
        "nanobot_kt.tools.persona_update",
        "PersonaUpdateTool",
    ),
    "tool.schedule_task.execute": KtExecutionBinding(
        "nanobot_kt.tools.schedule_task",
        "ScheduleTaskTool",
    ),
    "tool.group_analysis.execute": KtExecutionBinding(
        "nanobot_kt.tools.group_analysis",
        "GroupAnalysisTool",
    ),
    "tool.sticker_search.execute": KtExecutionBinding(
        "nanobot_kt.tools.sticker_search",
        "StickerSearchTool",
    ),
    "tool.reply.execute": KtExecutionBinding(
        "nanobot_kt.tools.reply",
        "ReplyTool",
    ),
    "tool.no_reply.execute": KtExecutionBinding(
        "nanobot_kt.tools.reply",
        "NoReplyTool",
    ),
    "tool.sandbox_exec.execute": KtExecutionBinding(
        "nanobot_kt.tools.sandbox",
        "SandboxExecTool",
    ),
    "tool.sandbox_poll.execute": KtExecutionBinding(
        "nanobot_kt.tools.sandbox",
        "SandboxPollTool",
    ),
    "tool.sandbox_write_stdin.execute": KtExecutionBinding(
        "nanobot_kt.tools.sandbox",
        "SandboxWriteStdinTool",
    ),
    "tool.sandbox_terminate.execute": KtExecutionBinding(
        "nanobot_kt.tools.sandbox",
        "SandboxTerminateTool",
    ),
    "tool.workspace_read.execute": KtExecutionBinding(
        "nanobot_kt.tools.sandbox",
        "WorkspaceReadTool",
    ),
    "tool.workspace_search.execute": KtExecutionBinding(
        "nanobot_kt.tools.sandbox",
        "WorkspaceSearchTool",
    ),
    "tool.workspace_write.execute": KtExecutionBinding(
        "nanobot_kt.tools.sandbox",
        "WorkspaceWriteTool",
    ),
    "tool.workspace_edit.execute": KtExecutionBinding(
        "nanobot_kt.tools.sandbox",
        "WorkspaceEditTool",
    ),
    "tool.asset_import.execute": KtExecutionBinding(
        "nanobot_kt.tools.sandbox",
        "AssetImportTool",
    ),
    "tool.asset_publish.execute": KtExecutionBinding(
        "nanobot_kt.tools.sandbox",
        "AssetPublishTool",
    ),
})


def _active_port_ids() -> tuple[str, ...]:
    port_ids: list[str] = []
    for registration in list_active_tool_registrations():
        binding = registration.execution_binding
        if binding is None:
            raise ToolRegistrationDriftError(
                f"active tool {registration.name} 缺少执行 Port"
            )
        port_ids.append(binding.port_id)
    return tuple(sorted(port_ids))


def validate_kt_execution_bindings() -> KtExecutionBindingValidation:
    """验证 KT Adapter 完整覆盖且没有悬空执行绑定。"""

    expected = set(_active_port_ids())
    actual = set(_KT_EXECUTION_BINDINGS)
    missing = tuple(sorted(expected - actual))
    orphan = tuple(sorted(actual - expected))
    result = KtExecutionBindingValidation(
        active_tool_count=len(expected),
        missing_port_ids=missing,
        orphan_port_ids=orphan,
    )
    if missing or orphan:
        raise ToolRegistrationDriftError(
            "KT execution binding 漂移："
            f"缺失={list(missing)}，悬空={list(orphan)}"
        )
    return result


def build_kt_tool_configs() -> tuple[ToolConfigItem, ...]:
    """从冻结注册快照生成 KT ToolConfigItem，完全忽略 YAML 工具列表。"""

    validate_kt_execution_bindings()
    items: list[ToolConfigItem] = []
    for registration in list_active_tool_registrations():
        execution = registration.execution_binding
        if execution is None:
            raise ToolRegistrationDriftError(
                f"active tool {registration.name} 缺少执行绑定"
            )
        binding = _KT_EXECUTION_BINDINGS[execution.port_id]
        items.append(
            ToolConfigItem(
                name=registration.name,
                type=binding.tool_type,
                module=binding.module,
                class_name=binding.class_name,
            )
        )
    return tuple(items)


def apply_tool_registration_projection(
    config: object,
) -> ToolRegistrationProjection:
    """以代码注册快照覆盖 creature 配置中的工具声明。"""

    declared_items = getattr(config, "tools", ()) or ()
    declared_names = tuple(
        str(getattr(item, "name", "") or "").strip()
        for item in declared_items
        if str(getattr(item, "name", "") or "").strip()
    )
    projected = build_kt_tool_configs()
    projected_names = tuple(item.name for item in projected)
    setattr(config, "tools", list(projected))

    declared_set = set(declared_names)
    projected_set = set(projected_names)
    snapshot = TOOL_REGISTRATION_REGISTRY.registry_snapshot
    return ToolRegistrationProjection(
        declared_names=declared_names,
        projected_names=projected_names,
        removed_declared_names=tuple(sorted(declared_set - projected_set)),
        added_projected_names=tuple(sorted(projected_set - declared_set)),
        generation=snapshot.generation,
        sha256=snapshot.sha256,
    )


def _expected_loaded_tool_names() -> tuple[str, ...]:
    return tuple(
        registration.name
        for registration in list_active_tool_registrations()
    )


def validate_loaded_tool_names(loaded_names: Iterable[str]) -> None:
    """真实 KT Agent 启动后严格核对工具目录。

    框架自带且已登记为 ``framework`` 的工具（目前为 ``skill``）允许额外
    存在；其他未登记工具和任何 active 工具缺失都直接阻断启动。
    """

    loaded = {
        str(name or "").strip()
        for name in loaded_names
        if str(name or "").strip()
    }
    expected = set(_expected_loaded_tool_names())
    framework = {
        registration.name
        for registration in list_tool_registrations()
        if registration.lifecycle == "framework"
    }
    missing = tuple(sorted(expected - loaded))
    unknown = tuple(sorted(loaded - expected - framework))
    if missing or unknown:
        raise ToolRegistrationDriftError(
            "KT loaded tool 与冻结注册快照不一致："
            f"缺失={list(missing)}，未登记={list(unknown)}"
        )


validate_loaded_tool_names.expected_names = _expected_loaded_tool_names  # type: ignore[attr-defined]


def build_tool_registry_runtime_info(
    loaded_names: Iterable[str],
    projection: ToolRegistrationProjection,
    *,
    strict: bool,
) -> dict[str, object]:
    """生成管理端审计快照，并在真实 KT 运行时执行严格漂移校验。"""

    loaded = {
        str(name or "").strip()
        for name in loaded_names
        if str(name or "").strip()
    }
    if strict:
        validate_loaded_tool_names(loaded)
    active = set(_expected_loaded_tool_names())
    framework = {
        registration.name
        for registration in list_tool_registrations()
        if registration.lifecycle == "framework"
    }
    snapshot = TOOL_REGISTRATION_REGISTRY.registry_snapshot
    return {
        "kt_loaded": sorted(loaded),
        "missing_meta": sorted(loaded - active - framework),
        "missing_kt": sorted(active - loaded),
        "declared_yaml": list(projection.declared_names),
        "projected": list(projection.projected_names),
        "removed_declared": list(projection.removed_declared_names),
        "added_projected": list(projection.added_projected_names),
        "generation": snapshot.generation,
        "sha256": snapshot.sha256,
    }
