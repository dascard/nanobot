import pytest


@pytest.fixture
def auth_header(monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    return {"Authorization": "Bearer test-token"}


@pytest.fixture
def legacy_prompt_env(monkeypatch, tmp_path):
    default_dir = tmp_path / "default_fragments"
    runtime_dir = tmp_path / "runtime_fragments"
    backup_dir = tmp_path / "backups"
    output_path = tmp_path / "runtime_prompt" / "prompt.md"
    default_prompt = tmp_path / "creature_prompt.md"
    default_dir.mkdir(parents=True)
    backup_dir.mkdir(parents=True)
    output_path.parent.mkdir(parents=True)
    default_prompt.write_text("默认整页 Prompt", encoding="utf-8")
    (default_dir / "00_test.md").write_text("# 默认片段\n", encoding="utf-8")

    monkeypatch.setenv("NANOBOT_LEGACY_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_LEGACY_PROMPT_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("NANOBOT_LEGACY_PROMPT_BACKUP_DIR", str(backup_dir))
    monkeypatch.setenv("NANOBOT_LEGACY_PROMPT_OUTPUT", str(output_path))
    monkeypatch.setattr(
        "core.legacy_prompt_runtime.legacy_default_prompt_path",
        lambda: str(default_prompt),
    )

    return {
        "default_dir": default_dir,
        "runtime_dir": runtime_dir,
        "backup_dir": backup_dir,
        "output_path": output_path,
        "default_prompt": default_prompt,
    }


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("put", "/api/v1/admin/prompts/group_chat", {"content": "不应写入"}),
        ("post", "/api/v1/admin/prompts/reload", None),
        ("post", "/api/v1/admin/prompts/group_chat/rollback", {"backup_name": "x.bak"}),
        ("put", "/api/v1/admin/prompt/fragments/00_test.md", {"content": "不应写入"}),
        ("post", "/api/v1/admin/prompt/build", None),
        ("post", "/api/v1/admin/prompt/fragments/00_test.md/reset-to-default", None),
        ("post", "/api/v1/admin/prompt/init-runtime", None),
        ("post", "/api/v1/admin/prompt/backups/00_test.md.20260101_000000_000000.abc123.bak/rollback", None),
    ],
)
def test_legacy_prompt_write_endpoints_are_readonly(client, auth_header, method, path, json_body):
    response = getattr(client, method)(path, headers=auth_header, json=json_body)

    assert response.status_code == 410
    assert "只读迁移入口" in response.text


def test_effective_preview_v1_returns_410_without_prompt_assembler(client, auth_header, monkeypatch):
    def fail_assembler(*_args, **_kwargs):
        raise AssertionError("V1 effective preview must not call PromptAssembler after P1-5")

    monkeypatch.setattr("core.prompt_assembler.PromptAssembler.build", fail_assembler)

    response = client.post(
        "/api/v1/admin/prompt/effective-preview",
        json={
            "engine": "v1",
            "chat_type": "group",
            "session_id": "group_1001",
            "user_id": "u1",
            "group_id": "1001",
            "prompt_key": "group_chat",
            "mode": "managed",
            "user_input": "LEGACY_PREVIEW_MARKER",
        },
        headers=auth_header,
    )

    assert response.status_code == 410
    assert "Prompt V1" in response.text
    assert "只读迁移入口" in response.text


def test_legacy_prompt_read_endpoints_do_not_write_runtime_files(client, auth_header, legacy_prompt_env):
    env = legacy_prompt_env
    env["output_path"].write_text("# 旧产物\n\n{{ name_hint }}\n", encoding="utf-8")
    before_output = env["output_path"].read_text(encoding="utf-8")
    assert not env["runtime_dir"].exists()

    prompt_response = client.get("/api/v1/admin/prompt", headers=auth_header)
    fragments_response = client.get("/api/v1/admin/prompt/fragments", headers=auth_header)
    default_response = client.get("/api/v1/admin/prompt/fragments/00_test.md/default", headers=auth_header)
    diff_response = client.get("/api/v1/admin/prompt/fragments/00_test.md/diff-default", headers=auth_header)
    backups_response = client.get("/api/v1/admin/prompt/backups", headers=auth_header)

    assert prompt_response.status_code == 200, prompt_response.text
    assert fragments_response.status_code == 200, fragments_response.text
    assert default_response.status_code == 200, default_response.text
    assert diff_response.status_code == 200, diff_response.text
    assert backups_response.status_code == 200, backups_response.text
    assert env["output_path"].read_text(encoding="utf-8") == before_output
    assert not env["runtime_dir"].exists()
    assert fragments_response.json()["fragments"][0]["name"] == "00_test.md"
