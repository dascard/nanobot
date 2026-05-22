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


_VARIABLES: dict[str, tuple[VariableDef, ...]] = {
    "identity_context": (
        VariableDef("character_name", "identity_context", "当前角色名", "七濑"),
        VariableDef("name_hint", "identity_context", "用户可能用来称呼机器人的主名称", "七濑"),
        VariableDef("alias_names", "identity_context", "用户可能用来称呼机器人的别名列表", "小七\\nbot"),
        VariableDef("sender_id", "identity_context", "当前发送者 ID", "0000000000"),
        VariableDef("super_user_id", "identity_context", "超级用户 ID 列表", "0000000000"),
        VariableDef("is_super_user", "identity_context", "当前发送者是否超级用户", "true"),
    ),
}


def normalize_scope(scope: str) -> str:
    return str(scope or "").removesuffix(".md").strip()


def list_variables(scope: str = "") -> list[dict[str, str]]:
    scope = normalize_scope(scope)
    if scope:
        return [item.to_dict() for item in _VARIABLES.get(scope, ())]
    result: list[dict[str, str]] = []
    for items in _VARIABLES.values():
        result.extend(item.to_dict() for item in items)
    return result


def allowed_variable_names(scope: str) -> set[str]:
    return {item.name for item in _VARIABLES.get(normalize_scope(scope), ())}


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
