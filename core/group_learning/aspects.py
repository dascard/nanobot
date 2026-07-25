"""群分析方面的冻结 Descriptor Registry。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from core.registry import RegistryBuilder, RegistrySnapshot
from core.registry.validation import validate_identifier


GROUP_ANALYSIS_ASPECT_IDS = (
    "topics",
    "expressions",
    "slang",
    "style",
    "titles",
    "quotes",
    "quality",
)
GROUP_LEARNING_MEMORY_TYPES = (
    "topic",
    "expression",
    "slang",
    "style",
)
GROUP_ANALYSIS_REPORT_ASPECT_IDS = (
    "topics",
    "titles",
    "quotes",
    "quality",
)
GROUP_ANALYSIS_TOOL_DEFAULT_ASPECT_IDS = (
    "topics",
    "titles",
    "quotes",
    "quality",
)


@dataclass(frozen=True, slots=True)
class GroupAnalysisAspectDescriptor:
    """一个可选择的群分析方面及其长期数据策略。"""

    aspect_id: str
    display_name: str
    task_key: str
    schedule_default: bool
    writes_long_term_memory: bool
    memory_type: str
    prompt_injectable: bool
    owner_module: str = "core.group_learning"
    lifecycle: str = "active"

    def __post_init__(self) -> None:
        validate_identifier(
            self.aspect_id,
            field_name="group_analysis.aspect_id",
        )
        validate_identifier(
            self.owner_module,
            field_name="group_analysis.owner_module",
        )
        if self.aspect_id not in GROUP_ANALYSIS_ASPECT_IDS:
            raise ValueError(f"未知群分析方面：{self.aspect_id}")
        if not self.display_name.strip():
            raise ValueError("群分析方面显示名不能为空")
        if not self.task_key.startswith("tasks/"):
            raise ValueError("群分析方面必须绑定 canonical Task key")
        if self.lifecycle not in {"active", "deprecated", "retired"}:
            raise ValueError("群分析方面 lifecycle 无效")
        if self.writes_long_term_memory:
            if self.memory_type not in GROUP_LEARNING_MEMORY_TYPES:
                raise ValueError("长期群分析方面必须绑定正式 memory_type")
        elif self.memory_type:
            raise ValueError("报告方面不能声明长期 memory_type")
        if self.prompt_injectable and not self.writes_long_term_memory:
            raise ValueError("报告方面不能成为 Prompt 注入候选")

    @property
    def registry_namespace(self) -> str:
        return "group_analysis_aspect"

    @property
    def registry_id(self) -> str:
        return self.aspect_id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "display_name": self.display_name,
            "task_key": self.task_key,
            "schedule_default": self.schedule_default,
            "writes_long_term_memory": self.writes_long_term_memory,
            "memory_type": self.memory_type,
            "prompt_injectable": self.prompt_injectable,
            "owner_module": self.owner_module,
            "lifecycle": self.lifecycle,
        }


def _build_aspect_registry(
) -> RegistrySnapshot[GroupAnalysisAspectDescriptor]:
    descriptors = (
        GroupAnalysisAspectDescriptor(
            aspect_id="topics",
            display_name="稳定讨论话题",
            task_key="tasks/group_analysis_topics",
            schedule_default=True,
            writes_long_term_memory=True,
            memory_type="topic",
            prompt_injectable=True,
        ),
        GroupAnalysisAspectDescriptor(
            aspect_id="expressions",
            display_name="群内常用表达",
            task_key="tasks/group_memory_learning",
            schedule_default=True,
            writes_long_term_memory=True,
            memory_type="expression",
            prompt_injectable=True,
        ),
        GroupAnalysisAspectDescriptor(
            aspect_id="slang",
            display_name="黑话与术语",
            task_key="tasks/group_memory_learning",
            schedule_default=True,
            writes_long_term_memory=True,
            memory_type="slang",
            prompt_injectable=True,
        ),
        GroupAnalysisAspectDescriptor(
            aspect_id="style",
            display_name="群体交流风格",
            task_key="tasks/group_memory_learning",
            schedule_default=True,
            writes_long_term_memory=True,
            memory_type="style",
            prompt_injectable=True,
        ),
        GroupAnalysisAspectDescriptor(
            aspect_id="titles",
            display_name="活跃用户称号",
            task_key="tasks/group_analysis_titles",
            schedule_default=False,
            writes_long_term_memory=False,
            memory_type="",
            prompt_injectable=False,
        ),
        GroupAnalysisAspectDescriptor(
            aspect_id="quotes",
            display_name="群聊金句",
            task_key="tasks/group_analysis_quotes",
            schedule_default=False,
            writes_long_term_memory=False,
            memory_type="",
            prompt_injectable=False,
        ),
        GroupAnalysisAspectDescriptor(
            aspect_id="quality",
            display_name="聊天质量锐评",
            task_key="tasks/group_analysis_quality",
            schedule_default=False,
            writes_long_term_memory=False,
            memory_type="",
            prompt_injectable=False,
        ),
    )
    builder = RegistryBuilder[GroupAnalysisAspectDescriptor](
        "group_analysis_aspect"
    )
    for descriptor in descriptors:
        builder.register(descriptor)
    return builder.freeze()


GROUP_ANALYSIS_ASPECT_REGISTRY = _build_aspect_registry()


def list_group_analysis_aspects(
) -> tuple[GroupAnalysisAspectDescriptor, ...]:
    return tuple(
        GROUP_ANALYSIS_ASPECT_REGISTRY.require(aspect_id)
        for aspect_id in GROUP_ANALYSIS_ASPECT_IDS
    )


def default_scheduled_aspects() -> tuple[str, ...]:
    return tuple(
        descriptor.aspect_id
        for descriptor in list_group_analysis_aspects()
        if descriptor.schedule_default
    )


def default_tool_aspects() -> tuple[str, ...]:
    """返回显式工具省略 aspects 时的兼容默认值。"""

    return GROUP_ANALYSIS_TOOL_DEFAULT_ASPECT_IDS


def validate_aspect_selection(
    aspects: object,
    *,
    use_schedule_default: bool = False,
) -> tuple[str, ...]:
    """按 Registry 顺序返回去重后的合法方面。"""

    if aspects is None and use_schedule_default:
        return default_scheduled_aspects()
    if not isinstance(aspects, (list, tuple)):
        raise ValueError("aspects 必须是数组")
    requested = tuple(str(item or "").strip() for item in aspects)
    if not requested or any(not item for item in requested):
        raise ValueError("aspects 不能为空")
    if len(requested) != len(set(requested)):
        raise ValueError("aspects 不能重复")
    unknown = set(requested) - set(
        GROUP_ANALYSIS_ASPECT_REGISTRY.ordered_ids
    )
    if unknown:
        raise ValueError(
            "未知群分析方面：" + ", ".join(sorted(unknown))
        )
    requested_set = set(requested)
    return tuple(
        aspect_id
        for aspect_id in GROUP_ANALYSIS_ASPECT_IDS
        if aspect_id in requested_set
    )


__all__ = [
    "GROUP_ANALYSIS_ASPECT_IDS",
    "GROUP_ANALYSIS_ASPECT_REGISTRY",
    "GROUP_ANALYSIS_REPORT_ASPECT_IDS",
    "GROUP_ANALYSIS_TOOL_DEFAULT_ASPECT_IDS",
    "GROUP_LEARNING_MEMORY_TYPES",
    "GroupAnalysisAspectDescriptor",
    "default_scheduled_aspects",
    "default_tool_aspects",
    "list_group_analysis_aspects",
    "validate_aspect_selection",
]
