"""身份与权限辅助——超级用户判断、动态名字变量。"""

from __future__ import annotations

from config import SUPER_USER_IDS


def normalize_user_id(value: object) -> str:
    return str(value or "").strip()


def is_super_user_id(user_id: object) -> bool:
    uid = normalize_user_id(user_id)
    return bool(uid and uid in SUPER_USER_IDS)


def build_identity_vars(
    *,
    sender_id: object = "",
    bot_name: object = "",
    bot_aliases: object = None,
) -> dict[str, str]:
    aliases = bot_aliases or []
    if isinstance(aliases, (list, tuple, set)):
        alias_text = "\n".join(str(x) for x in aliases if str(x).strip())
    else:
        alias_text = str(aliases or "")

    name = str(bot_name or "").strip()

    return {
        "sender_id": normalize_user_id(sender_id),
        "is_super_user": "true" if is_super_user_id(sender_id) else "false",
        "character_name": name,
        "name_hint": name,
        "alias_names": alias_text,
        "super_user_id": ",".join(sorted(SUPER_USER_IDS)),
    }
