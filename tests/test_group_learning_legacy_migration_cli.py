"""旧群学习迁移 CLI 的 dry-run 与 apply 门禁测试。"""

from __future__ import annotations

import json

import pytest


def test_legacy_migration_cli_defaults_to_metadata_only_dry_run(
    db_session,
    monkeypatch,
):
    from core.db.models import ExpressionMemory
    from scripts import migrate_group_learning_legacy as cli

    secret_content = "不得出现在 CLI 输出中的旧表达"
    db_session.add(ExpressionMemory(
        chat_stream_id="qq:42:group",
        expression=secret_content,
        checked=0,
        status="candidate",
    ))
    db_session.commit()
    monkeypatch.setattr(cli, "SessionLocal", lambda: db_session)

    payload = cli._execute(cli.build_parser().parse_args([]))
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["mode"] == "dry_run"
    assert payload["audit"]["source_count"] == 1
    assert payload["audit"]["planned_count"] == 1
    assert len(payload["audit"]["source_sha256"]) == 64
    assert len(payload["audit"]["planned_sha256"]) == 64
    assert secret_content not in serialized
    assert "content" not in payload["audit"]["planned"][0]


@pytest.mark.parametrize(
    "argv, missing_field",
    [
        (["--apply"], "expected_source_sha256"),
        (
            [
                "--apply",
                "--expected-source-sha256",
                "a" * 64,
            ],
            "expected_planned_sha256",
        ),
        (
            [
                "--apply",
                "--expected-source-sha256",
                "a" * 64,
                "--expected-planned-sha256",
                "b" * 64,
            ],
            "actor",
        ),
    ],
)
def test_legacy_migration_cli_apply_requires_both_hashes_and_actor(
    argv,
    missing_field,
):
    from scripts import migrate_group_learning_legacy as cli

    with pytest.raises(ValueError, match=missing_field):
        cli._execute(cli.build_parser().parse_args(argv))
