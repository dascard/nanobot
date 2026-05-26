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
    force_disabled_group: bool = False    # 群聊强制禁用（bash/write/edit）


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

    # ── 数据分析 ──
    "sql_analysis": ToolDef(
        name="sql_analysis", label="数据库查询", category="data", risk_level="medium",
        private_default=True, group_default=True,
        description="查询聊天记录数据库（只读SELECT），包括上一句、历史发言、会话日志和统计。",
    ),
    "python_sandbox": ToolDef(
        name="python_sandbox", label="Python沙箱", category="data", risk_level="high",
        private_default=True, group_default=True,
        description="执行复杂数据处理/计算；简单聊天记录查询、上一句、表结构检查优先用 sql_analysis。",
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
        description="用户明确要求记住、纠正、删除或重建画像时使用；普通聊天新信息由后台画像进化处理。",
    ),
    "schedule_task": ToolDef(
        name="schedule_task", label="定时任务", category="system", risk_level="medium",
        private_default=True, group_default=True,
        description="创建/管理定时推送任务；cron 使用 Asia/Shanghai，未指定目标时尝试当前会话。",
    ),
    "memory_read": ToolDef(
        name="memory_read", label="记忆读取 (subagent)", category="system", risk_level="low",
        private_default=True, group_default=True,
        description="读取长期记忆/上下文，不用于查询 chat_logs 或 conversation_turns。注意：此工具为 subagent，运行时禁用支持有限。",
    ),
    "memory_write": ToolDef(
        name="memory_write", label="记忆写入 (subagent)", category="system", risk_level="low",
        private_default=True, group_default=True,
        description="写入长期记忆/上下文。注意：此工具为 subagent，运行时禁用支持有限。",
    ),

    # ── 文件操作 ──
    "read": ToolDef(
        name="read", label="读取文件", category="file", risk_level="low",
        private_default=True, group_default=False,
        description="读取工作区文件内容。",
    ),
    "write": ToolDef(
        name="write", label="写入文件", category="file", risk_level="medium",
        private_default=True, group_default=False,
        description="写入工作区文件。",
        force_disabled_group=True,
    ),
    "edit": ToolDef(
        name="edit", label="编辑文件", category="file", risk_level="medium",
        private_default=True, group_default=False,
        description="编辑工作区文件。",
        force_disabled_group=True,
    ),
    "grep": ToolDef(
        name="grep", label="文件搜索", category="file", risk_level="low",
        private_default=True, group_default=False,
        description="在工作区文件中搜索内容。",
    ),
    "glob": ToolDef(
        name="glob", label="文件查找", category="file", risk_level="low",
        private_default=True, group_default=False,
        description="按模式查找工作区文件。",
    ),
    "bash": ToolDef(
        name="bash", label="命令行", category="file", risk_level="high",
        private_default=True, group_default=False,
        description="执行Shell命令。高风险工具。",
        force_disabled_group=True,
    ),
}


def get_tool_def(name: str) -> ToolDef | None:
    return TOOL_METADATA.get(name)
