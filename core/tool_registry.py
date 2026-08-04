"""工具注册表——兼容元数据与类型化能力描述的单一来源。"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

from core.registry import RegistryBuilder, RegistrySnapshot


@dataclass(frozen=True)
class ToolDef:
    name: str                  # config.yaml 内部名称
    label: str                 # 中文展示标签
    category: str              # communication/data/analysis/system/file
    risk_level: str            # low/medium/high
    private_default: bool      # 私聊默认开启
    group_default: bool        # 群聊默认开启
    description: str           # WebUI 用途说明
    force_enabled: bool = False           # 不可禁用（reply/no_reply）
    force_disabled: bool = False          # 全局硬禁用（任何默认或覆盖都不能开启）
    force_disabled_group: bool = False    # 群聊强制禁用（bash/write/edit）
    supports_background: bool = True      # 是否向模型暴露 run_in_background
    prompt_template_keys: tuple[str, ...] | None = None
    effect_policy: str = "read_only"      # read_only/local_write/external


ToolAvailabilityPolicy = Literal[
    "force_enabled",
    "force_disabled",
    "configurable",
    "framework_only",
]
ToolExecutionPolicy = Literal["foreground_or_background", "foreground_only"]
ToolTracePolicy = Literal["standard", "metadata_only"]
ToolEffectPolicy = Literal["read_only", "local_write", "external"]


class ToolDescriptorRegistryError(ValueError):
    """工具描述符注册失败。"""


@dataclass(frozen=True)
class ToolDescriptor:
    """由兼容 ToolDef 派生的稳定、可验证工具能力描述。"""

    name: str
    definition: ToolDef
    availability_policy: ToolAvailabilityPolicy
    execution_policy: ToolExecutionPolicy
    trace_policy: ToolTracePolicy
    prompt_template_keys: tuple[str, ...]
    prompt_source_precedence: tuple[str, ...]
    prompt_editable: bool
    owner_module: str
    domain: str
    effect_policy: ToolEffectPolicy = "read_only"
    framework_owned: bool = False

    @property
    def supports_background(self) -> bool:
        return self.execution_policy == "foreground_or_background"

    @property
    def registry_namespace(self) -> str:
        return "tool"

    @property
    def registry_id(self) -> str:
        return self.name

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "name": self.name,
            "definition": {
                "label": self.definition.label,
                "category": self.definition.category,
                "risk_level": self.definition.risk_level,
                "private_default": self.definition.private_default,
                "group_default": self.definition.group_default,
                "description": self.definition.description,
                "force_enabled": self.definition.force_enabled,
                "force_disabled": self.definition.force_disabled,
                "force_disabled_group": (
                    self.definition.force_disabled_group
                ),
                "supports_background": (
                    self.definition.supports_background
                ),
            },
            "availability_policy": self.availability_policy,
            "execution_policy": self.execution_policy,
            "trace_policy": self.trace_policy,
            "prompt_template_keys": list(self.prompt_template_keys),
            "prompt_source_precedence": list(
                self.prompt_source_precedence
            ),
            "prompt_editable": self.prompt_editable,
            "owner_module": self.owner_module,
            "domain": self.domain,
            "effect_policy": self.effect_policy,
            "framework_owned": self.framework_owned,
        }


class ToolDescriptorRegistry:
    """启动后只读的工具描述符快照。"""

    def __init__(self, descriptors: Mapping[str, ToolDescriptor]):
        self._descriptors = MappingProxyType(dict(descriptors))
        builder = RegistryBuilder[ToolDescriptor]("tool")
        for name in sorted(self._descriptors):
            builder.register(self._descriptors[name])
        self._registry_snapshot: RegistrySnapshot[ToolDescriptor] = (
            builder.freeze()
        )

    @property
    def frozen(self) -> bool:
        return True

    @property
    def registry_snapshot(self) -> RegistrySnapshot[ToolDescriptor]:
        return self._registry_snapshot

    @classmethod
    def from_metadata(
        cls,
        metadata: Mapping[str, ToolDef],
        framework_metadata: Mapping[str, ToolDef] | None = None,
    ) -> "ToolDescriptorRegistry":
        descriptors: dict[str, ToolDescriptor] = {}
        for framework_owned, definitions in (
            (False, metadata),
            (True, framework_metadata or {}),
        ):
            for key, definition in definitions.items():
                name = str(key or "").strip()
                if name != definition.name:
                    raise ToolDescriptorRegistryError(
                        f"tool registry key {name!r} 与 ToolDef.name {definition.name!r} 不一致"
                    )
                if name in descriptors:
                    raise ToolDescriptorRegistryError(f"tool descriptor 重复登记: {name}")
                if definition.force_enabled and definition.force_disabled:
                    raise ToolDescriptorRegistryError(
                        f"tool {name} 不能同时 force_enabled 与 force_disabled"
                    )
                if definition.risk_level not in {"low", "medium", "high"}:
                    raise ToolDescriptorRegistryError(
                        f"tool {name} risk_level 不支持: {definition.risk_level}"
                    )
                if definition.category not in {
                    "communication",
                    "data",
                    "analysis",
                    "system",
                    "file",
                }:
                    raise ToolDescriptorRegistryError(
                        f"tool {name} category 不支持: {definition.category}"
                    )
                if definition.effect_policy not in {
                    "read_only",
                    "local_write",
                    "external",
                }:
                    raise ToolDescriptorRegistryError(
                        f"tool {name} effect_policy 不支持: "
                        f"{definition.effect_policy}"
                    )
                prompt_keys = _derive_prompt_template_keys(definition)
                expected_prefix = f"tools/{name}/"
                invalid_prompt_keys = [
                    item for item in prompt_keys if not item.startswith(expected_prefix)
                ]
                if invalid_prompt_keys:
                    raise ToolDescriptorRegistryError(
                        f"tool {name} prompt template 不属于自身命名空间: "
                        + ", ".join(invalid_prompt_keys)
                    )
                if len(set(prompt_keys)) != len(prompt_keys):
                    raise ToolDescriptorRegistryError(
                        f"tool {name} prompt template 重复登记"
                    )
                if framework_owned:
                    availability: ToolAvailabilityPolicy = "framework_only"
                elif definition.force_disabled:
                    availability = "force_disabled"
                elif definition.force_enabled:
                    availability = "force_enabled"
                else:
                    availability = "configurable"
                descriptors[name] = ToolDescriptor(
                    name=name,
                    definition=definition,
                    availability_policy=availability,
                    execution_policy=(
                        "foreground_or_background"
                        if definition.supports_background
                        else "foreground_only"
                    ),
                    trace_policy=(
                        "metadata_only" if name in SANDBOX_TOOL_NAMES else "standard"
                    ),
                    prompt_template_keys=prompt_keys,
                    prompt_source_precedence=(
                        ("runtime", "default") if prompt_keys else ()
                    ),
                    prompt_editable=bool(prompt_keys)
                    and availability != "force_disabled"
                    and not framework_owned,
                    owner_module=(
                        "core.sandbox"
                        if name in SANDBOX_TOOL_NAMES
                        else "core.tool_registry"
                    ),
                    domain=f"tool.{definition.category}",
                    effect_policy=definition.effect_policy,
                    framework_owned=framework_owned,
                )
        return cls(descriptors)

    def get(self, name: str) -> ToolDescriptor | None:
        return self._descriptors.get(str(name or "").strip())

    def values(self) -> tuple[ToolDescriptor, ...]:
        return tuple(self._registry_snapshot)

    def snapshot(self) -> Mapping[str, ToolDescriptor]:
        return self._descriptors


SANDBOX_TOOL_NAMES = frozenset({
    "sandbox_exec",
    "sandbox_poll",
    "sandbox_write_stdin",
    "sandbox_terminate",
    "workspace_list",
    "workspace_read",
    "workspace_search",
    "workspace_write",
    "workspace_edit",
    "workspace_apply_patch",
    "asset_import",
    "asset_publish",
})

LEGACY_FILE_TOOL_NAMES = frozenset({
    "bash",
    "read",
    "write",
    "edit",
    "grep",
    "glob",
    "memory_read",
    "memory_write",
})


def _derive_prompt_template_keys(definition: ToolDef) -> tuple[str, ...]:
    if definition.prompt_template_keys is not None:
        return tuple(definition.prompt_template_keys)
    return (f"tools/{definition.name}/usage",)


TOOL_METADATA: Mapping[str, ToolDef] = MappingProxyType({
    # ── 通讯工具 ──
    "reply": ToolDef(
        name="reply", label="回复", category="communication", risk_level="low",
        private_default=True, group_default=True,
        description="生成最终用户可见回复。调用后系统把回复发送给用户。",
        force_enabled=True,
        effect_policy="local_write",
    ),
    "no_reply": ToolDef(
        name="no_reply", label="主动不回复", category="communication", risk_level="low",
        private_default=True, group_default=True,
        description="主动决定不回复当前消息。和reply()互斥。",
        force_enabled=True,
        effect_policy="local_write",
    ),
    "sticker_search": ToolDef(
        name="sticker_search", label="表情包搜索", category="communication", risk_level="low",
        private_default=True, group_default=True,
        description="从表情库搜索匹配表情包。",
    ),
    "image_generation": ToolDef(
        name="image_generation", label="图片生成", category="communication", risk_level="medium",
        private_default=True, group_default=True,
        description="按用户明确要求生成新图片，返回可发送的 CQ 图片码。",
        effect_policy="external",
    ),

    # ── 数据分析 ──
    "sql_analysis": ToolDef(
        name="sql_analysis", label="数据库查询", category="data", risk_level="medium",
        private_default=True, group_default=True,
        description="查询聊天记录数据库（只读SELECT），包括上一句、历史发言、会话日志和统计。",
    ),
    "python_sandbox": ToolDef(
        name="python_sandbox", label="Python沙箱", category="data", risk_level="high",
        private_default=False, group_default=False,
        description="任意 Python 执行已安全禁用；复杂分析需先通过只读 SQL 获取有界数据。",
        force_disabled=True,
    ),
    "ai_daily": ToolDef(
        name="ai_daily", label="AI日报", category="data", risk_level="low",
        private_default=True, group_default=True,
        description="聚合 AI/科技可信来源，生成日报/简报。",
        prompt_template_keys=(
            "tools/ai_daily/usage",
            "tools/ai_daily/digest_system",
            "tools/ai_daily/digest_user",
            "tools/ai_daily/quality_system",
            "tools/ai_daily/quality_user",
        ),
    ),
    "memory_query": ToolDef(
        name="memory_query", label="摘要记忆查询", category="data", risk_level="low",
        private_default=True, group_default=True,
        description=(
            "查询已生成的结构化摘要和召回卡片；不返回原始 ChatLog 全文。"
            "当前短期窗口或未摘要消息必须用 sql_analysis 查询原始日志，不要用本工具判断刚才发生的事。"
        ),
    ),
    "knowledge_query": ToolDef(
        name="knowledge_query", label="外部知识库查询", category="data", risk_level="low",
        private_default=True, group_default=True,
        description=(
            "查询已入库的外部知识库，只返回带 citation 的结果。"
            "今天、刚刚、实时资讯仍优先用 ai_daily。"
        ),
    ),
    "web_search": ToolDef(
        name="web_search", label="网页搜索", category="data", risk_level="low",
        private_default=True, group_default=True,
        description=(
            "调用管理后台配置的 Web Search provider 搜索外部网页，返回标题、URL 和摘要。"
            "适合需要最新网页资料、官方文档、公告或产品信息的问题。"
        ),
    ),

    # ── 分析工具 ──
    "image_summary": ToolDef(
        name="image_summary", label="图片描述", category="analysis", risk_level="low",
        private_default=True, group_default=True,
        description="OCR/图片内容分析，生成结构化摘要。",
        prompt_template_keys=(
            "tools/image_summary/usage",
            "tools/image_summary/system",
            "tools/image_summary/user",
        ),
    ),
    "group_analysis": ToolDef(
        name="group_analysis", label="群聊分析", category="analysis", risk_level="medium",
        private_default=False, group_default=True,
        description="分析目标群聊近期内容；可直接传群号、群名、session_id 或 stream_id，无需先查 SQL。",
        prompt_template_keys=(
            "tools/group_analysis/usage",
            "tools/group_analysis/system",
            "tools/group_analysis/topics",
            "tools/group_analysis/titles",
            "tools/group_analysis/quotes",
            "tools/group_analysis/quality",
        ),
    ),

    # ── 系统工具 ──
    "persona_update": ToolDef(
        name="persona_update", label="画像更新", category="system", risk_level="medium",
        private_default=True, group_default=True,
        description="仅触发当前用户的整体画像提取与刷新；无参数，不操作单条事实。",
        effect_policy="local_write",
    ),
    "schedule_task": ToolDef(
        name="schedule_task", label="定时任务", category="system", risk_level="medium",
        private_default=True, group_default=True,
        description="创建/管理当前会话 owner 的定时推送任务；cron 使用 Asia/Shanghai。",
        effect_policy="local_write",
    ),
    "memory_read": ToolDef(
        name="memory_read", label="记忆读取 (subagent)", category="system", risk_level="low",
        private_default=False, group_default=False,
        description="旧 KT 记忆子代理的路径隔离不足，已安全禁用；结构化摘要查询使用 memory_query。",
        force_disabled=True,
        prompt_template_keys=(),
    ),
    "memory_write": ToolDef(
        name="memory_write", label="记忆写入 (subagent)", category="system", risk_level="low",
        private_default=False, group_default=False,
        description="旧 KT 记忆子代理可写通用宿主工作目录，路径隔离不足，已安全禁用。",
        force_disabled=True,
        prompt_template_keys=(),
    ),

    # ── 持久 Workspace 与受控 Sandbox ──
    "sandbox_exec": ToolDef(
        name="sandbox_exec", label="Sandbox 执行", category="file", risk_level="high",
        private_default=False, group_default=False,
        description="在当前授权 Profile 的固定镜像中执行命令，只能访问当前 Workspace 和已授权输入资产。",
        supports_background=False,
        effect_policy="external",
    ),
    "sandbox_poll": ToolDef(
        name="sandbox_poll", label="Sandbox 进程轮询", category="file", risk_level="high",
        private_default=False, group_default=False,
        description="读取当前会话已授权 Lease 进程的增量输出和状态。",
        supports_background=False,
    ),
    "sandbox_write_stdin": ToolDef(
        name="sandbox_write_stdin", label="Sandbox stdin", category="file", risk_level="high",
        private_default=False, group_default=False,
        description="向当前会话已授权且仍在运行的 Lease 进程写入标准输入。",
        supports_background=False,
        effect_policy="external",
    ),
    "sandbox_terminate": ToolDef(
        name="sandbox_terminate", label="Sandbox 终止", category="file", risk_level="high",
        private_default=False, group_default=False,
        description="按进程句柄回收其所属 Lease，并终止该 Lease 内全部活动进程。",
        supports_background=False,
        effect_policy="external",
    ),
    "workspace_list": ToolDef(
        name="workspace_list", label="工作区列表", category="file", risk_level="low",
        private_default=False, group_default=False,
        description="workspace_search 的内部兼容旧名；不再向模型注册。",
        supports_background=False,
        force_disabled=True,
        prompt_template_keys=(),
    ),
    "workspace_read": ToolDef(
        name="workspace_read", label="工作区读取", category="file", risk_level="low",
        private_default=False, group_default=False,
        description="按行有界读取当前持久 Workspace 的 UTF-8 文本并返回稳定行号。",
        supports_background=False,
    ),
    "workspace_search": ToolDef(
        name="workspace_search", label="工作区搜索", category="file", risk_level="low",
        private_default=False, group_default=False,
        description="在当前持久 Workspace 中执行有界正则内容搜索、文件查找或目录树浏览。",
        supports_background=False,
    ),
    "workspace_write": ToolDef(
        name="workspace_write", label="工作区写入", category="file", risk_level="medium",
        private_default=False, group_default=False,
        description="向当前持久 Workspace 原子写入小文本文件。",
        supports_background=False,
        effect_policy="local_write",
    ),
    "workspace_edit": ToolDef(
        name="workspace_edit", label="工作区编辑", category="file", risk_level="medium",
        private_default=False, group_default=False,
        description="对当前持久 Workspace 原子执行精确替换或多文件 unified diff。",
        supports_background=False,
        effect_policy="local_write",
    ),
    "workspace_apply_patch": ToolDef(
        name="workspace_apply_patch", label="工作区补丁", category="file", risk_level="medium",
        private_default=False, group_default=False,
        description="workspace_edit 的内部兼容旧名；不再向模型注册。",
        supports_background=False,
        force_disabled=True,
        prompt_template_keys=(),
        effect_policy="local_write",
    ),
    "asset_import": ToolDef(
        name="asset_import", label="资产导入", category="file", risk_level="medium",
        private_default=False, group_default=False,
        description="把当前附件引用或已经授权的不可变资产链接到当前 Workspace。",
        supports_background=False,
        effect_policy="local_write",
    ),
    "asset_publish": ToolDef(
        name="asset_publish", label="资产发布", category="file", risk_level="medium",
        private_default=False, group_default=False,
        description="把当前 Workspace 中的普通文件发布为不可变资产并返回短引用。",
        supports_background=False,
        effect_policy="local_write",
    ),

    # ── 文件操作 ──
    "read": ToolDef(
        name="read", label="读取文件", category="file", risk_level="low",
        private_default=True, group_default=False,
        description="旧 KT 宿主文件读取入口，已由 workspace_read 替代。",
        force_disabled=True,
        prompt_template_keys=(),
    ),
    "write": ToolDef(
        name="write", label="写入文件", category="file", risk_level="medium",
        private_default=True, group_default=False,
        description="旧 KT 宿主文件写入入口，已由 workspace_write 替代。",
        force_disabled=True,
        force_disabled_group=True,
        prompt_template_keys=(),
    ),
    "edit": ToolDef(
        name="edit", label="编辑文件", category="file", risk_level="medium",
        private_default=True, group_default=False,
        description="旧 KT 宿主文件编辑入口，已安全禁用。",
        force_disabled=True,
        force_disabled_group=True,
        prompt_template_keys=(),
    ),
    "grep": ToolDef(
        name="grep", label="文件搜索", category="file", risk_level="low",
        private_default=True, group_default=False,
        description="旧 KT 宿主文件搜索入口，已由 workspace_search 替代。",
        force_disabled=True,
        prompt_template_keys=(),
    ),
    "glob": ToolDef(
        name="glob", label="文件查找", category="file", risk_level="low",
        private_default=True, group_default=False,
        description="旧 KT 宿主文件查找入口，已由 workspace_search 的 files/tree 模式替代。",
        force_disabled=True,
        prompt_template_keys=(),
    ),
    "bash": ToolDef(
        name="bash", label="命令行", category="file", risk_level="high",
        private_default=True, group_default=False,
        description="旧 KT 宿主命令执行入口，已由 sandbox_exec 替代。",
        force_disabled=True,
        force_disabled_group=True,
        prompt_template_keys=(),
    ),
})


# KT 自动注册但不会进入 Nanobot 用户可见 ToolPlan 的框架工具。
FRAMEWORK_TOOL_METADATA: Mapping[str, ToolDef] = MappingProxyType({
    "skill": ToolDef(
        name="skill",
        label="技能加载（框架）",
        category="system",
        risk_level="low",
        private_default=False,
        group_default=False,
        description="KT 框架按需读取过程技能；不作为 Nanobot 对话工具暴露。",
        prompt_template_keys=(),
    ),
})


TOOL_DESCRIPTOR_REGISTRY = ToolDescriptorRegistry.from_metadata(
    TOOL_METADATA,
    FRAMEWORK_TOOL_METADATA,
)


def get_tool_descriptor(name: str) -> ToolDescriptor | None:
    return TOOL_DESCRIPTOR_REGISTRY.get(name)


def list_tool_descriptors() -> tuple[ToolDescriptor, ...]:
    return TOOL_DESCRIPTOR_REGISTRY.values()


def list_user_tool_descriptors() -> tuple[ToolDescriptor, ...]:
    """返回进入 Nanobot ToolPlan 的描述符，不包含 KT 框架内部工具。"""

    return tuple(
        descriptor
        for descriptor in list_tool_descriptors()
        if not descriptor.framework_owned
    )


def validate_tool_descriptor_registry() -> None:
    """供启动期显式校验；构造新快照会重新执行全部 fail-closed 规则。"""

    ToolDescriptorRegistry.from_metadata(TOOL_METADATA, FRAMEWORK_TOOL_METADATA)


def get_tool_def(name: str) -> ToolDef | None:
    descriptor = get_tool_descriptor(name)
    return descriptor.definition if descriptor is not None else None
