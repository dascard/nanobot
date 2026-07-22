import hashlib
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import get_db
from server import app
from tests.sqlite_test_utils import install_base_schema


PROMPT_V2_DEFAULT_DIR = Path("prompts.v2.default")


def _auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _chat_stream_config_snapshot(db) -> list[tuple]:
    from core.database import ChatStreamConfig

    return [
        (
            row.chat_stream_id,
            row.talk_value,
            row.group_profile_mode,
            row.session_guidance,
            row.session_guidance_updated_at,
        )
        for row in db.query(ChatStreamConfig).order_by(
            ChatStreamConfig.chat_stream_id,
        ).all()
    ]


def _write_template(path: Path, name: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\nversion: 1\n---\n{body}\n",
        encoding="utf-8",
    )


def _write_tool_template(path: Path, name: str, tool_name: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\nversion: 1\nkind: tool\ntool_name: {tool_name}\n---\n{body}\n",
        encoding="utf-8",
    )


def _write_flow(path: Path) -> None:
    path.write_text(
        (PROMPT_V2_DEFAULT_DIR / "chat" / "flow.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def test_prompt_template_admin_exposes_canonical_and_v2_compat_routes(tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    default_dir.mkdir()
    _write_template(default_dir / "chat" / "main.md", "主回复", "默认主规则 {{ chat_type }}")
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))

    engine = create_engine(
        f"sqlite:///{tmp_path / 'canonical.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    install_base_schema(engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)

        canonical = client.get("/api/v1/admin/prompt/templates", headers=_auth_header())
        compat = client.get("/api/v1/admin/prompt-v2/templates", headers=_auth_header())
    finally:
        app.dependency_overrides.clear()

    assert canonical.status_code == 200, canonical.text
    assert compat.status_code == 200, compat.text
    assert canonical.json()["items"] == compat.json()["items"]
    assert canonical.json()["default_dir"] == str(default_dir)
    assert canonical.json()["runtime_dir"] == str(runtime_dir)


def test_prompt_v2_templates_can_be_edited_from_admin(tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    default_dir.mkdir()
    _write_template(default_dir / "chat" / "main.md", "主回复 V2", "默认主规则 {{ chat_type }}")
    _write_template(default_dir / "chat" / "branch_group.md", "群聊回复 V2", "默认群聊行为")
    _write_template(default_dir / "chat" / "branch_private.md", "私聊回复 V2", "默认私聊行为")
    _write_template(default_dir / "chat" / "custom.md", "自定义扩展", "自定义规则")
    _write_template(default_dir / "chat" / "identity_context.md", "身份上下文", "你叫 {{ character_name }}")
    _write_tool_template(default_dir / "tools" / "sql_analysis" / "usage.md", "SQL 分析工具", "sql_analysis", "只读查询")
    _write_flow(default_dir / "chat" / "flow.json")
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    install_base_schema(engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)

        listed = client.get("/api/v1/admin/prompt-v2/templates", headers=_auth_header())
        assert listed.status_code == 200, listed.text
        data = listed.json()
        assert data["default_dir"] == str(default_dir)
        assert data["runtime_dir"] == str(runtime_dir)
        assert [item["template_key"] for item in data["items"]] == [
            "chat/branch_group",
            "chat/branch_private",
            "chat/custom",
            "chat/identity_context",
            "chat/main",
            "tools/sql_analysis/usage",
        ]
        assert "chat" in data["tree"]
        assert "tools" in data["tree"]
        assert data["items"][0]["source"] == "default"
        sql_record = next(item for item in data["items"] if item["template_key"] == "tools/sql_analysis/usage")
        assert sql_record["kind"] == "tool"
        assert sql_record["tool_name"] == "sql_analysis"
        assert sql_record["tool_schema"]["function"]["name"] == "sql_analysis"
        assert "sql" in sql_record["tool_schema"]["function"]["parameters"]["properties"]

        detail = client.get("/api/v1/admin/prompt-v2/templates/chat/identity_context", headers=_auth_header())
        assert detail.status_code == 200, detail.text
        assert detail.json()["content"] == "你叫 {{ character_name }}"
        assert {item["name"] for item in detail.json()["variables"]} == {
            "character_name",
            "name_hint",
            "alias_names",
            "sender_id",
            "is_super_user",
            "chat_type",
            "platform",
            "session_id",
            "group_id",
            "user_id",
            "sender_name",
            "bot_name",
            "bot_aliases",
            "current_time",
            "timezone",
            "messages_text",
            "style_messages_text",
            "users_text",
            "instructions",
            "evidence_cards",
            "candidate_cards",
            "mode_hint",
            "card_count",
            "image_count",
            "focus",
        }

        sql_detail = client.get("/api/v1/admin/prompt-v2/templates/tools/sql_analysis/usage", headers=_auth_header())
        assert sql_detail.status_code == 200, sql_detail.text
        sql_schema = sql_detail.json()["tool_schema"]["function"]
        assert sql_schema["name"] == "sql_analysis"
        assert sql_schema["parameters"]["required"] == ["sql"]
        assert "只读" in sql_schema["description"]

        saved = client.put(
            "/api/v1/admin/prompt-v2/templates/chat/identity_context",
            json={"content": "你叫 {{ character_name }}\nsuper: {{ is_super_user }}"},
            headers=_auth_header(),
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["saved"] is True
        assert (runtime_dir / "chat" / "identity_context.md").read_text(encoding="utf-8") == (
            "你叫 {{ character_name }}\nsuper: {{ is_super_user }}\n"
        )

        rejected = client.put(
            "/api/v1/admin/prompt-v2/templates/chat/identity_context",
            json={"content": "当前输入 {{ user_input }}"},
            headers=_auth_header(),
        )
        assert rejected.status_code == 400
        assert "unsupported variables" in rejected.text

        flow = client.get("/api/v1/admin/prompt-v2/flow", headers=_auth_header())
        assert flow.status_code == 200, flow.text
        flow_json = flow.json()
        assert [node["id"] for node in flow_json["flow"]["nodes"]] == [
            "base_contract",
            "qq_common_policy",
            "group_policy",
            "qq_group_policy",
            "private_policy",
            "runtime_context",
            "identity_context",
            "session_guidance",
            "persona_reference",
            "conversation_context_header",
            "history_messages",
            "group_context",
            "effort_constraint",
            "runtime_tool_prompt",
            "current_user_event",
        ]

        edited_flow = flow_json["flow"]
        edited_flow["nodes"].insert(
            3,
            {
                "id": "custom_template",
                "type": "template",
                "label": "system: custom",
                "template_key": "chat/custom",
                "chat_types": ["private"],
            },
        )
        edited_flow["edges"] = [
            edge
            for edge in edited_flow["edges"]
            if (edge["from"], edge["to"]) != ("private_policy", "runtime_context")
        ]
        edited_flow["edges"].extend(
            [
                {
                    "from": "private_policy",
                    "to": "custom_template",
                    "chat_types": ["private"],
                },
                {
                    "from": "custom_template",
                    "to": "runtime_context",
                    "chat_types": ["private"],
                },
            ]
        )
        saved_flow = client.put(
            "/api/v1/admin/prompt-v2/flow",
            json={"flow": edited_flow},
            headers=_auth_header(),
        )
        assert saved_flow.status_code == 200, saved_flow.text
        assert (runtime_dir / "chat" / "flow.json").exists()

        preview = client.post(
            "/api/v1/admin/prompt/effective-preview",
            json={
                "engine": "v2",
                "chat_type": "private",
                "platform": "web",
                "user_id": "u1",
                "user_input": "你好",
            },
            headers=_auth_header(),
        )
        assert preview.status_code == 200, preview.text
        preview_data = preview.json()
        assert preview_data["platform"] == "web"
        assert preview_data["prompt_plan"]["platform"] == "web"
        assert preview_data["message_token_estimate"] > 0
        assert preview_data["tool_schema_token_estimate"] > 0
        assert preview_data["token_estimate"] == (
            preview_data["message_token_estimate"]
            + preview_data["tool_schema_token_estimate"]
        )
        assert "qq_common_policy" not in preview_data["debug"].get("flow_node_ids", [])
        messages = preview_data["request_json"]["messages"]
        runtime_message = next(
            message
            for message in messages
            if str(message.get("content") or "").startswith("<runtime_context>")
        )
        runtime_body = str(runtime_message["content"]).split(
            "<runtime_context>", 1
        )[1].split("</runtime_context>", 1)[0]
        assert json.loads(runtime_body)["platform"] == "web"
        rendered = str(messages)
        assert "QQ 平台" not in rendered
        assert "默认主规则 private" in rendered
        assert "你叫" in rendered
        assert messages[-1]["role"] == "user"
        assert preview_data["debug"]["flow_source"] == "runtime"
    finally:
        app.dependency_overrides.clear()


def test_effective_preview_v2_calls_compiler_directly(tmp_path, monkeypatch):
    from core.prompt_v2.schema import PromptPlan

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    engine = create_engine(
        f"sqlite:///{tmp_path / 'preview.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    install_base_schema(engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    async def fail_preview_wrapper(*_args, **_kwargs):
        raise AssertionError("V2 effective preview must not call build_preview_plan wrapper")

    captured = []
    plan_tool_schemas = [{"type": "function", "function": {"name": "from_plan"}}]
    plan_messages = [
        {"role": "system", "content": "COMPILED_BY_REAL_V2_SERVICE"},
        {"role": "user", "content": "<user_input>\n你好\n</user_input>"},
    ]

    async def fake_compile(request, *, strict_audit=False):
        captured.append((request, strict_audit))
        return PromptPlan(
            engine="prompt",
            chat_type="private",
            prompt_key="chat_private",
            messages=plan_messages,
            tool_schemas=plan_tool_schemas,
            section_hashes={"base_contract": "a" * 64},
            prompt_sha256="b" * 64,
            token_estimate=12,
            message_token_estimate=5,
            tool_schema_token_estimate=7,
            warnings=["preview warning"],
            debug={"template_path": "/tmp/v2.md"},
        )

    monkeypatch.setattr("core.prompt_v2.preview.build_preview_plan", fail_preview_wrapper)
    monkeypatch.setattr("core.prompt_v2.compiler.compile_prompt_plan", fake_compile)

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/admin/prompt/effective-preview",
            json={
                "engine": "v2",
                "chat_type": "private",
                "session_id": "private_preview-direct",
                "user_id": "u1",
                "user_input": "你好",
                "session_guidance_override": "严格编译草稿",
            },
            headers=_auth_header(),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    data = response.json()
    assert captured
    assert captured[0][1] is True
    assert captured[0][0].session_guidance == "严格编译草稿"
    assert (
        captured[0][0].session_guidance_chat_stream_id
        == "qq:preview-direct:private"
    )
    assert captured[0][0].debug["session_guidance_resolution_status"] == "configured"
    assert data["engine"] == "prompt"
    assert data["prompt_plan"]["engine"] == "prompt"
    assert data["compiled_prompt"]["engine"] == "prompt"
    assert data["prompt_build"]["engine"] == "prompt"
    assert data["request_json"]["messages"] == plan_messages
    assert data["messages"] == plan_messages
    assert data["request_json"]["tools"] == plan_tool_schemas
    assert data["tool_schemas"] == plan_tool_schemas
    assert data["message_token_estimate"] == 5
    assert data["tool_schema_token_estimate"] == 7
    assert data["token_estimate"] == 12
    assert data["prompt_plan"]["message_token_estimate"] == 5
    assert data["compiled_prompt"]["tool_schema_token_estimate"] == 7
    assert data["prompt_build"]["token_estimate"] == 12
    assert len(data["tool_plan_sha256"]) == 64
    assert data["warnings"] == ["preview warning"]
    assert data["session_guidance_configured"] is True
    assert data["session_guidance_chars"] == len("严格编译草稿")


def test_effective_preview_uses_unsaved_session_guidance_without_persisting(
    client,
    db_session,
    monkeypatch,
):
    from core.database import ChatStreamConfig

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")

    async def fail_model_call(*_args, **_kwargs):
        raise AssertionError("effective preview 不得调用模型")

    monkeypatch.setattr(
        "kohakuterrarium.llm.openai.OpenAIProvider._complete_chat",
        fail_model_call,
    )
    monkeypatch.setattr(
        "kohakuterrarium.llm.openai.OpenAIProvider._stream_chat",
        fail_model_call,
    )
    draft = "未保存草稿"

    response = client.post(
        "/api/v1/admin/prompt/effective-preview",
        headers=_auth_header(),
        json={
            "engine": "prompt",
            "platform": "qq",
            "chat_type": "group",
            "session_id": "group_preview-draft",
            "group_id": "preview-draft",
            "user_input": "预览消息",
            "session_guidance_override": draft,
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    guidance_messages = [
        message
        for message in data["messages"]
        if str(message.get("content") or "").startswith("<session_guidance>")
    ]
    assert len(guidance_messages) == 1
    assert draft in guidance_messages[0]["content"]
    assert data["session_guidance_chat_stream_id"] == "qq:preview-draft:group"
    assert data["session_guidance_resolution_status"] == "configured"
    assert data["session_guidance_configured"] is True
    assert data["session_guidance_chars"] == len(draft)
    assert data["session_guidance_sha256"] == hashlib.sha256(
        draft.encode("utf-8"),
    ).hexdigest()
    assert db_session.query(ChatStreamConfig).count() == 0


def test_effective_preview_reads_database_guidance_and_empty_override_is_ephemeral(
    client,
    db_session,
    monkeypatch,
):
    from core.database import ChatStreamConfig

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    stored_guidance = "数据库有效指导"
    updated_at = datetime(2026, 7, 13, 12, 0, 0)
    db_session.add(ChatStreamConfig(
        chat_stream_id="web:preview-db:private",
        talk_value=0.8,
        group_profile_mode="preview",
        session_guidance=stored_guidance,
        session_guidance_updated_at=updated_at,
    ))
    db_session.commit()
    before = _chat_stream_config_snapshot(db_session)

    database_preview = client.post(
        "/api/v1/admin/prompt/effective-preview",
        headers=_auth_header(),
        json={
            "engine": "prompt",
            "platform": "web",
            "chat_type": "private",
            "session_id": "private_preview-db",
            "user_input": "读取数据库",
        },
    )
    cleared_preview = client.post(
        "/api/v1/admin/prompt/effective-preview",
        headers=_auth_header(),
        json={
            "engine": "prompt",
            "platform": "web",
            "chat_type": "private",
            "session_id": "private_preview-db",
            "user_input": "临时清空",
            "session_guidance_override": "",
        },
    )

    assert database_preview.status_code == 200, database_preview.text
    database_data = database_preview.json()
    database_guidance = [
        message
        for message in database_data["messages"]
        if str(message.get("content") or "").startswith("<session_guidance>")
    ]
    assert len(database_guidance) == 1
    assert stored_guidance in database_guidance[0]["content"]
    assert database_data["session_guidance_resolution_status"] == "configured"
    assert database_data["session_guidance_updated_at"] == "2026-07-13 12:00:00"

    assert cleared_preview.status_code == 200, cleared_preview.text
    cleared_data = cleared_preview.json()
    assert not any(
        str(message.get("content") or "").startswith("<session_guidance>")
        for message in cleared_data["messages"]
    )
    assert cleared_data["session_guidance_resolution_status"] == "empty"
    assert cleared_data["session_guidance_configured"] is False
    assert cleared_data["session_guidance_chars"] == 0
    assert cleared_data["session_guidance_sha256"] == ""

    db_session.expire_all()
    assert _chat_stream_config_snapshot(db_session) == before


@pytest.mark.parametrize(
    ("payload", "secret"),
    [
        (
            {
                "session_id": "private_preview-invalid",
                "session_guidance_override": "正文不能泄漏<runtime_context>",
            },
            "正文不能泄漏",
        ),
        (
            {"session_guidance_override": "缺失身份的草稿正文"},
            "缺失身份的草稿正文",
        ),
        (
            {
                "session_id": "private_preview-invalid-object",
                "session_guidance_override": {
                    "draft": "对象类型草稿正文不能泄漏",
                },
            },
            "对象类型草稿正文不能泄漏",
        ),
        (
            {
                "session_id": "private_preview-invalid-list",
                "session_guidance_override": ["数组类型草稿正文不能泄漏"],
            },
            "数组类型草稿正文不能泄漏",
        ),
    ],
    ids=[
        "reserved-marker",
        "missing-identity",
        "invalid-object-type",
        "invalid-list-type",
    ],
)
def test_effective_preview_rejects_invalid_guidance_without_echoing_body(
    client,
    db_session,
    monkeypatch,
    payload,
    secret,
):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    before = _chat_stream_config_snapshot(db_session)
    body = {
        "engine": "prompt",
        "platform": "qq",
        "chat_type": "private",
        "user_input": "错误路径",
        **payload,
    }

    response = client.post(
        "/api/v1/admin/prompt/effective-preview",
        headers=_auth_header(),
        json=body,
    )

    assert response.status_code == 422, response.text
    assert secret not in response.text
    assert _chat_stream_config_snapshot(db_session) == before


def test_effective_preview_without_session_or_override_keeps_generic_behavior(
    client,
    db_session,
    monkeypatch,
):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    before = _chat_stream_config_snapshot(db_session)

    response = client.post(
        "/api/v1/admin/prompt/effective-preview",
        headers=_auth_header(),
        json={
            "engine": "prompt",
            "platform": "web",
            "chat_type": "private",
            "user_id": "generic-preview-user",
            "user_input": "通用预览",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["session_guidance_resolution_status"] == "not_requested"
    assert data["session_guidance_chat_stream_id"] == ""
    assert data["session_guidance_configured"] is False
    assert not any(
        str(message.get("content") or "").startswith("<session_guidance>")
        for message in data["messages"]
    )
    assert _chat_stream_config_snapshot(db_session) == before


def test_effective_preview_v2_passes_platform_to_tools_and_compiler(tmp_path, monkeypatch):
    from core.prompt_v2.schema import PromptPlan

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    engine = create_engine(
        f"sqlite:///{tmp_path / 'preview_platform.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    install_base_schema(engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    captured = {}

    async def fake_compile(request, *, strict_audit=False):
        captured["compile_platform"] = request.platform
        captured["compile_normalized_platform"] = request.normalized_platform
        return PromptPlan(
            engine="prompt",
            chat_type=request.normalized_chat_type,
            platform=request.normalized_platform,
            prompt_key=request.normalized_prompt_key,
            messages=[{"role": "user", "content": "<user_input>\nhi\n</user_input>"}],
            tool_schemas=[],
            section_hashes={},
            prompt_sha256="b" * 64,
            token_estimate=1,
            warnings=[],
            debug={"platform": request.normalized_platform, "flow_node_ids": ["base"]},
        )

    def fake_build_tool_plan(**kwargs):
        captured["tool_platform"] = kwargs.get("platform")
        return SimpleNamespace(
            enabled={},
            disabled={},
            runtime_tool_prompt="",
            sent_tool_schemas=[],
            sha256="c" * 64,
        )

    monkeypatch.setattr("core.prompt_v2.compiler.compile_prompt_plan", fake_compile)
    monkeypatch.setattr("core.tool_plan.build_tool_plan", fake_build_tool_plan)

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/admin/prompt/effective-preview",
            json={
                "engine": "v2",
                "chat_type": "private",
                "platform": "web",
                "user_id": "u1",
                "user_input": "hi",
            },
            headers=_auth_header(),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["platform"] == "web"
    assert data["prompt_plan"]["platform"] == "web"
    assert captured["compile_platform"] == "web"
    assert captured["compile_normalized_platform"] == "web"
    assert captured["tool_platform"] == "web"


@pytest.mark.parametrize("error_kind", ["flow", "audit"])
def test_effective_preview_v2_returns_400_for_invalid_contract(
    tmp_path,
    monkeypatch,
    error_kind,
):
    from core.prompt_v2.audit import PromptAuditError
    from core.prompt_v2.flow import PromptFlowError

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    engine = create_engine(
        f"sqlite:///{tmp_path / 'preview_error.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    install_base_schema(engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    async def fail_compile(*_args, **kwargs):
        assert kwargs.get("strict_audit") is True
        if error_kind == "audit":
            raise PromptAuditError(["identity_context flow contract invalid"])
        raise PromptFlowError("flow 在 private 下存在环: a -> b")

    monkeypatch.setattr("core.prompt_v2.compiler.compile_prompt_plan", fail_compile)

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/admin/prompt/effective-preview",
            json={
                "engine": "v2",
                "chat_type": "private",
                "session_id": "private_preview-error",
                "user_id": "u1",
                "user_input": "你好",
                "session_guidance_override": "FLOW_ERROR_DRAFT_MUST_NOT_ECHO",
            },
            headers=_auth_header(),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    expected = (
        "identity_context flow contract invalid"
        if error_kind == "audit"
        else "flow 在 private 下存在环"
    )
    assert expected in response.text
    assert "FLOW_ERROR_DRAFT_MUST_NOT_ECHO" not in response.text


def test_prompt_v2_template_admin_crud_runtime_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    _write_tool_template(
        default_dir / "tools" / "group_analysis" / "topics.md",
        "群聊话题",
        "group_analysis",
        "默认话题 {{ messages_text }}",
    )
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    engine = create_engine(
        f"sqlite:///{tmp_path / 'crud.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    install_base_schema(engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)

        created = client.post(
            "/api/v1/admin/prompt-v2/templates",
            json={
                "template_key": "tools/custom_tool/usage",
                "name": "自定义工具",
                "kind": "tool",
                "tool_name": "custom_tool",
                "content": "自定义工具规则 {{ character_name }}",
            },
            headers=_auth_header(),
        )
        assert created.status_code == 400, created.text
        assert "unregistered_tool_template" in created.text
        created_path = runtime_dir / "tools" / "custom_tool" / "usage.md"
        assert not created_path.exists()

        saved = client.put(
            "/api/v1/admin/prompt-v2/templates/tools/group_analysis/topics",
            json={"content": "运行时话题 {{ messages_text }}"},
            headers=_auth_header(),
        )
        assert saved.status_code == 200, saved.text
        runtime_topic = runtime_dir / "tools" / "group_analysis" / "topics.md"
        assert runtime_topic.read_text(encoding="utf-8") == "运行时话题 {{ messages_text }}\n"

        detail = client.get(
            "/api/v1/admin/prompt-v2/templates/tools/group_analysis/topics",
            headers=_auth_header(),
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["source"] == "runtime"
        assert detail.json()["frontmatter"]["tool_name"] == "group_analysis"

        deleted = client.delete(
            "/api/v1/admin/prompt-v2/templates/tools/group_analysis/topics",
            headers=_auth_header(),
        )
        assert deleted.status_code == 200, deleted.text
        assert not runtime_topic.exists()

        reset = client.post(
            "/api/v1/admin/prompt-v2/templates/tools/custom_tool/usage/reset",
            headers=_auth_header(),
        )
        assert reset.status_code == 200, reset.text
        assert not created_path.exists()
    finally:
        app.dependency_overrides.clear()


def test_default_prompt_v2_tool_templates_cover_runtime_tools():
    tool_names: set[str] = set()
    for path in PROMPT_V2_DEFAULT_DIR.rglob("*.md"):
        raw = path.read_text(encoding="utf-8")
        if "\nkind: tool\n" not in raw:
            continue
        for line in raw.splitlines():
            if line.startswith("tool_name:"):
                tool_names.add(line.split(":", 1)[1].strip())

    assert {
        "reply",
        "no_reply",
        "sql_analysis",
        "python_sandbox",
        "ai_daily",
        "image_summary",
        "persona_update",
        "schedule_task",
        "group_analysis",
        "sticker_search",
    }.issubset(tool_names)
    assert "news_search" not in tool_names


def test_group_analysis_template_describes_real_pipeline():
    raw = (PROMPT_V2_DEFAULT_DIR / "tools" / "group_analysis" / "usage.md").read_text(encoding="utf-8")

    assert "group_id" in raw
    assert "window_hours" in raw
    assert "话题总结" in raw
    assert "活跃用户称号" in raw
    assert "群聊金句" in raw
    assert "聊天质量锐评" in raw
    assert "工具返回的是可直接发送的 HTML 日报" in raw
    assert "并不承诺匿名化" in raw
    assert "显示名" in raw


def test_tool_usage_templates_match_deployed_capabilities():
    persona = (PROMPT_V2_DEFAULT_DIR / "tools" / "persona_update" / "usage.md").read_text(encoding="utf-8")
    sandbox = (PROMPT_V2_DEFAULT_DIR / "tools" / "python_sandbox" / "usage.md").read_text(encoding="utf-8")
    daily = (PROMPT_V2_DEFAULT_DIR / "tools" / "ai_daily" / "usage.md").read_text(encoding="utf-8")

    assert "schema 无参数" in persona
    assert "不支持定点纠正、删除或从零重建" in persona
    assert "硬禁用" in sandbox
    assert "不要选择、调用" in sandbox
    assert "最终 HTML" in daily
    assert "不要总结、压缩、重排" in daily
