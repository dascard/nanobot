"""基于已审核 dry-run 清单执行生产记忆归档。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

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
from core.time_utils import db_now_naive


CLEANUP_VERSION = "20260718_memory_governance_v1"
_MANIFEST_FILES = frozenset({
    "group_memory_candidates.json",
    "memory_digest_candidates.json",
    "persona_candidates.json",
    "report.json",
    "report.md",
})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SUBJECTIVE_GROUP_TYPES = frozenset({"style", "relationship", "event", "slang"})
_OBJECTIVE_GROUP_TYPES = frozenset({"topic", "preference"})


class MemoryCleanupError(RuntimeError):
    """清单、数据库状态或执行前提不满足。"""


@dataclass(frozen=True)
class CleanupBundle:
    root: Path
    manifest_sha256: str
    report_sha256: str
    generated_at_utc: str
    archive_persona_user_fingerprints: frozenset[str]
    archive_fact_ids: tuple[int, ...]
    archive_behavior_ids: tuple[int, ...]
    archive_group_ids: tuple[int, ...]
    archive_digest_ids: tuple[int, ...]
    archive_digest_source_fingerprints: frozenset[str]
    stale_index_ids: tuple[int, ...]
    persona_fact_details: dict[int, dict[str, Any]]
    group_details: dict[int, dict[str, Any]]
    digest_details: dict[int, dict[str, Any]]

    @property
    def target_counts(self) -> dict[str, int]:
        return {
            "persona_snapshots": len(self.archive_persona_user_fingerprints),
            "persona_facts": len(self.archive_fact_ids),
            "persona_behaviors": len(self.archive_behavior_ids),
            "group_memories": len(self.archive_group_ids),
            "memory_digests": len(self.archive_digest_ids),
            "memory_digest_sources": len(self.archive_digest_source_fingerprints),
            "semantic_index_candidates": len(self.stale_index_ids),
        }


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_bytes(root: Path, name: str) -> bytes:
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise MemoryCleanupError(f"bundle_file_invalid:{name}")
    if path.stat().st_size > 16 * 1024 * 1024:
        raise MemoryCleanupError(f"bundle_file_too_large:{name}")
    return path.read_bytes()


def _read_json(root: Path, name: str) -> Any:
    try:
        return json.loads(_read_bytes(root, name).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MemoryCleanupError(f"bundle_json_invalid:{name}") from exc


def _positive_ids(value: Any, field: str) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise MemoryCleanupError(f"bundle_field_invalid:{field}")
    result: list[int] = []
    for item in value:
        if isinstance(item, bool):
            raise MemoryCleanupError(f"bundle_id_invalid:{field}")
        try:
            parsed = int(item)
        except (TypeError, ValueError) as exc:
            raise MemoryCleanupError(f"bundle_id_invalid:{field}") from exc
        if parsed <= 0:
            raise MemoryCleanupError(f"bundle_id_invalid:{field}")
        result.append(parsed)
    if len(result) != len(set(result)):
        raise MemoryCleanupError(f"bundle_id_duplicate:{field}")
    return tuple(sorted(result))


def _detail_map(value: Any, field: str) -> dict[int, dict[str, Any]]:
    if not isinstance(value, list):
        raise MemoryCleanupError(f"bundle_field_invalid:{field}")
    result: dict[int, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            raise MemoryCleanupError(f"bundle_detail_invalid:{field}")
        ids = _positive_ids([item.get("id")], field)
        row_id = ids[0]
        if row_id in result:
            raise MemoryCleanupError(f"bundle_detail_duplicate:{field}")
        result[row_id] = item
    return result


def _fingerprints(value: Any, field: str) -> frozenset[str]:
    if not isinstance(value, list):
        raise MemoryCleanupError(f"bundle_field_invalid:{field}")
    result = frozenset(str(item or "") for item in value)
    if (
        len(result) != len(value)
        or any(not re.fullmatch(r"[0-9a-f]{16}", item) for item in result)
    ):
        raise MemoryCleanupError(f"bundle_fingerprint_invalid:{field}")
    return result


def load_cleanup_bundle(
    bundle_dir: str | Path,
    *,
    expected_manifest_sha256: str,
) -> CleanupBundle:
    """校验导出包完整性并加载不含业务正文的候选清单。"""

    expected = str(expected_manifest_sha256 or "").strip().lower()
    if not _SHA256_RE.fullmatch(expected):
        raise MemoryCleanupError("manifest_sha256_invalid")
    root = Path(bundle_dir).resolve()
    if root.is_symlink() or not root.is_dir():
        raise MemoryCleanupError("bundle_dir_invalid")

    manifest_bytes = _read_bytes(root, "sha256_manifest.json")
    actual_manifest_sha256 = _sha256_bytes(manifest_bytes)
    if actual_manifest_sha256 != expected:
        raise MemoryCleanupError("manifest_sha256_mismatch")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MemoryCleanupError("manifest_json_invalid") from exc
    if not isinstance(manifest, dict) or frozenset(manifest) != _MANIFEST_FILES:
        raise MemoryCleanupError("manifest_file_set_invalid")
    for name in sorted(_MANIFEST_FILES):
        expected_file_hash = str(manifest.get(name) or "").lower()
        if not _SHA256_RE.fullmatch(expected_file_hash):
            raise MemoryCleanupError(f"manifest_entry_invalid:{name}")
        if _sha256_bytes(_read_bytes(root, name)) != expected_file_hash:
            raise MemoryCleanupError(f"bundle_file_hash_mismatch:{name}")

    report = _read_json(root, "report.json")
    persona = _read_json(root, "persona_candidates.json")
    group = _read_json(root, "group_memory_candidates.json")
    digest = _read_json(root, "memory_digest_candidates.json")
    if not all(isinstance(item, dict) for item in (report, persona, group, digest)):
        raise MemoryCleanupError("bundle_root_invalid")
    safety = report.get("safety")
    if not isinstance(safety, dict) or safety != {
        "business_content_exported": False,
        "chat_logs_preserved": True,
        "database_query_only": True,
        "writes_performed": False,
    }:
        raise MemoryCleanupError("dry_run_safety_contract_invalid")
    if str(report.get("mode") or "") != "dry_run_read_only":
        raise MemoryCleanupError("dry_run_mode_invalid")

    persona_fingerprints = _fingerprints(
        persona.get("archive_persona_user_fingerprints"),
        "archive_persona_user_fingerprints",
    )
    fact_ids = _positive_ids(persona.get("archive_fact_ids"), "archive_fact_ids")
    behavior_ids = _positive_ids(
        persona.get("archive_behavior_ids"),
        "archive_behavior_ids",
    )
    group_ids = _positive_ids(group.get("archive_ids"), "archive_group_ids")
    digest_ids = _positive_ids(digest.get("archive_row_ids"), "archive_digest_ids")
    stale_index_ids = _positive_ids(
        digest.get("stale_semantic_index_item_ids"),
        "stale_semantic_index_item_ids",
    )
    source_fingerprints = _fingerprints(
        digest.get("archive_source_fingerprints"),
        "archive_source_fingerprints",
    )

    fact_details = _detail_map(persona.get("facts"), "persona_facts")
    group_details = _detail_map(group.get("items"), "group_memories")
    digest_details = _detail_map(digest.get("items"), "memory_digests")
    if not set(fact_ids).issubset(fact_details):
        raise MemoryCleanupError("persona_fact_details_incomplete")
    if not set(group_ids).issubset(group_details):
        raise MemoryCleanupError("group_details_incomplete")
    if not set(digest_ids).issubset(digest_details):
        raise MemoryCleanupError("digest_details_incomplete")

    report_persona = report.get("persona") if isinstance(report.get("persona"), dict) else {}
    report_group = report.get("group_memory") if isinstance(report.get("group_memory"), dict) else {}
    report_digest = report.get("memory_digest") if isinstance(report.get("memory_digest"), dict) else {}
    expected_counts = {
        "persona_snapshots": report_persona.get("proposed_archive_snapshot_count"),
        "persona_facts": report_persona.get("proposed_archive_fact_count"),
        "persona_behaviors": report_persona.get("proposed_archive_behavior_count"),
        "group_memories": report_group.get("proposed_archive_count"),
        "memory_digests": report_digest.get("proposed_archive_rows"),
        "memory_digest_sources": report_digest.get("proposed_archive_sources"),
        "semantic_index_candidates": report_digest.get("stale_memory_digest_index_items"),
    }
    actual_counts = {
        "persona_snapshots": len(persona_fingerprints),
        "persona_facts": len(fact_ids),
        "persona_behaviors": len(behavior_ids),
        "group_memories": len(group_ids),
        "memory_digests": len(digest_ids),
        "memory_digest_sources": len(source_fingerprints),
        "semantic_index_candidates": len(stale_index_ids),
    }
    if expected_counts != actual_counts:
        raise MemoryCleanupError("bundle_report_count_mismatch")

    return CleanupBundle(
        root=root,
        manifest_sha256=actual_manifest_sha256,
        report_sha256=str(manifest["report.json"]),
        generated_at_utc=str(report.get("generated_at_utc") or ""),
        archive_persona_user_fingerprints=persona_fingerprints,
        archive_fact_ids=fact_ids,
        archive_behavior_ids=behavior_ids,
        archive_group_ids=group_ids,
        archive_digest_ids=digest_ids,
        archive_digest_source_fingerprints=source_fingerprints,
        stale_index_ids=stale_index_ids,
        persona_fact_details=fact_details,
        group_details=group_details,
        digest_details=digest_details,
    )


def _safe_json(raw: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(raw or ""))
    except Exception:
        return fallback


def _normalize_ids(raw: Any) -> dict[str, Any]:
    value = _safe_json(raw, None)
    if not isinstance(value, list):
        return {
            "ids": [],
            "array_length": -1,
            "unique_length": 0,
            "non_integer": True,
        }
    ids: list[int] = []
    non_integer = False
    for item in value:
        if isinstance(item, bool):
            non_integer = True
            continue
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            non_integer = True
            continue
        if parsed <= 0:
            non_integer = True
            continue
        ids.append(parsed)
    unique = list(dict.fromkeys(ids))
    return {
        "ids": unique,
        "array_length": len(value),
        "unique_length": len(unique),
        "non_integer": non_integer,
    }


def _chunks(values: Iterable[int], size: int = 800) -> Iterable[list[int]]:
    items = sorted(set(int(item) for item in values))
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _rows_by_ids(db: Session, model: Any, ids: Iterable[int]) -> dict[int, Any]:
    result: dict[int, Any] = {}
    for part in _chunks(ids):
        for row in db.query(model).filter(model.id.in_(part)).all():
            result[int(row.id)] = row
    return result


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


def _digest_status(meta: dict[str, Any]) -> str:
    explicit = str(meta.get("status") or "").strip()
    if explicit:
        return explicit
    try:
        if int(meta.get("schema_version") or 0) != 2:
            return "legacy"
    except (TypeError, ValueError):
        return "legacy"
    return "active"


def _digest_source_key(row: MemoryDigest, meta: dict[str, Any]) -> str:
    source_id = str(meta.get("source_id") or "").strip()
    if source_id:
        return source_id
    return "|".join(str(getattr(row, key, "") or "") for key in (
        "session_id",
        "digest_date",
        "source_start_log_id",
        "source_end_log_id",
    ))


def _digest_card_type(row: MemoryDigest, meta: dict[str, Any]) -> str:
    if int(row.level or 0) != 2:
        return ""
    card = meta.get("recall_card")
    if not isinstance(card, dict):
        cards = meta.get("recall_cards")
        card = cards[0] if isinstance(cards, list) and len(cards) == 1 and isinstance(cards[0], dict) else {}
    return str(card.get("type") or "missing").strip().lower()


def _same_archive_marker(raw: Any, bundle_sha256: str) -> bool:
    meta = _safe_json(raw, {})
    if not isinstance(meta, dict):
        return False
    marker = meta.get("memory_cleanup_archive")
    return (
        isinstance(marker, dict)
        and str(marker.get("bundle_sha256") or "") == bundle_sha256
    )


def _current_digest_detail(row: MemoryDigest) -> tuple[dict[str, Any], dict[str, Any]]:
    meta = _safe_json(row.meta_json, {})
    if not isinstance(meta, dict):
        meta = {}
    source_key = _digest_source_key(row, meta)
    return meta, {
        "id": int(row.id),
        "source_fingerprint": _fingerprint(source_key),
        "level": int(row.level or 0),
        "status": _digest_status(meta),
        "schema_version": meta.get("schema_version", "missing"),
        "generator": str(meta.get("generator") or "missing"),
        "llm_status": str(meta.get("llm_status") or "missing"),
        "summary_type": str(meta.get("summary_type") or "missing"),
        "card_type": _digest_card_type(row, meta),
        "content_chars": len(str(row.content or "")),
    }


def _assert_detail_matches(
    entity: str,
    row_id: int,
    current: dict[str, Any],
    expected: dict[str, Any],
    fields: Iterable[str],
) -> None:
    for field in fields:
        if current.get(field) != expected.get(field):
            raise MemoryCleanupError(f"{entity}_drift:{row_id}:{field}")


def _persona_fact_reliable(db: Session, row: PersonaFact, evidence: dict[str, Any]) -> bool:
    ids = evidence["ids"]
    if not ids or evidence["non_integer"]:
        return False
    found: set[int] = set()
    for part in _chunks(ids):
        found.update(
            int(item[0])
            for item in db.query(ChatLog.id).filter(
                ChatLog.id.in_(part),
                ChatLog.user_id == row.user_id,
                ChatLog.role == "user",
            ).all()
        )
    return found == set(ids)


def _group_aliases(row: GroupMemory) -> set[str]:
    from core.chat_stream_identity import (
        identity_storage_aliases,
        parse_compatibility_chat_stream_identity,
    )

    identity = parse_compatibility_chat_stream_identity(
        str(row.chat_stream_id or row.group_id or ""),
    )
    if identity is None or identity.chat_type != "group":
        return set()
    return set(identity_storage_aliases(
        identity,
        include_raw_group_id=True,
    ))


def _group_evidence_reliable(
    db: Session,
    row: GroupMemory,
    evidence: dict[str, Any],
) -> bool:
    ids = evidence["ids"]
    if not ids or evidence["non_integer"]:
        return False
    aliases = _group_aliases(row)
    found: set[int] = set()
    for part in _chunks(ids):
        found.update(
            int(item[0])
            for item in db.query(ChatLog.id).filter(
                ChatLog.id.in_(part),
                ChatLog.role.in_(("user", "ambient")),
                ChatLog.session_id.in_(sorted(aliases)),
            ).all()
        )
    return found == set(ids)


def _validate_persona_targets(
    db: Session,
    bundle: CleanupBundle,
) -> tuple[dict[int, PersonaFact], dict[int, PersonaBehavior], dict[str, Persona]]:
    facts = _rows_by_ids(db, PersonaFact, bundle.archive_fact_ids)
    behaviors = _rows_by_ids(db, PersonaBehavior, bundle.archive_behavior_ids)
    if set(facts) != set(bundle.archive_fact_ids):
        raise MemoryCleanupError("persona_fact_target_missing")
    if set(behaviors) != set(bundle.archive_behavior_ids):
        raise MemoryCleanupError("persona_behavior_target_missing")

    for row_id, row in facts.items():
        if row.status == "archived" and _same_archive_marker(
            row.candidate_meta_json,
            bundle.manifest_sha256,
        ):
            continue
        evidence = _normalize_ids(row.evidence_log_ids_json)
        reliable = _persona_fact_reliable(db, row, evidence)
        if reliable:
            raise MemoryCleanupError(f"persona_fact_no_longer_matches_rule:{row_id}")
        current = {
            "status": str(row.status or "missing"),
            "inject_policy": str(row.inject_policy or "missing"),
            "memory_type": str(row.memory_type or "stable_preference"),
            "stored_evidence_count": int(row.evidence_count or 0),
            "evidence_array_length": evidence["array_length"],
            "evidence_unique_length": evidence["unique_length"],
            "reliable_user_chatlog_backlink": reliable,
        }
        _assert_detail_matches(
            "persona_fact",
            row_id,
            current,
            bundle.persona_fact_details[row_id],
            current,
        )

    for row_id, row in behaviors.items():
        if str(row.status or "") == "archived" and _same_archive_marker(
            row.archive_meta_json,
            bundle.manifest_sha256,
        ):
            continue
        if str(row.status or "") != "active":
            raise MemoryCleanupError(f"persona_behavior_drift:{row_id}:status")

    personas: dict[str, Persona] = {}
    if bundle.archive_persona_user_fingerprints:
        for row in db.query(Persona).order_by(Persona.user_id.asc()).all():
            fingerprint = _fingerprint(row.user_id)
            if fingerprint not in bundle.archive_persona_user_fingerprints:
                continue
            if fingerprint in personas:
                raise MemoryCleanupError("persona_snapshot_fingerprint_collision")
            personas[fingerprint] = row
    if set(personas) != set(bundle.archive_persona_user_fingerprints):
        raise MemoryCleanupError("persona_snapshot_target_missing")
    for row in personas.values():
        if str(row.status or "") == "archived" and _same_archive_marker(
            row.archive_meta_json,
            bundle.manifest_sha256,
        ):
            continue
        if str(row.status or "") not in {"active", "review"}:
            raise MemoryCleanupError("persona_snapshot_state_drift")
    return facts, behaviors, personas


def _validate_group_targets(
    db: Session,
    bundle: CleanupBundle,
) -> dict[int, GroupMemory]:
    rows = _rows_by_ids(db, GroupMemory, bundle.archive_group_ids)
    if set(rows) != set(bundle.archive_group_ids):
        raise MemoryCleanupError("group_memory_target_missing")
    for row_id, row in rows.items():
        if str(row.status or "") == "archived" and _same_archive_marker(
            row.meta_json,
            bundle.manifest_sha256,
        ):
            continue
        evidence = _normalize_ids(row.evidence_log_ids_json)
        memory_type = str(row.memory_type or "")
        reliable = (
            _group_evidence_reliable(db, row, evidence)
            if evidence["array_length"] <= 8
            else False
        )
        matches_rule = memory_type in _SUBJECTIVE_GROUP_TYPES or (
            memory_type in _OBJECTIVE_GROUP_TYPES
            and (evidence["array_length"] > 8 or not reliable)
        )
        if not matches_rule:
            raise MemoryCleanupError(f"group_memory_no_longer_matches_rule:{row_id}")
        meta = _safe_json(row.meta_json, {})
        raw_count = int(meta.get("raw_count") or 0) if isinstance(meta, dict) else 0
        current = {
            "memory_type": memory_type,
            "status": str(row.status or "missing"),
            "inject_policy": str(row.inject_policy or "missing"),
            "stored_evidence_count": int(row.evidence_count or 0),
            "evidence_array_length": evidence["array_length"],
            "evidence_unique_length": evidence["unique_length"],
            "source_raw_count": raw_count,
        }
        _assert_detail_matches(
            "group_memory",
            row_id,
            current,
            bundle.group_details[row_id],
            current,
        )
    return rows


def _validate_digest_targets(
    db: Session,
    bundle: CleanupBundle,
) -> dict[int, MemoryDigest]:
    rows = _rows_by_ids(db, MemoryDigest, bundle.archive_digest_ids)
    if set(rows) != set(bundle.archive_digest_ids):
        raise MemoryCleanupError("memory_digest_target_missing")

    all_rows = db.query(MemoryDigest).order_by(MemoryDigest.id.asc()).all()
    current_target_ids: set[int] = set()
    for row in all_rows:
        _meta, current = _current_digest_detail(row)
        if current["source_fingerprint"] in bundle.archive_digest_source_fingerprints:
            current_target_ids.add(int(row.id))
    if current_target_ids != set(bundle.archive_digest_ids):
        raise MemoryCleanupError("memory_digest_source_membership_drift")

    fields = (
        "source_fingerprint",
        "level",
        "status",
        "schema_version",
        "generator",
        "llm_status",
        "summary_type",
        "card_type",
        "content_chars",
    )
    for row_id, row in rows.items():
        meta, current = _current_digest_detail(row)
        if current["status"] == "archived" and _same_archive_marker(
            row.meta_json,
            bundle.manifest_sha256,
        ):
            continue
        _assert_detail_matches(
            "memory_digest",
            row_id,
            current,
            bundle.digest_details[row_id],
            fields,
        )
        if current["source_fingerprint"] not in bundle.archive_digest_source_fingerprints:
            raise MemoryCleanupError(f"memory_digest_rule_drift:{row_id}")
    return rows


def preview_memory_cleanup(db: Session, bundle: CleanupBundle) -> dict[str, Any]:
    """在当前事务中复核候选清单；不执行写入。"""

    facts, behaviors, personas = _validate_persona_targets(db, bundle)
    groups = _validate_group_targets(db, bundle)
    digests = _validate_digest_targets(db, bundle)
    index_rows = _rows_by_ids(db, SemanticIndexItem, bundle.stale_index_ids)
    active_index_candidates = sum(
        str(row.status or "") == "active"
        for row in index_rows.values()
    )
    already = {
        "persona_snapshots": sum(
            str(row.status or "") == "archived"
            and _same_archive_marker(row.archive_meta_json, bundle.manifest_sha256)
            for row in personas.values()
        ),
        "persona_facts": sum(
            str(row.status or "") == "archived"
            and _same_archive_marker(row.candidate_meta_json, bundle.manifest_sha256)
            for row in facts.values()
        ),
        "persona_behaviors": sum(
            str(row.status or "") == "archived"
            and _same_archive_marker(row.archive_meta_json, bundle.manifest_sha256)
            for row in behaviors.values()
        ),
        "group_memories": sum(
            str(row.status or "") == "archived"
            and _same_archive_marker(row.meta_json, bundle.manifest_sha256)
            for row in groups.values()
        ),
        "memory_digests": sum(
            _digest_status(_safe_json(row.meta_json, {})) == "archived"
            and _same_archive_marker(row.meta_json, bundle.manifest_sha256)
            for row in digests.values()
        ),
    }
    return {
        "cleanup_version": CLEANUP_VERSION,
        "bundle_sha256": bundle.manifest_sha256,
        "report_sha256": bundle.report_sha256,
        "report_generated_at_utc": bundle.generated_at_utc,
        "target_counts": bundle.target_counts,
        "already_archived": already,
        "chat_logs_preserved_count": int(db.query(ChatLog).count()),
        "active_semantic_index_candidates": int(active_index_candidates),
        "semantic_index_backfill_required": bool(active_index_candidates),
        "validation": "passed",
        "writes_performed": False,
    }


def _archive_meta(
    raw: Any,
    *,
    bundle: CleanupBundle,
    run_id: int,
    audit_log_id: int,
    previous: dict[str, Any],
) -> str:
    meta = _safe_json(raw, {})
    if not isinstance(meta, dict):
        meta = {}
    meta["memory_cleanup_archive"] = {
        "cleanup_version": CLEANUP_VERSION,
        "bundle_sha256": bundle.manifest_sha256,
        "run_id": int(run_id),
        "audit_log_id": int(audit_log_id),
        "archived_at_utc": datetime.now(timezone.utc).isoformat(),
        "previous": previous,
    }
    return json.dumps(meta, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _begin_immediate_if_sqlite(db: Session) -> None:
    bind = db.get_bind()
    if str(getattr(getattr(bind, "dialect", None), "name", "")) == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))


def apply_memory_cleanup(
    db: Session,
    bundle: CleanupBundle,
    *,
    actor: str,
) -> dict[str, Any]:
    """单事务归档清单；相同 bundle 重放时直接返回原执行结果。"""

    normalized_actor = str(actor or "cli").strip()[:255] or "cli"
    try:
        _begin_immediate_if_sqlite(db)
        existing = db.query(MemoryCleanupRun).filter(
            MemoryCleanupRun.bundle_sha256 == bundle.manifest_sha256,
        ).first()
        if existing is not None:
            if str(existing.status or "") != "applied":
                raise MemoryCleanupError("cleanup_run_not_applied")
            result = _safe_json(existing.result_json, {})
            if not isinstance(result, dict):
                raise MemoryCleanupError("cleanup_run_result_invalid")
            db.rollback()
            return {**result, "idempotent_replay": True}

        preview = preview_memory_cleanup(db, bundle)
        chat_logs_before = int(preview["chat_logs_preserved_count"])
        run = MemoryCleanupRun(
            cleanup_version=CLEANUP_VERSION,
            bundle_sha256=bundle.manifest_sha256,
            status="applying",
            actor=normalized_actor,
            target_counts_json=json.dumps(
                preview["target_counts"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        db.add(run)
        db.flush()
        audit = AdminAuditLog(
            admin_user=normalized_actor,
            action="apply_memory_cleanup",
            target_type="memory_cleanup_bundle",
            target_id=bundle.manifest_sha256,
            detail_json="{}",
            ip_address="local-cli",
        )
        db.add(audit)
        db.flush()

        facts, behaviors, personas = _validate_persona_targets(db, bundle)
        groups = _validate_group_targets(db, bundle)
        digests = _validate_digest_targets(db, bundle)
        changed = {
            "persona_snapshots": 0,
            "persona_facts": 0,
            "persona_behaviors": 0,
            "group_memories": 0,
            "memory_digests": 0,
        }
        archive_reason = f"memory_cleanup:{CLEANUP_VERSION}"

        for row in personas.values():
            if str(row.status or "") == "archived" and _same_archive_marker(
                row.archive_meta_json,
                bundle.manifest_sha256,
            ):
                continue
            row.archive_meta_json = _archive_meta(
                row.archive_meta_json,
                bundle=bundle,
                run_id=run.id,
                audit_log_id=audit.id,
                previous={"status": str(row.status or "")},
            )
            row.status = "archived"
            changed["persona_snapshots"] += 1

        for row in facts.values():
            if str(row.status or "") == "archived" and _same_archive_marker(
                row.candidate_meta_json,
                bundle.manifest_sha256,
            ):
                continue
            row.candidate_meta_json = _archive_meta(
                row.candidate_meta_json,
                bundle=bundle,
                run_id=run.id,
                audit_log_id=audit.id,
                previous={
                    "status": str(row.status or ""),
                    "inject_policy": str(row.inject_policy or ""),
                    "confidence": str(row.confidence or ""),
                    "disabled_reason": str(row.disabled_reason or ""),
                },
            )
            row.status = "archived"
            row.inject_policy = "never"
            row.confidence = "归档"
            row.disabled_reason = archive_reason
            changed["persona_facts"] += 1

        for row in behaviors.values():
            if str(row.status or "") == "archived" and _same_archive_marker(
                row.archive_meta_json,
                bundle.manifest_sha256,
            ):
                continue
            row.archive_meta_json = _archive_meta(
                row.archive_meta_json,
                bundle=bundle,
                run_id=run.id,
                audit_log_id=audit.id,
                previous={"status": str(row.status or "")},
            )
            row.status = "archived"
            changed["persona_behaviors"] += 1

        for row in groups.values():
            if str(row.status or "") == "archived" and _same_archive_marker(
                row.meta_json,
                bundle.manifest_sha256,
            ):
                continue
            row.meta_json = _archive_meta(
                row.meta_json,
                bundle=bundle,
                run_id=run.id,
                audit_log_id=audit.id,
                previous={
                    "status": str(row.status or ""),
                    "inject_policy": str(row.inject_policy or ""),
                    "disabled_reason": str(row.disabled_reason or ""),
                },
            )
            row.status = "archived"
            row.inject_policy = "never"
            row.disabled_reason = archive_reason
            changed["group_memories"] += 1

        for row in digests.values():
            meta = _safe_json(row.meta_json, {})
            if not isinstance(meta, dict):
                meta = {}
            if _digest_status(meta) == "archived" and _same_archive_marker(
                row.meta_json,
                bundle.manifest_sha256,
            ):
                continue
            row.meta_json = _archive_meta(
                row.meta_json,
                bundle=bundle,
                run_id=run.id,
                audit_log_id=audit.id,
                previous={"status": _digest_status(meta)},
            )
            archived_meta = _safe_json(row.meta_json, {})
            archived_meta["status"] = "archived"
            row.meta_json = json.dumps(
                archived_meta,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            changed["memory_digests"] += 1

        db.flush()
        chat_logs_after = int(db.query(ChatLog).count())
        if chat_logs_after != chat_logs_before:
            raise MemoryCleanupError("chat_log_count_changed")

        result = {
            "cleanup_version": CLEANUP_VERSION,
            "bundle_sha256": bundle.manifest_sha256,
            "report_sha256": bundle.report_sha256,
            "run_id": int(run.id),
            "audit_log_id": int(audit.id),
            "target_counts": preview["target_counts"],
            "changed_counts": changed,
            "chat_logs_before": chat_logs_before,
            "chat_logs_after": chat_logs_after,
            "semantic_index_backfill_required": bool(
                preview["active_semantic_index_candidates"]
            ),
            "active_semantic_index_candidates": int(
                preview["active_semantic_index_candidates"]
            ),
            "applied_at_utc": datetime.now(timezone.utc).isoformat(),
            "idempotent_replay": False,
        }
        audit.detail_json = json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        run.audit_log_id = int(audit.id)
        run.result_json = audit.detail_json
        run.status = "applied"
        run.applied_at = db_now_naive()
        db.commit()
        return result
    except BaseException:
        db.rollback()
        raise
