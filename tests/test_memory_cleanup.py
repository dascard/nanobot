from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from core.database import (
    AdminAuditLog,
    ChatLog,
    GroupMemory,
    MemoryCleanupRun,
    MemoryDigest,
    Persona,
    PersonaBehavior,
    PersonaFact,
    SemanticIndexItem,
)
from core.memory_cleanup import (
    MemoryCleanupError,
    apply_memory_cleanup,
    load_cleanup_bundle,
    preview_memory_cleanup,
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _source_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _build_bundle(tmp_path: Path, db_session) -> tuple[Path, str, dict[str, object]]:
    user_log = ChatLog(
        user_id="cleanup-user",
        session_id="private_cleanup-user",
        role="user",
        content="原始用户证据必须保留",
    )
    group_log = ChatLog(
        user_id="group-member",
        session_id="group_42",
        role="ambient",
        content="群聊原始证据必须保留",
    )
    db_session.add_all([user_log, group_log])
    db_session.flush()

    persona = Persona(
        user_id="cleanup-user",
        persona_json='{"facts":[{"content":"旧画像正文"}]}',
    )
    fact = PersonaFact(
        user_id="cleanup-user",
        content="无可靠证据的旧事实",
        evidence_count=0,
        evidence_log_ids_json="[]",
        status="active",
        inject_policy="auto",
        memory_type="stable_preference",
    )
    behavior = PersonaBehavior(
        user_id="cleanup-user",
        pattern="旧行为正文",
        source_log_ids='["旧文本证据"]',
    )
    group = GroupMemory(
        group_id="group_42",
        memory_type="style",
        content="旧群记忆正文",
        evidence_log_ids_json=json.dumps([group_log.id]),
        evidence_count=1,
        status="active",
        inject_policy="auto",
        meta_json='{"raw_count":1}',
    )
    db_session.add_all([persona, fact, behavior, group])
    db_session.flush()

    source_id = "cleanup-source"
    digest_rows = []
    digest_specs = (
        (0, "detailed_digest", "旧 L0 正文"),
        (1, "preview_digest", "旧 L1 正文"),
        (2, "recall_card", "旧 L2 正文"),
    )
    for level, summary_type, content in digest_specs:
        meta = {
            "schema_version": 2,
            "status": "active",
            "generator": "deterministic_fallback",
            "llm_status": "fallback",
            "source_id": source_id,
            "summary_type": summary_type,
        }
        if level == 2:
            meta["recall_card"] = {
                "type": "episode_detail",
                "text": "旧召回卡正文",
                "evidence_log_ids": [user_log.id],
            }
        row = MemoryDigest(
            user_id="cleanup-user",
            session_id="private_cleanup-user",
            digest_date="2026-07-18",
            level=level,
            content=content,
            meta_json=json.dumps(meta, ensure_ascii=False),
            source_start_log_id=user_log.id,
            source_end_log_id=user_log.id,
        )
        db_session.add(row)
        digest_rows.append(row)
    db_session.flush()

    index = SemanticIndexItem(
        source_type="memory_digest",
        source_id=source_id,
        source_sub_id="old-card",
        index_version="v1",
        status="active",
        text="旧索引正文",
        meta_json='{"generator":"deterministic_fallback","schema_version":2}',
    )
    db_session.add(index)
    db_session.commit()

    persona_candidates = {
        "rules": {},
        "archive_persona_user_fingerprints": [
            _source_fingerprint("cleanup-user")
        ],
        "archive_fact_ids": [fact.id],
        "archive_behavior_ids": [behavior.id],
        "facts": [{
            "id": fact.id,
            "user_fingerprint": _source_fingerprint("cleanup-user"),
            "status": "active",
            "inject_policy": "auto",
            "memory_type": "stable_preference",
            "stored_evidence_count": 0,
            "evidence_array_length": 0,
            "evidence_unique_length": 0,
            "reliable_user_chatlog_backlink": False,
            "legacy_source_log_text": False,
        }],
    }
    group_candidates = {
        "rules": {},
        "archive_ids": [group.id],
        "items": [{
            "id": group.id,
            "group_fingerprint": _source_fingerprint("group_42"),
            "memory_type": "style",
            "status": "active",
            "inject_policy": "auto",
            "stored_evidence_count": 1,
            "evidence_array_length": 1,
            "evidence_unique_length": 1,
            "reliable_group_chatlog_backlink": True,
            "source_raw_count": 1,
            "evidence_window_fingerprint": "window",
        }],
    }
    digest_items = []
    for row, (_, summary_type, content) in zip(digest_rows, digest_specs, strict=True):
        digest_items.append({
            "id": row.id,
            "source_fingerprint": _source_fingerprint(source_id),
            "level": row.level,
            "status": "active",
            "schema_version": 2,
            "generator": "deterministic_fallback",
            "llm_status": "fallback",
            "summary_type": summary_type,
            "card_type": "episode_detail" if row.level == 2 else "",
            "content_chars": len(content),
        })
    digest_candidates = {
        "rules": {},
        "archive_source_fingerprints": [_source_fingerprint(source_id)],
        "archive_row_ids": [row.id for row in digest_rows],
        "stale_semantic_index_item_ids": [index.id],
        "items": digest_items,
    }
    report = {
        "generated_at_utc": "2026-07-18T08:00:00+00:00",
        "mode": "dry_run_read_only",
        "persona": {
            "proposed_archive_snapshot_count": 1,
            "proposed_archive_fact_count": 1,
            "proposed_archive_behavior_count": 1,
        },
        "group_memory": {"proposed_archive_count": 1},
        "memory_digest": {
            "proposed_archive_rows": 3,
            "proposed_archive_sources": 1,
            "stale_memory_digest_index_items": 1,
        },
        "safety": {
            "business_content_exported": False,
            "chat_logs_preserved": True,
            "database_query_only": True,
            "writes_performed": False,
        },
    }

    bundle_dir = tmp_path / "cleanup-bundle"
    bundle_dir.mkdir()
    files = {
        "persona_candidates.json": persona_candidates,
        "group_memory_candidates.json": group_candidates,
        "memory_digest_candidates.json": digest_candidates,
        "report.json": report,
    }
    for name, value in files.items():
        _write_json(bundle_dir / name, value)
    (bundle_dir / "report.md").write_text("# 测试 dry-run\n", encoding="utf-8")
    manifest = {
        path.name: _sha256(path.read_bytes())
        for path in sorted(bundle_dir.iterdir())
        if path.is_file()
    }
    _write_json(bundle_dir / "sha256_manifest.json", manifest)
    manifest_sha256 = _sha256((bundle_dir / "sha256_manifest.json").read_bytes())
    return bundle_dir, manifest_sha256, {
        "persona_id": persona.user_id,
        "fact_id": fact.id,
        "behavior_id": behavior.id,
        "group_id": group.id,
        "digest_ids": [row.id for row in digest_rows],
        "digest_source_id": source_id,
        "index_id": index.id,
        "chat_log_count": 2,
    }


def test_memory_cleanup_rejects_untrusted_manifest(tmp_path, db_session):
    bundle_dir, manifest_sha256, _ids = _build_bundle(tmp_path, db_session)

    with pytest.raises(MemoryCleanupError, match="manifest_sha256_mismatch"):
        load_cleanup_bundle(
            bundle_dir,
            expected_manifest_sha256="0" * 64,
        )

    assert load_cleanup_bundle(
        bundle_dir,
        expected_manifest_sha256=manifest_sha256,
    ).manifest_sha256 == manifest_sha256


def test_memory_cleanup_preview_apply_and_idempotent_replay(tmp_path, db_session):
    bundle_dir, manifest_sha256, ids = _build_bundle(tmp_path, db_session)
    bundle = load_cleanup_bundle(
        bundle_dir,
        expected_manifest_sha256=manifest_sha256,
    )

    preview = preview_memory_cleanup(db_session, bundle)
    assert preview["writes_performed"] is False
    assert preview["validation"] == "passed"
    assert preview["target_counts"] == {
        "persona_snapshots": 1,
        "persona_facts": 1,
        "persona_behaviors": 1,
        "group_memories": 1,
        "memory_digests": 3,
        "memory_digest_sources": 1,
        "semantic_index_candidates": 1,
    }
    assert db_session.query(ChatLog).count() == ids["chat_log_count"]
    db_session.rollback()

    result = apply_memory_cleanup(db_session, bundle, actor="test-admin")
    assert result["idempotent_replay"] is False
    assert result["chat_logs_before"] == result["chat_logs_after"] == 2
    assert result["changed_counts"] == {
        "persona_snapshots": 1,
        "persona_facts": 1,
        "persona_behaviors": 1,
        "group_memories": 1,
        "memory_digests": 3,
    }

    persona = db_session.get(Persona, ids["persona_id"])
    fact = db_session.get(PersonaFact, ids["fact_id"])
    behavior = db_session.get(PersonaBehavior, ids["behavior_id"])
    group = db_session.get(GroupMemory, ids["group_id"])
    assert persona.status == "archived"
    assert "旧画像正文" in persona.persona_json
    assert fact.status == "archived"
    assert fact.inject_policy == "never"
    assert fact.content == "无可靠证据的旧事实"
    assert behavior.status == "archived"
    assert behavior.pattern == "旧行为正文"
    assert group.status == "archived"
    assert group.inject_policy == "never"
    assert group.content == "旧群记忆正文"
    for digest_id in ids["digest_ids"]:
        digest = db_session.get(MemoryDigest, digest_id)
        assert json.loads(digest.meta_json)["status"] == "archived"
        assert digest.content.startswith("旧 L")
    assert db_session.get(SemanticIndexItem, ids["index_id"]).status == "active"
    assert db_session.query(ChatLog).count() == 2
    assert db_session.query(MemoryCleanupRun).count() == 1
    assert db_session.query(AdminAuditLog).filter_by(action="apply_memory_cleanup").count() == 1

    replay = apply_memory_cleanup(db_session, bundle, actor="test-admin")
    assert replay["idempotent_replay"] is True
    assert db_session.query(MemoryCleanupRun).count() == 1
    assert db_session.query(AdminAuditLog).filter_by(action="apply_memory_cleanup").count() == 1


def test_memory_cleanup_blocks_digest_source_membership_drift(tmp_path, db_session):
    bundle_dir, manifest_sha256, ids = _build_bundle(tmp_path, db_session)
    bundle = load_cleanup_bundle(
        bundle_dir,
        expected_manifest_sha256=manifest_sha256,
    )
    db_session.add(MemoryDigest(
        user_id="cleanup-user",
        session_id="private_cleanup-user",
        digest_date="2026-07-18",
        level=0,
        content="报告生成后新增的同源摘要",
        meta_json=json.dumps({
            "schema_version": 2,
            "status": "active",
            "generator": "llm",
            "llm_status": "success",
            "source_id": ids["digest_source_id"],
            "summary_type": "detailed_digest",
        }),
    ))
    db_session.commit()

    with pytest.raises(MemoryCleanupError, match="memory_digest_source_membership_drift"):
        preview_memory_cleanup(db_session, bundle)


def test_memory_cleanup_blocks_missing_persona_snapshot_target(tmp_path, db_session):
    bundle_dir, manifest_sha256, ids = _build_bundle(tmp_path, db_session)
    bundle = load_cleanup_bundle(
        bundle_dir,
        expected_manifest_sha256=manifest_sha256,
    )
    db_session.delete(db_session.get(Persona, ids["persona_id"]))
    db_session.commit()

    with pytest.raises(MemoryCleanupError, match="persona_snapshot_target_missing"):
        preview_memory_cleanup(db_session, bundle)


def test_memory_cleanup_rolls_back_all_entities_on_failure(
    tmp_path,
    db_session,
    monkeypatch,
):
    from core import memory_cleanup

    bundle_dir, manifest_sha256, ids = _build_bundle(tmp_path, db_session)
    bundle = load_cleanup_bundle(
        bundle_dir,
        expected_manifest_sha256=manifest_sha256,
    )
    original = memory_cleanup._archive_meta
    calls = 0

    def fail_after_partial_changes(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise RuntimeError("simulated_failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(memory_cleanup, "_archive_meta", fail_after_partial_changes)
    with pytest.raises(RuntimeError, match="simulated_failure"):
        apply_memory_cleanup(db_session, bundle, actor="test-admin")

    db_session.expire_all()
    assert db_session.get(Persona, ids["persona_id"]).status == "active"
    assert db_session.get(PersonaFact, ids["fact_id"]).status == "active"
    assert db_session.get(PersonaBehavior, ids["behavior_id"]).status == "active"
    assert db_session.get(GroupMemory, ids["group_id"]).status == "active"
    assert all(
        json.loads(db_session.get(MemoryDigest, item).meta_json)["status"] == "active"
        for item in ids["digest_ids"]
    )
    assert db_session.query(MemoryCleanupRun).count() == 0
    assert db_session.query(AdminAuditLog).filter_by(action="apply_memory_cleanup").count() == 0
