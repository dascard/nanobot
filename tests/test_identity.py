from core.identity import normalize_user_id, is_super_user_id, build_identity_vars


def test_normalize_user_id():
    assert normalize_user_id(" 123 ") == "123"
    assert normalize_user_id(None) == ""
    assert normalize_user_id(123) == "123"


def test_is_super_user_id(monkeypatch):
    monkeypatch.setattr("core.identity.NANOBOT_SUPER_USER_IDS", {"123", "456"})
    assert is_super_user_id("123")
    assert is_super_user_id("456")
    assert not is_super_user_id("789")
    assert not is_super_user_id("")
    assert not is_super_user_id(None)


def test_build_identity_vars():
    vars_ = build_identity_vars(sender_id="123", bot_name="testbot", bot_aliases=["bot", "机器人"])
    assert vars_["sender_id"] == "123"
    assert vars_["character_name"] == "testbot"
    assert vars_["name_hint"] == "testbot"
    assert "bot" in vars_["alias_names"]
    assert "机器人" in vars_["alias_names"]


def test_build_identity_vars_has_non_empty_defaults(monkeypatch):
    monkeypatch.setattr("core.identity.NANOBOT_CHARACTER_NAME", "nanobot")
    monkeypatch.setattr("core.identity.NANOBOT_BOT_ALIASES", {"nanobot"})
    monkeypatch.setattr("core.identity.NANOBOT_SUPER_USER_IDS", set())

    vars_ = build_identity_vars()

    assert vars_["sender_id"] == "未提供"
    assert vars_["character_name"] == "nanobot"
    assert vars_["name_hint"] == "nanobot"
    assert vars_["alias_names"] == "nanobot"
    assert "super_user_id" not in vars_


def test_build_identity_vars_reads_identity_without_exposing_super_user_ids(monkeypatch):
    configured = {
        "bot.character_name": "七濑",
        "bot.alias_names": "小七\nnanobot",
    }
    monkeypatch.setattr(
        "core.settings_service.settings.get_str",
        lambda key, default="": configured.get(key, default),
    )
    monkeypatch.setattr("core.identity.NANOBOT_CHARACTER_NAME", "fallback")
    monkeypatch.setattr("core.identity.NANOBOT_BOT_ALIASES", {"fallback"})
    monkeypatch.setattr("core.identity.NANOBOT_SUPER_USER_IDS", {"42", "99"})

    vars_ = build_identity_vars(sender_id="42")

    assert vars_["character_name"] == "七濑"
    assert vars_["name_hint"] == "七濑"
    assert vars_["alias_names"] == "小七\nnanobot"
    assert "super_user_id" not in vars_
    assert vars_["is_super_user"] == "true"
    assert is_super_user_id("99")


def test_build_identity_vars_uses_explicit_authorization_fact(monkeypatch):
    monkeypatch.setattr("core.identity.is_super_user_id", lambda _value: False)

    explicit_true = build_identity_vars(
        sender_id="placeholder-user",
        is_super_user=True,
    )

    monkeypatch.setattr("core.identity.is_super_user_id", lambda _value: True)
    explicit_false = build_identity_vars(
        sender_id="placeholder-user",
        is_super_user=False,
    )

    assert explicit_true["is_super_user"] == "true"
    assert explicit_false["is_super_user"] == "false"
