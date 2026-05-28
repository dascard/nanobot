def _auth_header():
    return {"Authorization": "Bearer test-token"}


def test_read_log_supports_all_lines_and_grouped_error_context(client, tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    fake_api_dir = tmp_path / "api"
    fake_api_dir.mkdir()
    monkeypatch.setattr("api.admin_routes.__file__", str(fake_api_dir / "admin_routes.py"))
    log_dir = tmp_path / "data"
    log_dir.mkdir()
    (log_dir / "nanobot.log").write_text(
        "\n".join([
            "2026-05-28 10:00:00,000 [INFO] nanobot: before one",
            "2026-05-28 10:00:01,000 [INFO] nanobot: before two",
            "2026-05-28 10:00:02,000 [ERROR] nanobot: bridge failed",
            "Traceback (most recent call last):",
            "  File \"/app/server.py\", line 1, in run",
            "sqlite3.OperationalError: database is locked",
            "[SQL: INSERT INTO chat_logs ...]",
            "2026-05-28 10:00:03,000 [INFO] nanobot: after one",
            "2026-05-28 10:00:04,000 [WARNING] nanobot: after two",
        ]) + "\n",
        encoding="utf-8",
    )

    all_response = client.get(
        "/api/v1/admin/logs/nanobot.log",
        headers=_auth_header(),
        params={"lines": "all"},
    )
    error_response = client.get(
        "/api/v1/admin/logs/nanobot.log",
        headers=_auth_header(),
        params={
            "lines": "all",
            "level": "ERROR",
            "group_errors": "true",
            "context_before": 2,
            "context_after": 1,
        },
    )

    assert all_response.status_code == 200, all_response.text
    assert "before one" in all_response.json()["content"]
    assert "after two" in all_response.json()["content"]

    assert error_response.status_code == 200, error_response.text
    events = error_response.json()["events"]
    assert len(events) == 1
    assert events[0]["before_lines"] == [
        "2026-05-28 10:00:00,000 [INFO] nanobot: before one",
        "2026-05-28 10:00:01,000 [INFO] nanobot: before two",
    ]
    assert "bridge failed" in "\n".join(events[0]["event_lines"])
    assert "database is locked" in "\n".join(events[0]["event_lines"])
    assert events[0]["after_lines"] == ["2026-05-28 10:00:03,000 [INFO] nanobot: after one"]
