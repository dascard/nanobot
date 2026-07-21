"""工具注册表——所有工具的元数据单一来源。"""

from dataclasses import dataclass


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


SANDBOX_TOOL_NAMES = frozenset({
    "sandbox_exec",
    "workspace_list",
    "workspace_read",
    "workspace_search",
    "workspace_write",
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


TOOL_METADATA: dict[str, ToolDef] = {
    # ── 通讯工具 ──
    "reply": ToolDef(
        name="reply", label="回复", category="communication", risk_level="low",
        private_default=True, group_default=True,
        description="生成最终用户可见回复。调用后系统把回复发送给用户。",
        force_enabled=True,
    ),
    "no_reply": ToolDef(
        name="no_reply", label="主动不回复", category="communication", risk_level="low",
        private_default=True, group_default=True,
        description="主动决定不回复当前消息。和reply()互斥。",
        force_enabled=True,
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
    ),
    "group_analysis": ToolDef(
        name="group_analysis", label="群聊分析", category="analysis", risk_level="medium",
        private_default=False, group_default=True,
        description="分析目标群聊近期内容；可直接传群号、群名、session_id 或 stream_id，无需先查 SQL。",
    ),

    # ── 系统工具 ──
    "persona_update": ToolDef(
        name="persona_update", label="画像更新", category="system", risk_level="medium",
        private_default=True, group_default=True,
        description="仅触发当前用户的整体画像提取与刷新；无参数，不操作单条事实。",
    ),
    "schedule_task": ToolDef(
        name="schedule_task", label="定时任务", category="system", risk_level="medium",
        private_default=True, group_default=True,
        description="创建/管理定时推送任务；cron 使用 Asia/Shanghai，未指定目标时尝试当前会话。",
    ),
    "memory_read": ToolDef(
        name="memory_read", label="记忆读取 (subagent)", category="system", risk_level="low",
        private_default=False, group_default=False,
        description="旧 KT 记忆子代理的路径隔离不足，已安全禁用；结构化摘要查询使用 memory_query。",
        force_disabled=True,
    ),
    "memory_write": ToolDef(
        name="memory_write", label="记忆写入 (subagent)", category="system", risk_level="low",
        private_default=False, group_default=False,
        description="旧 KT 记忆子代理可写通用宿主工作目录，路径隔离不足，已安全禁用。",
        force_disabled=True,
    ),

    # ── 持久 Workspace 与一次性 Sandbox ──
    "sandbox_exec": ToolDef(
        name="sandbox_exec", label="Sandbox 执行", category="file", risk_level="high",
        private_default=False, group_default=False,
        description="在固定镜像的一次性断网容器中执行命令，只能访问当前 Workspace 和已授权输入资产。",
        supports_background=False,
    ),
    "workspace_list": ToolDef(
        name="workspace_list", label="工作区列表", category="file", risk_level="low",
        private_default=False, group_default=False,
        description="分页列出当前持久 Workspace 的相对路径和文件元数据。",
        supports_background=False,
    ),
    "workspace_read": ToolDef(
        name="workspace_read", label="工作区读取", category="file", risk_level="low",
        private_default=False, group_default=False,
        description="有界读取当前持久 Workspace 的文本文件；二进制文件只返回元数据。",
        supports_background=False,
    ),
    "workspace_search": ToolDef(
        name="workspace_search", label="工作区搜索", category="file", risk_level="low",
        private_default=False, group_default=False,
        description="在当前持久 Workspace 中执行有界字面量搜索。",
        supports_background=False,
    ),
    "workspace_write": ToolDef(
        name="workspace_write", label="工作区写入", category="file", risk_level="medium",
        private_default=False, group_default=False,
        description="向当前持久 Workspace 原子写入小文本文件。",
        supports_background=False,
    ),
    "asset_import": ToolDef(
        name="asset_import", label="资产导入", category="file", risk_level="medium",
        private_default=False, group_default=False,
        description="把当前附件引用或已经授权的不可变资产链接到当前 Workspace。",
        supports_background=False,
    ),
    "asset_publish": ToolDef(
        name="asset_publish", label="资产发布", category="file", risk_level="medium",
        private_default=False, group_default=False,
        description="把当前 Workspace 中的普通文件发布为不可变资产并返回短引用。",
        supports_background=False,
    ),

    # ── 文件操作 ──
    "read": ToolDef(
        name="read", label="读取文件", category="file", risk_level="low",
        private_default=True, group_default=False,
        description="旧 KT 宿主文件读取入口，已由 workspace_read 替代。",
        force_disabled=True,
    ),
    "write": ToolDef(
        name="write", label="写入文件", category="file", risk_level="medium",
        private_default=True, group_default=False,
        description="旧 KT 宿主文件写入入口，已由 workspace_write 替代。",
        force_disabled=True,
        force_disabled_group=True,
    ),
    "edit": ToolDef(
        name="edit", label="编辑文件", category="file", risk_level="medium",
        private_default=True, group_default=False,
        description="旧 KT 宿主文件编辑入口，已安全禁用。",
        force_disabled=True,
        force_disabled_group=True,
    ),
    "grep": ToolDef(
        name="grep", label="文件搜索", category="file", risk_level="low",
        private_default=True, group_default=False,
        description="旧 KT 宿主文件搜索入口，已由 workspace_search 替代。",
        force_disabled=True,
    ),
    "glob": ToolDef(
        name="glob", label="文件查找", category="file", risk_level="low",
        private_default=True, group_default=False,
        description="旧 KT 宿主文件查找入口，已由 workspace_list 替代。",
        force_disabled=True,
    ),
    "bash": ToolDef(
        name="bash", label="命令行", category="file", risk_level="high",
        private_default=True, group_default=False,
        description="旧 KT 宿主命令执行入口，已由 sandbox_exec 替代。",
        force_disabled=True,
        force_disabled_group=True,
    ),
}


# KT 自动注册但不会进入 Nanobot 用户可见 ToolPlan 的框架工具。
FRAMEWORK_TOOL_METADATA: dict[str, ToolDef] = {
    "skill": ToolDef(
        name="skill",
        label="技能加载（框架）",
        category="system",
        risk_level="low",
        private_default=False,
        group_default=False,
        description="KT 框架按需读取过程技能；不作为 Nanobot 对话工具暴露。",
    ),
}


def get_tool_def(name: str) -> ToolDef | None:
    return TOOL_METADATA.get(name) or FRAMEWORK_TOOL_METADATA.get(name)
