from core.identity import normalize_user_id, is_super_user_id, build_identity_vars


def test_normalize_user_id():
    assert normalize_user_id(" 123 ") == "123"
    assert normalize_user_id(None) == ""
    assert normalize_user_id(123) == "123"


def test_is_super_user_id(monkeypatch):
    monkeypatch.setattr("core.identity.SUPER_USER_IDS", {"123", "456"})
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
