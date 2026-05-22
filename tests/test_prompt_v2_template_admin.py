from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, get_db
from server import app


def _auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _write_template(path: Path, name: str, body: str) -> None:
    path.write_text(
        f"---\nname: {name}\nversion: 1\n---\n{body}\n",
        encoding="utf-8",
    )


def test_prompt_v2_templates_can_be_edited_from_admin(tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    default_dir.mkdir()
    _write_template(default_dir / "chat_private.md", "私聊回复 V2", "默认私聊规则\n## 私聊行为\n默认私聊行为")
    _write_template(default_dir / "identity_context.md", "身份上下文", "你叫 {{ character_name }}")
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
        assert [item["template_key"] for item in data["items"]] == ["chat_private", "identity_context"]
        assert data["items"][0]["source"] == "default"

        detail = client.get("/api/v1/admin/prompt-v2/templates/identity_context", headers=_auth_header())
        assert detail.status_code == 200, detail.text
        assert detail.json()["content"] == "你叫 {{ character_name }}"
        assert {item["name"] for item in detail.json()["variables"]} == {
            "character_name",
            "name_hint",
            "alias_names",
            "sender_id",
            "super_user_id",
            "is_super_user",
        }

        saved = client.put(
            "/api/v1/admin/prompt-v2/templates/identity_context",
            json={"content": "你叫 {{ character_name }}\nsuper: {{ super_user_id }}"},
            headers=_auth_header(),
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["saved"] is True
        assert (runtime_dir / "identity_context.md").read_text(encoding="utf-8") == (
            "你叫 {{ character_name }}\nsuper: {{ super_user_id }}\n"
        )

        rejected = client.put(
            "/api/v1/admin/prompt-v2/templates/identity_context",
            json={"content": "当前输入 {{ user_input }}"},
            headers=_auth_header(),
        )
        assert rejected.status_code == 400
        assert "unsupported variables" in rejected.text

        client.put(
            "/api/v1/admin/prompt-v2/templates/chat_private",
            json={"content": "V2_RUNTIME_TEMPLATE_MARKER\n## 私聊行为\n只走运行时模板"},
            headers=_auth_header(),
        )
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
        rendered = str(preview.json()["request_json"])
        assert "V2_RUNTIME_TEMPLATE_MARKER" in rendered
        assert preview.json()["prompt_runtime_path"] == str(runtime_dir / "chat_private.md")
    finally:
        app.dependency_overrides.clear()
