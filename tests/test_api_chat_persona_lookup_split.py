from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class FakeField:
    def __eq__(self, other: object) -> tuple[str, object]:
        return ("user_id", other)


class FakePersonaModel:
    user_id = FakeField()


class FakeQuery:
    def __init__(self, db: "FakeDb") -> None:
        self.db = db
        self.current_user_id = ""

    def filter(self, expression: tuple[str, object]) -> "FakeQuery":
        assert expression[0] == "user_id"
        self.current_user_id = str(expression[1])
        self.db.queries.append(self.current_user_id)
        return self

    def first(self) -> Any | None:
        value = self.db.rows.get(self.current_user_id)
        if value is None:
            return None
        return SimpleNamespace(
            user_id=self.current_user_id,
            persona_json=value,
            status=self.db.statuses.get(self.current_user_id, "active"),
        )


class FakeDb:
    def __init__(
        self,
        rows: dict[str, str],
        statuses: dict[str, str] | None = None,
    ) -> None:
        self.rows = rows
        self.statuses = statuses or {}
        self.queries: list[str] = []

    def query(self, model: Any) -> FakeQuery:
        assert model is FakePersonaModel
        return FakeQuery(self)


def test_chat_persona_lookup_module_does_not_import_parent_routes_or_runtime_side_effects():
    path = ROOT / "api/chat_persona_lookup.py"
    assert path.exists()
    source = _source("api/chat_persona_lookup.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "FastAPI" not in source
    assert "APIRouter" not in source
    assert "StreamingResponse" not in source
    assert "BackgroundTasks" not in source
    assert "HTTPException" not in source
    assert "get_bridge(" not in source
    assert "build_chat_runtime_payload" not in source
    assert "_persist_chat_turn" not in source
    assert "_chat_response_payload" not in source
    assert "db.commit(" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_iter_persona_user_id_candidates_preserves_legacy_order_and_dedupes():
    from api.chat_persona_lookup import iter_persona_user_id_candidates

    assert iter_persona_user_id_candidates("u1") == ["u1", "private_u1", "group_u1"]
    assert iter_persona_user_id_candidates("private_u1") == ["private_u1", "group_private_u1", "u1"]
    assert iter_persona_user_id_candidates("group_u1") == ["group_u1", "private_group_u1", "u1"]
    assert iter_persona_user_id_candidates("") == ["", "private_", "group_"]


def test_resolve_persona_snapshot_uses_first_matching_candidate_and_formatter():
    from api.chat_persona_lookup import resolve_chat_persona_snapshot

    formatter_calls: list[dict[str, Any]] = []

    def formatter(data: dict[str, Any]) -> str:
        formatter_calls.append(data)
        return f"persona:{data['persona_summary']}"

    db = FakeDb({"private_u1": json.dumps({"persona_summary": "fallback"}, ensure_ascii=False)})

    snapshot = resolve_chat_persona_snapshot(
        db,
        "u1",
        persona_model=FakePersonaModel,
        format_persona=formatter,
    )

    assert db.queries == ["u1", "private_u1"]
    assert snapshot.persona_obj is not None
    assert snapshot.lookup_user_id == "u1"
    assert snapshot.matched_user_id == "private_u1"
    assert snapshot.candidate_count == 3
    assert snapshot.persona_data == {"persona_summary": "fallback"}
    assert snapshot.persona_text == "persona:fallback"
    assert formatter_calls == [{"persona_summary": "fallback"}]


def test_resolve_persona_snapshot_skips_archived_candidate():
    from api.chat_persona_lookup import resolve_chat_persona_snapshot

    db = FakeDb(
        {
            "u1": json.dumps({"persona_summary": "archived"}),
            "private_u1": json.dumps({"persona_summary": "active"}),
        },
        statuses={"u1": "archived"},
    )

    snapshot = resolve_chat_persona_snapshot(
        db,
        "u1",
        persona_model=FakePersonaModel,
        format_persona=lambda data: str(data.get("persona_summary") or ""),
    )

    assert db.queries == ["u1", "private_u1"]
    assert snapshot.matched_user_id == "private_u1"
    assert snapshot.persona_text == "active"


def test_resolve_persona_snapshot_falls_back_to_empty_data_for_missing_or_invalid_json():
    from api.chat_persona_lookup import resolve_chat_persona_snapshot

    formatter_payloads: list[dict[str, Any]] = []

    def formatter(data: dict[str, Any]) -> str:
        formatter_payloads.append(data)
        return "empty" if not data else "unexpected"

    missing = resolve_chat_persona_snapshot(
        FakeDb({}),
        "missing",
        persona_model=FakePersonaModel,
        format_persona=formatter,
    )
    invalid = resolve_chat_persona_snapshot(
        FakeDb({"u-invalid": "not json"}),
        "u-invalid",
        persona_model=FakePersonaModel,
        format_persona=formatter,
    )
    array_value = resolve_chat_persona_snapshot(
        FakeDb({"u-array": "[1, 2, 3]"}),
        "u-array",
        persona_model=FakePersonaModel,
        format_persona=formatter,
    )

    assert missing.persona_obj is None
    assert missing.persona_json == "{}"
    assert missing.parse_failed is False
    assert invalid.persona_data == {}
    assert invalid.parse_failed is True
    assert array_value.persona_data == {}
    assert array_value.parse_failed is True
    assert formatter_payloads == [{}, {}, {}]


def test_parent_persona_lookup_wrapper_remains_patchable(monkeypatch):
    from api import chat_persona_lookup
    from api import routes

    calls: list[tuple[Any, str, Any]] = []

    def fake_resolver(db, user_id, *, persona_model, format_persona):
        calls.append((db, user_id, persona_model))
        return chat_persona_lookup.ChatPersonaSnapshot(
            persona_obj=None,
            persona_json="{}",
            persona_data={},
            persona_text="patched",
            lookup_user_id=user_id,
            matched_user_id=None,
            candidate_count=1,
            parse_failed=False,
        )

    monkeypatch.setattr(chat_persona_lookup, "resolve_chat_persona_snapshot", fake_resolver)
    db = object()

    assert routes._resolve_chat_persona_snapshot.__module__ == "api.routes"
    snapshot = routes._resolve_chat_persona_snapshot(db, "u-patched")
    assert snapshot.persona_text == "patched"
    assert calls == [(db, "u-patched", routes.Persona)]


def test_proxy_chat_group_persona_does_not_bypass_disabled_gate(
    client,
    db_session,
    monkeypatch,
):
    from core.database import Persona
    from unittest.mock import AsyncMock, patch

    db_session.add(
        Persona(
            user_id="private_persona-user",
            persona_json=json.dumps({"persona_summary": "fallback persona"}, ensure_ascii=False),
        )
    )
    db_session.commit()

    monkeypatch.setattr("api.routes._schedule_image_precache", lambda *args, **kwargs: None)

    mock_bridge = AsyncMock()
    mock_bridge.handle_message = AsyncMock(return_value="画像回复")

    with patch("api.routes.get_bridge", return_value=mock_bridge):
        response = client.post(
            "/api/v1/chat",
            json={
                "user_id": "persona-user",
                "session_id": "group_persona",
                "query": "你好",
                "client_meta": {"platform": "qq", "chat_type": "group"},
            },
        )

    assert response.status_code == 200
    _, kwargs = mock_bridge.handle_message.await_args
    assert kwargs["metadata"]["persona_text"] == ""
