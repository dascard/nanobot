"""身份与权限辅助——超级用户判断、动态名字变量。"""

from __future__ import annotations

from config import NANOBOT_BOT_ALIASES, NANOBOT_CHARACTER_NAME, SUPER_USER_IDS


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


def _configured_super_user_ids() -> set[str]:
    raw = _setting_str("bot.super_user_ids", ",".join(sorted(SUPER_USER_IDS)))
    ids = _parse_text_set(raw)
    return ids if str(raw or "").strip() else set(SUPER_USER_IDS)


def normalize_user_id(value: object) -> str:
    return str(value or "").strip()


def is_super_user_id(user_id: object) -> bool:
    uid = normalize_user_id(user_id)
    return bool(uid and uid in _configured_super_user_ids())


def build_identity_vars(
    *,
    sender_id: object = "",
    bot_name: object = "",
    bot_aliases: object = None,
) -> dict[str, str]:
    normalized_sender_id = normalize_user_id(sender_id)
    aliases = bot_aliases or _configured_aliases()
    if isinstance(aliases, (list, tuple, set)):
        alias_text = "\n".join(str(x) for x in aliases if str(x).strip())
    else:
        alias_text = str(aliases or "")

    name = str(bot_name or "").strip() or _configured_character_name()
    if not alias_text.strip():
        alias_text = name
    super_user_text = ",".join(sorted(_configured_super_user_ids())) or "未配置"

    return {
        "sender_id": normalized_sender_id or "未提供",
        "is_super_user": "true" if is_super_user_id(normalized_sender_id) else "false",
        "character_name": name,
        "name_hint": name,
        "alias_names": alias_text,
        "super_user_id": super_user_text,
    }
