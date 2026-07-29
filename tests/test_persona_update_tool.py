from contextlib import nullcontext
from types import SimpleNamespace

import pytest


def _untrusted_context(user_id: str):
    return SimpleNamespace(
        session=SimpleNamespace(
            extra={
                "nanobot_runtime_context": {
                    "chat_type": "private",
                    "user_id": user_id,
                }
            }
        )
    )


def _runtime_scope(user_id: str | None):
    if user_id is None:
        return nullcontext()
    from core.agent_runtime.request_scope import runtime_context_scope

    return runtime_context_scope({
        "chat_type": "private",
        "user_id": user_id,
        "session_id": f"private_{user_id}",
    })


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "runtime_user_id,context,args",
    [
        (None, None, {}),
        (None, _untrusted_context("actor-user"), {}),
        ("", None, {}),
        ("actor-user", None, {"user_id": "other-user"}),
    ],
)
async def test_persona_update_rejects_missing_or_mismatched_actor_before_database(
    monkeypatch,
    db_session,
    runtime_user_id,
    context,
    args,
):
    from core.database import ChatLog, Persona, PersonaFact
    from creatures.nanobot.prompts.skills.persona_update.tool import PersonaUpdateTool

    db_session.add(
        ChatLog(
            user_id="actor-user",
            session_id="private_actor-user",
            role="user",
            content="稳定日志",
        )
    )
    db_session.add(Persona(user_id="actor-user", persona_json='{"stable":true}'))
    db_session.commit()
    before = (
        db_session.query(ChatLog).count(),
        db_session.query(Persona).count(),
        db_session.query(PersonaFact).count(),
    )

    def fail_if_opened():
        raise AssertionError("database must not be opened before actor authorization")

    monkeypatch.setattr("core.database.SessionLocal", fail_if_opened)

    with _runtime_scope(runtime_user_id):
        result = await PersonaUpdateTool().execute(args, context=context)

    assert result.error == "Persona update authorization failed"
    assert (
        db_session.query(ChatLog).count(),
        db_session.query(Persona).count(),
        db_session.query(PersonaFact).count(),
    ) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [{}, {"user_id": "actor-user"}])
async def test_persona_update_uses_runtime_actor_and_enters_existing_flow(
    monkeypatch,
    args,
):
    from creatures.nanobot.prompts.skills.persona_update.tool import PersonaUpdateTool

    captured = {"opened": 0, "queried_user_ids": [], "filters": []}

    class FakeQuery:
        def __init__(self, model):
            self.model = model

        def filter(self, *expressions):
            for expression in expressions:
                field = str(getattr(expression.left, "name", ""))
                value = str(expression.right.value)
                captured["filters"].append((field, value))
                if field == "user_id":
                    captured["queried_user_ids"].append(value)
            return self

        def first(self):
            return None

        def order_by(self, *_args):
            return self

        def limit(self, _value):
            return self

        def all(self):
            return []

    class FakeDb:
        def query(self, model):
            return FakeQuery(model)

        def close(self):
            pass

    def open_db():
        captured["opened"] += 1
        return FakeDb()

    monkeypatch.setattr("core.database.SessionLocal", open_db)

    with _runtime_scope("actor-user"):
        result = await PersonaUpdateTool().execute(
            args,
            context=_untrusted_context("other-user"),
        )

    assert result.error is None
    assert "没有找到" in result.output
    assert captured["opened"] == 1
    assert captured["queried_user_ids"] == ["actor-user", "actor-user"]
    assert ("status", "active") in captured["filters"]


@pytest.mark.asyncio
async def test_persona_update_rejects_unimplemented_instructions_before_database(monkeypatch):
    from creatures.nanobot.prompts.skills.persona_update.tool import PersonaUpdateTool

    monkeypatch.setattr(
        "core.database.SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("database must not be opened")),
    )

    with _runtime_scope("actor-user"):
        result = await PersonaUpdateTool().execute(
            {"instructions": "删除并重建我的画像"},
            context=_untrusted_context("other-user"),
        )

    assert result.error == "Unsupported persona update arguments"
    assert "完成" not in str(result.output or "")


def test_persona_update_executable_schemas_have_no_model_selected_target_or_instructions():
    from core.tool_schema_preview import build_effective_tool_schemas, build_tool_schema
    from creatures.nanobot.prompts.skills.persona_update.tool import PersonaUpdateTool

    class_schema = PersonaUpdateTool().get_parameters_schema()
    static_schema = build_tool_schema("persona_update")["function"]["parameters"]
    effective_schema = build_effective_tool_schemas({"persona_update": True})[0]["function"][
        "parameters"
    ]

    assert class_schema["properties"] == {}
    assert class_schema.get("required", []) == []

    for schema in (static_schema, effective_schema):
        assert set(schema["properties"]) == {"run_in_background"}
        assert "user_id" not in schema["properties"]
        assert "instructions" not in schema["properties"]
        assert schema.get("required", []) == []


def test_persona_update_registry_only_claims_current_user_refresh():
    from core.tool_registry import get_tool_def
    from creatures.nanobot.prompts.skills.persona_update.tool import PersonaUpdateTool

    descriptions = [
        PersonaUpdateTool().description,
        get_tool_def("persona_update").description,
    ]
    for description in descriptions:
        assert "当前用户" in description
        assert "删除" not in description
        assert "重建" not in description
