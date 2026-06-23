from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class Field:
    def __init__(self, name: str):
        self.name = name

    def __eq__(self, value: Any):  # type: ignore[override]
        return (self.name, value)


class FakeRuleModel:
    user_id = Field("user_id")
    enabled = Field("enabled")


@dataclass
class FakeRule:
    user_id: str
    target_type: str = "private"
    group_id: str = ""
    enabled: int = 1
    rule_mode: str = "log_only"
    reason: str = ""


class FakeQuery:
    def __init__(self, rows: list[FakeRule]):
        self._rows = rows
        self._filters: tuple[Any, ...] = ()

    def filter(self, *conditions: Any):
        self._filters = conditions
        return self

    def all(self) -> list[FakeRule]:
        filters = dict(self._filters)
        return [
            row
            for row in self._rows
            if row.user_id == filters.get("user_id")
            and row.enabled == filters.get("enabled")
        ]


class FakeDb:
    def __init__(self, rows: list[FakeRule]):
        self.rows = rows
        self.model = None

    def query(self, model: Any) -> FakeQuery:
        self.model = model
        return FakeQuery(self.rows)


def test_user_block_rules_module_does_not_import_entrypoints_or_sync_awaitable():
    source = _source("core/user_block_rules.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "from app.group_ingress.helpers" not in source
    assert "import app.group_ingress.helpers" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_private_rule_matches_private_target():
    from core.user_block_rules import is_user_blocked

    db = FakeDb([FakeRule(user_id="u1", target_type="private")])

    assert is_user_blocked(db, "u1", target_type="private", rule_model=FakeRuleModel)
    assert db.model is FakeRuleModel


def test_all_rule_matches_private_and_group_targets():
    from core.user_block_rules import is_user_blocked

    db = FakeDb([FakeRule(user_id="u1", target_type="all", group_id="group_999")])

    assert is_user_blocked(db, "u1", target_type="private", rule_model=FakeRuleModel)
    assert is_user_blocked(
        db,
        "u1",
        target_type="group",
        group_id="123",
        rule_model=FakeRuleModel,
    )


def test_group_rule_matches_normalized_group_id_formats():
    from core.user_block_rules import is_user_blocked

    db = FakeDb([FakeRule(user_id="u1", target_type="group", group_id="group_123")])

    assert is_user_blocked(
        db,
        "u1",
        target_type="group",
        group_id="qq:123:group",
        rule_model=FakeRuleModel,
    )


def test_group_rule_with_group_id_mismatch_does_not_match():
    from core.user_block_rules import is_user_blocked

    db = FakeDb([FakeRule(user_id="u1", target_type="group", group_id="group_123")])

    assert not is_user_blocked(
        db,
        "u1",
        target_type="group",
        group_id="456",
        rule_model=FakeRuleModel,
    )


def test_group_rule_with_group_id_and_missing_request_group_keeps_legacy_match():
    from core.user_block_rules import is_user_blocked

    db = FakeDb([FakeRule(user_id="u1", target_type="group", group_id="group_123")])

    assert is_user_blocked(db, "u1", target_type="group", rule_model=FakeRuleModel)


def test_group_rule_without_group_id_matches_any_group():
    from core.user_block_rules import is_user_blocked

    db = FakeDb([FakeRule(user_id="u1", target_type="group", group_id="")])

    assert is_user_blocked(
        db,
        "u1",
        target_type="group",
        group_id="group_999",
        rule_model=FakeRuleModel,
    )


def test_disabled_rule_is_ignored():
    from core.user_block_rules import is_user_blocked

    db = FakeDb([FakeRule(user_id="u1", target_type="all", enabled=0)])

    assert not is_user_blocked(db, "u1", target_type="private", rule_model=FakeRuleModel)


def test_rule_mode_and_reason_do_not_affect_matching():
    from core.user_block_rules import is_user_blocked

    db = FakeDb([
        FakeRule(
            user_id="u1",
            target_type="private",
            rule_mode="unknown",
            reason="任何原因",
        )
    ])

    assert is_user_blocked(db, "u1", target_type="private", rule_model=FakeRuleModel)


def test_api_routes_wrapper_delegates_and_fails_open_on_exception(monkeypatch):
    from api import routes

    calls = []

    def fake_is_user_blocked(db, user_id, **kwargs):
        calls.append((db, user_id, kwargs))
        return True

    monkeypatch.setattr("core.user_block_rules.is_user_blocked", fake_is_user_blocked)

    db = object()
    assert routes._check_user_blocked(db, "u1", target_type="group", group_id="123")
    assert routes._check_user_blocked.__module__ == "api.routes"
    assert calls == [
        (db, "u1", {"target_type": "group", "group_id": "123"}),
    ]

    def raise_error(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("core.user_block_rules.is_user_blocked", raise_error)

    assert not routes._check_user_blocked(db, "u1", target_type="private")


def test_group_ingress_wrapper_delegates_and_fails_open_on_exception(monkeypatch):
    from app.group_ingress import helpers

    calls = []

    def fake_is_user_blocked(db, user_id, **kwargs):
        calls.append((db, user_id, kwargs))
        return True

    monkeypatch.setattr("core.user_block_rules.is_user_blocked", fake_is_user_blocked)

    db = object()
    assert helpers.check_user_blocked(db, "u1", target_type="group", group_id="123")
    assert helpers.check_user_blocked.__module__ == "app.group_ingress.helpers"
    assert calls == [
        (db, "u1", {"target_type": "group", "group_id": "123"}),
    ]

    def raise_error(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("core.user_block_rules.is_user_blocked", raise_error)

    assert not helpers.check_user_blocked(db, "u1", target_type="group", group_id="123")
