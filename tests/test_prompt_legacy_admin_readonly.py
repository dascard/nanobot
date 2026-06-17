import pytest


@pytest.fixture
def auth_header(monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    return {"Authorization": "Bearer test-token"}


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("put", "/api/v1/admin/prompts/group_chat", {"content": "不应写入"}),
        ("post", "/api/v1/admin/prompts/reload", None),
        ("post", "/api/v1/admin/prompts/group_chat/preview", {"variables": {"user_input": "不应预览"}}),
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


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/admin/prompts",
        "/api/v1/admin/prompts/group_chat",
        "/api/v1/admin/prompts/group_chat/history",
        "/api/v1/admin/prompt",
        "/api/v1/admin/prompt/fragments",
        "/api/v1/admin/prompt/fragments/00_test.md/default",
        "/api/v1/admin/prompt/fragments/00_test.md/diff-default",
        "/api/v1/admin/prompt/backups",
    ],
)
def test_legacy_prompt_read_endpoints_are_gone(client, auth_header, path):
    response = client.get(path, headers=auth_header)

    assert response.status_code in {404, 410}
