from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import asdict, dataclass
from typing import Any


class PromptVariableError(ValueError):
    """V2 模板变量校验失败。"""


@dataclass(frozen=True)
class VariableDef:
    name: str
    scope: str
    description: str
    example: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


_VARIABLE_PATTERN = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")


_GLOBAL_VARIABLES: tuple[VariableDef, ...] = (
    VariableDef("character_name", "global", "当前角色名", "七濑"),
    VariableDef("name_hint", "global", "用户可能用来称呼机器人的主名称", "七濑"),
    VariableDef("alias_names", "global", "用户可能用来称呼机器人的别名列表", "小七\\nbot"),
    VariableDef("sender_id", "global", "当前发送者 ID", "0000000000"),
    VariableDef("is_super_user", "global", "当前发送者是否超级用户", "true"),
    VariableDef("chat_type", "global", "当前会话类型", "group"),
    VariableDef("platform", "global", "当前客户端平台", "qq"),
    VariableDef("session_id", "global", "当前会话 ID", "group_1001"),
    VariableDef("group_id", "global", "当前群号，私聊为空", "1001"),
    VariableDef("user_id", "global", "当前用户 ID", "0000000000"),
    VariableDef("sender_name", "global", "当前发送者名称", "张三"),
    VariableDef("bot_name", "global", "机器人当前名称", "七濑"),
    VariableDef("bot_aliases", "global", "机器人别名列表", "小七\\nbot"),
    VariableDef("current_time", "global", "当前北京时间", "2026-05-23 10:30:00 CST"),
    VariableDef("timezone", "global", "当前时区", "Asia/Shanghai"),
    VariableDef("messages_text", "global", "工具运行时注入的消息文本", "[12:00] [10001]: 示例消息"),
    VariableDef("style_messages_text", "global", "工具运行时注入的风格参考消息文本", "[12:00] [10001]: 示例消息"),
    VariableDef("users_text", "global", "工具运行时注入的用户统计文本", "10001 | 5 | 24.0 | 0.10 | 0.20"),
    VariableDef("instructions", "global", "工具运行时注入的用户分析指引", "只看最近 2 小时"),
    VariableDef("evidence_cards", "global", "工具运行时注入的证据卡片文本", "### 来源 #1\n标题: 示例"),
    VariableDef("candidate_cards", "global", "工具运行时注入的候选卡片文本", "### 来源 #1\n标题: 示例"),
    VariableDef("mode_hint", "global", "工具运行时注入的模式说明", "生成 2-3 条 highlights"),
    VariableDef("card_count", "global", "工具运行时注入的卡片数量", "8"),
    VariableDef("image_count", "global", "工具运行时注入的图片数量", "2"),
    VariableDef("focus", "global", "工具运行时注入的图片摘要重点", "OCR"),
)

_MEMORY_DIGEST_VARIABLES: tuple[VariableDef, ...] = (
    VariableDef("date", "memory_digest", "摘要 source 日期", "2026-06-01"),
    VariableDef("source_id", "memory_digest", "摘要 source 稳定 ID", "20260601_group_1001_1_20_v2"),
    VariableDef("source_type", "memory_digest", "摘要 source 类型", "date_session"),
    VariableDef("source_range", "memory_digest", "摘要 source 覆盖范围", "log_id 1-20"),
    VariableDef("message_count", "memory_digest", "摘要 source 消息数量", "18"),
    VariableDef("digest_source", "memory_digest", "清洗后的长期摘要输入文本", "[log_id=1] 示例消息"),
    VariableDef("existing_digest_hint", "memory_digest", "规则摘要或已有摘要提示", "已有主题提示"),
)

_CLASSIFIER_TASK_VARIABLES: tuple[VariableDef, ...] = (
    VariableDef("message", "classifier_task", "待判定消息", "ping"),
    VariableDef("system_prompt", "classifier_task", "调用方旧系统提示", "只输出 JSON"),
    VariableDef("pending_text", "classifier_task", "待判定群聊文本", "ping"),
    VariableDef("recent_context", "classifier_task", "近期上下文", "上一句"),
    VariableDef("bot_name", "classifier_task", "机器人名称", "七濑"),
    VariableDef("group_profile", "classifier_task", "群体画像", "技术群"),
)

_MEMORY_EXTRACT_VARIABLES: tuple[VariableDef, ...] = (
    VariableDef("conversation", "memory_extract", "待抽取的对话文本", "用户: 喜欢 TypeScript"),
    VariableDef("existing_memory", "memory_extract", "已有用户记忆", "{}"),
)


def normalize_scope(scope: str) -> str:
    return str(scope or "").removesuffix(".md").strip()


def _scoped_variables(scope: str) -> tuple[VariableDef, ...]:
    normalized = normalize_scope(scope)
    if normalized in {"tasks/memory_digest_system", "tasks/memory_digest_user"}:
        return _MEMORY_DIGEST_VARIABLES
    if normalized in {
        "tasks/classifier_legacy",
        "tasks/private_decision",
        "tasks/timing_gate",
        "tasks/timing_proactive",
        "tasks/outreach_extract",
        "tasks/outreach_judge",
        "tasks/outreach_generate",
        "tasks/proactive_research",
    }:
        return _CLASSIFIER_TASK_VARIABLES
    if normalized == "tasks/memory_extract":
        return _MEMORY_EXTRACT_VARIABLES
    return ()


def list_variables(scope: str = "") -> list[dict[str, str]]:
    return [item.to_dict() for item in (*_GLOBAL_VARIABLES, *_scoped_variables(scope))]


def allowed_variable_names(scope: str) -> set[str]:
    return {item.name for item in (*_GLOBAL_VARIABLES, *_scoped_variables(scope))}


def referenced_variable_names(template_text: str) -> set[str]:
    return set(_VARIABLE_PATTERN.findall(str(template_text or "")))


def is_empty_task_call_value(value: Any) -> bool:
    """判断 task 动态调用值是否缺少可用内容。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, Collection):
        return len(value) == 0
    return False


def validate_scoped_template(scope: str, template_text: str) -> None:
    referenced = referenced_variable_names(template_text)
    allowed = allowed_variable_names(scope)
    disallowed = sorted(referenced - allowed)
    if disallowed:
        raise PromptVariableError(
            f"template {normalize_scope(scope)!r} contains unsupported variables: "
            + ", ".join(disallowed)
        )


def render_scoped_template(scope: str, template_text: str, values: dict[str, Any]) -> str:
    validate_scoped_template(scope, template_text)

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(values.get(name, "") or "")

    return _VARIABLE_PATTERN.sub(replace, str(template_text or ""))
