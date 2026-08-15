"""身份与权限辅助——超级用户判断、动态名字变量。"""

from __future__ import annotations

from config import (
    NANOBOT_BOT_ALIASES,
    NANOBOT_CHARACTER_NAME,
    NANOBOT_SUPER_USER_IDS,
)


def _parse_text_set(raw: object) -> set[str]:
    return {
        item.strip()
        for item in str(raw or "").replace("，", ",").replace("\n", ",").split(",")
        if item.strip()
    }


def _parse_text_list(raw: object) -> list[str]:
    return [
        item.strip()
        for item in str(raw or "").replace("，", ",").replace("\n", ",").split(",")
        if item.strip()
    ]


def _setting_str(key: str, default: str = "") -> str:
    try:
        from core.settings_service import settings

        return settings.get_str(key, default)
    except Exception:
        return default


def _configured_character_name() -> str:
    return (
        _setting_str("bot.character_name", NANOBOT_CHARACTER_NAME)
        or NANOBOT_CHARACTER_NAME
        or "nanobot"
    ).strip()


def _configured_aliases() -> list[str]:
    raw = _setting_str("bot.alias_names", ",".join(sorted(NANOBOT_BOT_ALIASES)))
    aliases = _parse_text_list(raw)
    return aliases or sorted(NANOBOT_BOT_ALIASES)


def get_super_user_ids() -> set[str]:
    """返回启动时从唯一环境变量解析的超级用户集合副本。"""

    return set(NANOBOT_SUPER_USER_IDS)


def normalize_user_id(value: object) -> str:
    return str(value or "").strip()


def is_super_user_id(user_id: object) -> bool:
    uid = normalize_user_id(user_id)
    return bool(uid and uid in get_super_user_ids())


def build_identity_vars(
    *,
    sender_id: object = "",
    bot_name: object = "",
    bot_aliases: object = None,
    is_super_user: bool | None = None,
) -> dict[str, str]:
    normalized_sender_id = normalize_user_id(sender_id)
    authorization_fact = (
        is_super_user_id(normalized_sender_id)
        if is_super_user is None
        else is_super_user is True
    )
    aliases = bot_aliases if bot_aliases else _configured_aliases()
    if isinstance(aliases, (list, tuple, set)):
        normalized_aliases = list(dict.fromkeys(
            str(item).strip()
            for item in aliases
            if str(item).strip()
        ))
        # 显式请求列表可能来自无序 JSON；配置中心返回的列表则保留管理员顺序。
        if bot_aliases:
            normalized_aliases.sort(key=lambda item: (item.casefold(), item))
        alias_text = "\n".join(normalized_aliases)
    else:
        alias_text = str(aliases or "")

    name = str(bot_name or "").strip() or _configured_character_name()
    if not alias_text.strip():
        alias_text = name
    return {
        "sender_id": normalized_sender_id or "未提供",
        "is_super_user": "true" if authorization_fact else "false",
        "character_name": name,
        "name_hint": name,
        "alias_names": alias_text,
    }
