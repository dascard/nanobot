import runpy


def test_config_uses_only_canonical_super_user_environment(monkeypatch):
    import config
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda: None)
    monkeypatch.setenv("NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setenv("NANOBOT_SUPER_USER_IDS", "1001, 1002，1003")
    legacy_names = ("SUPER" + "_USER_IDS", "ADMIN" + "_USER_ID")
    monkeypatch.setenv(legacy_names[0], "legacy-list")
    monkeypatch.setenv(legacy_names[1], "legacy-single")

    isolated = runpy.run_path(config.__file__)

    assert isolated["NANOBOT_SUPER_USER_IDS"] == {"1001", "1002", "1003"}
    assert all(name not in isolated for name in legacy_names)


def test_identity_returns_defensive_super_user_copy(monkeypatch):
    from core import identity

    monkeypatch.setattr(
        identity,
        "NANOBOT_SUPER_USER_IDS",
        {"canonical-user"},
        raising=False,
    )

    first = identity.get_super_user_ids()
    first.add("mutated")

    assert identity.get_super_user_ids() == {"canonical-user"}
    assert identity.is_super_user_id("canonical-user")
    assert not identity.is_super_user_id("mutated")


def test_guardrail_superuser_uses_shared_identity_service(monkeypatch):
    from api import routes

    calls: list[str] = []

    def fake_is_super_user_id(user_id: object) -> bool:
        calls.append(str(user_id))
        return str(user_id) == "canonical-user"

    monkeypatch.setattr(
        routes,
        "is_super_user_id",
        fake_is_super_user_id,
        raising=False,
    )

    assert routes._is_guardrail_superuser("canonical-user")
    assert not routes._is_guardrail_superuser("other-user")
    assert calls == ["canonical-user", "other-user"]
