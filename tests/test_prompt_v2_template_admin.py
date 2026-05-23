from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, get_db
from server import app


PROMPT_V2_DEFAULT_DIR = Path("prompts.v2.default")


def _auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


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
        """{
  "version": 1,
  "nodes": [
    {"id": "base_contract", "type": "template", "label": "system: V2 base contract", "template_key": "chat/main"},
    {"id": "private_policy", "type": "template", "label": "system: private policy", "template_key": "chat/branch_private", "chat_types": ["private"]},
    {"id": "runtime_context", "type": "runtime", "label": "system: runtime_context", "runtime_key": "runtime_context"},
    {"id": "current_user_event", "type": "runtime", "label": "user: current_user_input", "runtime_key": "current_user_event"}
  ],
  "edges": [
    {"from": "base_contract", "to": "private_policy", "chat_types": ["private"]},
    {"from": "private_policy", "to": "runtime_context", "chat_types": ["private"]},
    {"from": "runtime_context", "to": "current_user_event", "chat_types": ["private"]}
  ]
}
""",
        encoding="utf-8",
    )


def test_prompt_v2_templates_can_be_edited_from_admin(tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    default_dir.mkdir()
    _write_template(default_dir / "chat" / "main.md", "主回复 V2", "默认主规则 {{ chat_type }}")
    _write_template(default_dir / "chat" / "branch_private.md", "私聊回复 V2", "默认私聊行为")
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
    Base.metadata.create_all(bind=engine)

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
            "chat/branch_private",
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
            "super_user_id",
            "is_super_user",
            "chat_type",
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
            json={"content": "你叫 {{ character_name }}\nsuper: {{ super_user_id }}"},
            headers=_auth_header(),
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["saved"] is True
        assert (runtime_dir / "chat" / "identity_context.md").read_text(encoding="utf-8") == (
            "你叫 {{ character_name }}\nsuper: {{ super_user_id }}\n"
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
            "private_policy",
            "runtime_context",
            "current_user_event",
        ]

        edited_flow = flow_json["flow"]
        edited_flow["nodes"].insert(
            2,
            {
                "id": "custom_template",
                "type": "template",
                "label": "system: custom",
                "template_key": "chat/identity_context",
                "chat_types": ["private"],
            },
        )
        edited_flow["edges"] = [
            {"from": "base_contract", "to": "private_policy", "chat_types": ["private"]},
            {"from": "private_policy", "to": "custom_template", "chat_types": ["private"]},
            {"from": "custom_template", "to": "runtime_context", "chat_types": ["private"]},
            {"from": "runtime_context", "to": "current_user_event", "chat_types": ["private"]},
        ]
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
                "user_id": "u1",
                "user_input": "你好",
            },
            headers=_auth_header(),
        )
        assert preview.status_code == 200, preview.text
        messages = preview.json()["request_json"]["messages"]
        rendered = str(messages)
        assert "默认主规则 private" in rendered
        assert "你叫" in rendered
        assert messages[-1]["role"] == "user"
        assert preview.json()["debug"]["flow_source"] == "runtime"
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
    Base.metadata.create_all(bind=engine)

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
            engine="v2",
            chat_type="private",
            prompt_key="chat_private",
            messages=plan_messages,
            tool_schemas=plan_tool_schemas,
            section_hashes={"base_contract": "a" * 64},
            prompt_sha256="b" * 64,
            token_estimate=12,
            warnings=["preview warning"],
            debug={"template_path": "/tmp/v2.md"},
        )

    def fail_assembler(*_args, **_kwargs):
        raise AssertionError("V2 effective preview must not call PromptAssembler")

    monkeypatch.setattr("core.prompt_v2.preview.build_preview_plan", fail_preview_wrapper)
    monkeypatch.setattr("core.prompt_v2.compiler.compile_prompt_plan", fake_compile)
    monkeypatch.setattr("core.prompt_assembler.PromptAssembler.build", fail_assembler)

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/admin/prompt/effective-preview",
            json={
                "engine": "v2",
                "chat_type": "private",
                "user_id": "u1",
                "user_input": "你好",
            },
            headers=_auth_header(),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    data = response.json()
    assert captured
    assert captured[0][1] is False
    assert data["request_json"]["messages"] == plan_messages
    assert data["messages"] == plan_messages
    assert data["request_json"]["tools"] == plan_tool_schemas
    assert data["tool_schemas"] == plan_tool_schemas
    assert len(data["tool_plan_sha256"]) == 64
    assert data["warnings"] == ["preview warning"]


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
    Base.metadata.create_all(bind=engine)

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
        assert created.status_code == 200, created.text
        created_path = runtime_dir / "tools" / "custom_tool" / "usage.md"
        assert created_path.exists()
        assert "tool_name: custom_tool" in created_path.read_text(encoding="utf-8")

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
        "news_search",
        "image_summary",
        "persona_update",
        "schedule_task",
        "group_analysis",
        "sticker_search",
    }.issubset(tool_names)


def test_group_analysis_template_describes_real_pipeline():
    raw = (PROMPT_V2_DEFAULT_DIR / "tools" / "group_analysis" / "usage.md").read_text(encoding="utf-8")

    assert "group_id" in raw
    assert "window_hours" in raw
    assert "话题总结" in raw
    assert "活跃用户称号" in raw
    assert "群聊金句" in raw
    assert "聊天质量锐评" in raw
    assert "工具返回的是可直接发送的 HTML 日报" in raw
