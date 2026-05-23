from __future__ import annotations

import re
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
    VariableDef("super_user_id", "global", "超级用户 ID 列表", "0000000000"),
    VariableDef("is_super_user", "global", "当前发送者是否超级用户", "true"),
    VariableDef("chat_type", "global", "当前会话类型", "group"),
    VariableDef("session_id", "global", "当前会话 ID", "group_1001"),
    VariableDef("group_id", "global", "当前群号，私聊为空", "1001"),
    VariableDef("user_id", "global", "当前用户 ID", "0000000000"),
    VariableDef("sender_name", "global", "当前发送者名称", "张三"),
    VariableDef("bot_name", "global", "机器人当前名称", "七濑"),
    VariableDef("bot_aliases", "global", "机器人别名列表", "小七\\nbot"),
    VariableDef("current_time", "global", "当前北京时间", "2026-05-23 10:30:00 CST"),
    VariableDef("timezone", "global", "当前时区", "Asia/Shanghai"),
)


def normalize_scope(scope: str) -> str:
    return str(scope or "").removesuffix(".md").strip()


def list_variables(scope: str = "") -> list[dict[str, str]]:
    return [item.to_dict() for item in _GLOBAL_VARIABLES]


def allowed_variable_names(scope: str) -> set[str]:
    return {item.name for item in _GLOBAL_VARIABLES}


def referenced_variable_names(template_text: str) -> set[str]:
    return set(_VARIABLE_PATTERN.findall(str(template_text or "")))


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
