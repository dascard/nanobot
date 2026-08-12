from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


def _write_template(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def _store(tmp_path: Path):
    from core.prompt_v2.template_baseline import TemplateBaselineStore

    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    state_dir = tmp_path / "template-state"
    return (
        TemplateBaselineStore(
            default_dir=default_dir,
            runtime_dir=runtime_dir,
            state_dir=state_dir,
        ),
        default_dir,
        runtime_dir,
        state_dir,
    )


def _adopted_store(
    tmp_path: Path,
    *,
    content: bytes = b"---\nversion: 1\n---\nbase\n",
):
    store, default_dir, runtime_dir, state_dir = _store(tmp_path)
    default_path = default_dir / "chat" / "main.md"
    runtime_path = runtime_dir / "chat" / "main.md"
    _write_template(default_path, content)
    _write_template(runtime_path, content)
    store.adopt_in_sync(
        "chat/main",
        baseline_version="1",
        modified_by="test",
    )
    return store, default_path, runtime_path, state_dir


def test_template_baseline_first_audit_requires_explicit_adoption(tmp_path):
    store, default_dir, runtime_dir, state_dir = _store(tmp_path)
    content = b"---\r\nversion: 1\r\n---\r\nhello\r\n"
    _write_template(default_dir / "chat" / "main.md", content)
    _write_template(runtime_dir / "chat" / "main.md", content)

    report = store.audit("chat/main")

    assert report.drift_status == "untracked_legacy"
    assert report.runtime_sha256 == hashlib.sha256(content).hexdigest()
    assert report.default_sha256 == hashlib.sha256(content).hexdigest()
    assert report.baseline_sha256 is None
    assert not (state_dir / "manifest.json").exists()


def test_template_baseline_adopt_in_sync_records_verified_raw_blob(tmp_path):
    store, default_path, runtime_path, state_dir = _adopted_store(tmp_path)
    raw = default_path.read_bytes()
    raw_sha256 = hashlib.sha256(raw).hexdigest()

    report = store.audit("chat/main")
    manifest = store.manifest_snapshot()

    assert report.drift_status == "in_sync"
    assert report.baseline_version == "1"
    assert report.baseline_sha256 == raw_sha256
    assert report.default_sha256 == raw_sha256
    assert report.runtime_sha256 == raw_sha256
    assert store.read_baseline_bytes("chat/main") == raw
    entry = manifest["templates"]["chat/main"]
    assert entry["baseline_blob_sha256"] == raw_sha256
    assert entry["baseline_sha256"] == raw_sha256
    assert entry["canonical_sha256"] == raw_sha256
    assert entry["runtime_sha256"] == raw_sha256
    assert entry["modified_by"] == "test"
    assert (state_dir / "blobs" / raw_sha256).read_bytes() == raw
    assert runtime_path.read_bytes() == raw


def test_template_baseline_adopt_rejects_different_runtime_without_writes(tmp_path):
    from core.prompt_v2.template_baseline import TemplateBaselineError

    store, default_dir, runtime_dir, state_dir = _store(tmp_path)
    _write_template(default_dir / "chat" / "main.md", b"default\n")
    runtime_path = runtime_dir / "chat" / "main.md"
    _write_template(runtime_path, b"runtime\n")
    before = runtime_path.read_bytes()

    with pytest.raises(TemplateBaselineError, match="内容不一致"):
        store.adopt_in_sync(
            "chat/main",
            baseline_version="1",
            modified_by="test",
        )

    assert runtime_path.read_bytes() == before
    assert not (state_dir / "manifest.json").exists()
    assert not (state_dir / "blobs").exists()


@pytest.mark.parametrize(
    ("runtime_body", "canonical_body", "expected"),
    [
        (b"base\n", b"base\n", "in_sync"),
        (b"base\n", b"canonical-v2\n", "upgrade_available"),
        (b"local\n", b"base\n", "local_override"),
        (b"local\n", b"canonical-v2\n", "diverged"),
    ],
)
def test_template_baseline_classifies_tracked_drift_states(
    tmp_path,
    runtime_body,
    canonical_body,
    expected,
):
    store, default_path, runtime_path, _state_dir = _adopted_store(
        tmp_path,
        content=b"base\n",
    )
    default_path.write_bytes(canonical_body)
    runtime_path.write_bytes(runtime_body)

    assert store.audit("chat/main").drift_status == expected


def test_template_baseline_classifies_runtime_missing(tmp_path):
    store, default_path, runtime_path, _state_dir = _adopted_store(tmp_path)
    runtime_path.unlink()

    report = store.audit("chat/main")

    assert report.drift_status == "runtime_missing"
    assert report.default_sha256 == hashlib.sha256(default_path.read_bytes()).hexdigest()
    assert report.runtime_sha256 is None


@pytest.mark.parametrize("damage", ["missing", "content", "manifest_pointer"])
def test_template_baseline_marks_invalid_when_blob_integrity_is_broken(
    tmp_path,
    damage,
):
    from core.prompt_v2.template_baseline import TemplateBlobIntegrityError

    store, _default_path, _runtime_path, state_dir = _adopted_store(tmp_path)
    manifest_path = state_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = manifest["templates"]["chat/main"]
    blob_path = state_dir / "blobs" / entry["baseline_blob_sha256"]
    if damage == "missing":
        blob_path.unlink()
    elif damage == "content":
        blob_path.write_bytes(b"corrupted\n")
    else:
        entry["baseline_blob_sha256"] = "0" * 64
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

    report = store.audit("chat/main")

    assert report.drift_status == "invalid"
    assert report.invalid_component == "baseline_state"
    assert report.invalid_reason
    with pytest.raises(TemplateBlobIntegrityError):
        store.read_baseline_bytes("chat/main")


@pytest.mark.parametrize(
    "template_key",
    ("chat/flow", "tasks/private_decision"),
)
def test_historical_baseline_does_not_need_to_match_current_runtime_contract(
    tmp_path,
    template_key,
):
    store, default_dir, runtime_dir, _state_dir = _store(tmp_path)
    suffix = ".json" if template_key == "chat/flow" else ".md"
    relative_path = Path(f"{template_key}{suffix}")
    current = (Path("prompts.v2.default") / relative_path).read_bytes()
    _write_template(default_dir / relative_path, current)
    _write_template(runtime_dir / relative_path, current)
    store.adopt_in_sync(
        template_key,
        baseline_version="current",
        modified_by="test",
    )

    if template_key == "chat/flow":
        legacy_flow = json.loads(current.decode("utf-8"))
        private_edge = next(
            edge
            for edge in legacy_flow["edges"]
            if edge["from"] == "base_contract"
            and edge["to"] == "private_policy"
        )
        private_edge["platforms"].remove("external_private")
        legacy_flow["nodes"].append(
            {
                "id": "effort_constraint",
                "type": "runtime",
                "label": "system: effort_constraint",
                "runtime_key": "effort_constraint",
                "optional": True,
            }
        )
        history_edge = next(
            edge
            for edge in legacy_flow["edges"]
            if edge["from"] == "history_messages"
            and edge["to"] == "persona_reference"
        )
        history_edge["to"] = "effort_constraint"
        legacy_flow["edges"].append(
            {"from": "effort_constraint", "to": "persona_reference"}
        )
        legacy = (
            json.dumps(
                legacy_flow,
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
    else:
        legacy = current.replace(
            "输入是否带附件：{{ has_files }}\n\n".encode(),
            b"",
        )

    baseline_sha256 = store.install_blob_once(legacy)
    manifest = store.manifest_snapshot()
    entry = manifest["templates"][template_key]
    entry["baseline_version"] = "legacy"
    entry["baseline_sha256"] = baseline_sha256
    entry["baseline_blob_sha256"] = baseline_sha256
    store.write_manifest_snapshot(manifest)

    report = store.audit(template_key)

    assert report.drift_status == "in_sync"
    assert report.invalid_component is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: manifest.update({"unexpected": True}),
        lambda manifest: manifest["templates"]["chat/main"].update(
            {"baseline_sha256": "short"}
        ),
        lambda manifest: manifest["templates"]["chat/main"].update(
            {"unexpected": True}
        ),
        lambda manifest: manifest["lineage"].append("not-an-operation-id"),
    ],
)
def test_template_baseline_rejects_invalid_manifest_schema(tmp_path, mutate):
    store, _default_path, _runtime_path, state_dir = _adopted_store(tmp_path)
    manifest_path = state_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutate(manifest)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    report = store.audit("chat/main")

    assert report.drift_status == "invalid"
    assert report.invalid_component == "manifest_state"
    assert report.invalid_reason


def test_template_audit_all_includes_orphaned_tracked_manifest_entry(tmp_path):
    store, default_path, runtime_path, state_dir = _adopted_store(tmp_path)
    baseline_sha256 = store.manifest_snapshot()["templates"]["chat/main"][
        "baseline_blob_sha256"
    ]
    default_path.unlink()
    runtime_path.unlink()
    (state_dir / "blobs" / baseline_sha256).unlink()

    reports = {report.template_key: report for report in store.audit_all()}

    assert reports["chat/main"].drift_status == "invalid"
    assert reports["chat/main"].invalid_component == "baseline_state"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="平台不支持 FIFO")
def test_template_audit_all_includes_inactive_fifo_as_storage_invalid(tmp_path):
    store, _default_dir, runtime_dir, _state_dir = _store(tmp_path)
    fifo_path = runtime_dir / "tools" / "inactive_fifo" / "usage.md"
    fifo_path.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(fifo_path)

    reports = {report.template_key: report for report in store.audit_all()}

    assert reports["tools/inactive_fifo/usage"].drift_status == "invalid"
    assert reports["tools/inactive_fifo/usage"].invalid_component == "storage"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="平台不支持 FIFO")
@pytest.mark.parametrize(
    "surface",
    [
        "runtime",
        "canonical",
        "manifest",
        "baseline_blob",
        "migration_runtime",
        "migration_journal",
        "flow",
    ],
)
def test_template_readers_reject_fifo_without_blocking(tmp_path, surface):
    store, default_dir, runtime_dir, state_dir = _store(tmp_path)
    canonical = b"---\nversion: 1\nkind: chat\n---\ncanonical\n"
    default_path = default_dir / "chat" / "main.md"
    runtime_path = runtime_dir / "chat" / "main.md"
    _write_template(default_path, canonical)
    _write_template(runtime_path, canonical)

    if surface == "runtime":
        runtime_path.unlink()
        os.mkfifo(runtime_path)
    elif surface == "canonical":
        default_path.unlink()
        os.mkfifo(default_path)
    elif surface == "manifest":
        state_dir.mkdir(parents=True, exist_ok=True)
        os.mkfifo(state_dir / "manifest.json")
    elif surface == "baseline_blob":
        store.adopt_in_sync(
            "chat/main",
            baseline_version="1",
            modified_by="test",
        )
        baseline_sha256 = store.manifest_snapshot()["templates"]["chat/main"][
            "baseline_blob_sha256"
        ]
        blob_path = state_dir / "blobs" / baseline_sha256
        blob_path.unlink()
        os.mkfifo(blob_path)
    elif surface == "migration_runtime":
        runtime_path.unlink()
        os.mkfifo(runtime_path)
    elif surface == "migration_journal":
        journal_path = state_dir / "journals" / "pending.json"
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        os.mkfifo(journal_path)
    else:
        default_flow = default_dir / "chat" / "flow.json"
        runtime_flow = runtime_dir / "chat" / "flow.json"
        default_flow.write_bytes(Path("prompts.v2.default/chat/flow.json").read_bytes())
        runtime_flow.unlink(missing_ok=True)
        os.mkfifo(runtime_flow)

    probe = """
import os
from pathlib import Path

from core.prompt_v2.template_baseline import TemplateBaselineStore
from core.prompt_v2.template_migration import TemplateMigrationService

default_dir = Path(os.environ["PROBE_DEFAULT_DIR"])
runtime_dir = Path(os.environ["PROBE_RUNTIME_DIR"])
state_dir = Path(os.environ["PROBE_STATE_DIR"])
surface = os.environ["PROBE_SURFACE"]
store = TemplateBaselineStore(
    default_dir=default_dir,
    runtime_dir=runtime_dir,
    state_dir=state_dir,
)
try:
    if surface == "flow":
        os.environ["NANOBOT_PROMPT_DEFAULT_DIR"] = str(default_dir)
        os.environ["NANOBOT_PROMPT_RUNTIME_DIR"] = str(runtime_dir)
        from core.prompt_v2.flow import load_flow
        load_flow()
    elif surface == "migration_runtime":
        TemplateMigrationService(
            default_dir=default_dir,
            runtime_dir=runtime_dir,
            state_dir=state_dir,
        ).provision_missing("chat/main")
    elif surface == "migration_journal":
        TemplateMigrationService(
            default_dir=default_dir,
            runtime_dir=runtime_dir,
            state_dir=state_dir,
        ).recover()
    else:
        report = store.audit("chat/main")
        if report.drift_status == "invalid":
            print("rejected")
            raise SystemExit(0)
except Exception:
    print("rejected")
    raise SystemExit(0)
raise SystemExit(2)
"""
    env = os.environ.copy()
    env.update(
        {
            "PROBE_DEFAULT_DIR": str(default_dir),
            "PROBE_RUNTIME_DIR": str(runtime_dir),
            "PROBE_STATE_DIR": str(state_dir),
            "PROBE_SURFACE": surface,
        }
    )

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path.cwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "rejected"


def test_template_baseline_state_directory_must_be_outside_runtime(tmp_path):
    from core.prompt_v2.template_baseline import (
        TemplateBaselineError,
        TemplateBaselineStore,
    )

    runtime_dir = tmp_path / "runtime"

    with pytest.raises(TemplateBaselineError, match="runtime 模板根目录之外"):
        TemplateBaselineStore(
            default_dir=tmp_path / "defaults",
            runtime_dir=runtime_dir,
            state_dir=runtime_dir / ".state",
        )


def test_template_runtime_startup_preserves_existing_bytes_and_does_not_adopt(
    tmp_path,
    monkeypatch,
):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    state_dir = tmp_path / "template-state"
    shutil.copytree(Path("prompts.v2.default"), default_dir)
    shutil.copytree(Path("prompts.v2.default/tasks"), runtime_dir / "tasks")
    runtime_main = runtime_dir / "chat" / "main.md"
    runtime_main.parent.mkdir(parents=True)
    runtime_main.write_bytes(b"---\nversion: local\n---\nlocal override\n")
    before = runtime_main.read_bytes()
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_TEMPLATE_STATE_DIR", str(state_dir))

    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    result = init_prompt_v2_runtime_dir()

    assert runtime_main.read_bytes() == before
    report = next(
        item
        for item in result["template_audit"]
        if item["template_key"] == "chat/main"
    )
    assert report["drift_status"] == "untracked_legacy"
    manifest = json.loads((state_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "chat/main" not in manifest["templates"]


def test_template_runtime_startup_provisions_missing_file_with_baseline(
    tmp_path,
    monkeypatch,
):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    state_dir = tmp_path / "template-state"
    shutil.copytree(Path("prompts.v2.default"), default_dir)
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_TEMPLATE_STATE_DIR", str(state_dir))

    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    result = init_prompt_v2_runtime_dir()

    runtime_main = runtime_dir / "chat" / "main.md"
    assert runtime_main.read_bytes() == (default_dir / "chat" / "main.md").read_bytes()
    report = next(
        item
        for item in result["template_audit"]
        if item["template_key"] == "chat/main"
    )
    assert report["drift_status"] == "in_sync"
    assert report["baseline_sha256"] == hashlib.sha256(runtime_main.read_bytes()).hexdigest()
    assert result["baseline_provisioned"]


def _migration_service(tmp_path: Path):
    from core.prompt_v2.template_migration import TemplateMigrationService

    store, default_dir, runtime_dir, state_dir = _store(tmp_path)
    return (
        TemplateMigrationService(
            default_dir=default_dir,
            runtime_dir=runtime_dir,
            state_dir=state_dir,
        ),
        store,
        default_dir,
        runtime_dir,
        state_dir,
    )


def test_template_upgrade_plan_binds_inputs_and_apply_revalidates_runtime(tmp_path):
    from core.prompt_v2.template_migration import TemplateMigrationStalePlanError

    service, store, default_dir, runtime_dir, state_dir = _migration_service(tmp_path)
    base = b"---\nversion: 1\n---\nbase\n"
    canonical_v2 = b"---\nversion: 2\n---\ncanonical v2\n"
    _write_template(default_dir / "chat" / "main.md", base)
    _write_template(runtime_dir / "chat" / "main.md", base)
    store.adopt_in_sync("chat/main", baseline_version="1", modified_by="test")
    (default_dir / "chat" / "main.md").write_bytes(canonical_v2)
    manifest_before = (state_dir / "manifest.json").read_bytes()
    runtime_path = runtime_dir / "chat" / "main.md"

    plan = service.plan(template_keys=["chat/main"], modified_by="test")

    assert len(plan["plan_id"]) == 64
    assert plan["operation_type"] == "upgrade"
    assert plan["manifest_revision"] == 1
    assert len(plan["manifest_sha256"]) == 64
    assert plan["lineage_head"] is None
    assert plan["items"][0]["baseline_sha256"] == hashlib.sha256(base).hexdigest()
    assert plan["items"][0]["runtime_sha256"] == hashlib.sha256(base).hexdigest()
    assert plan["items"][0]["canonical_sha256"] == hashlib.sha256(canonical_v2).hexdigest()
    assert plan["items"][0]["target_sha256"] == hashlib.sha256(canonical_v2).hexdigest()
    assert runtime_path.read_bytes() == base
    assert (state_dir / "manifest.json").read_bytes() == manifest_before

    runtime_path.write_bytes(b"changed after plan\n")
    runtime_before_apply = runtime_path.read_bytes()
    with pytest.raises(TemplateMigrationStalePlanError, match="runtime"):
        service.apply(plan["plan_id"])

    assert runtime_path.read_bytes() == runtime_before_apply
    assert (state_dir / "manifest.json").read_bytes() == manifest_before
    assert not (state_dir / "journals" / "pending.json").exists()


def test_template_upgrade_apply_installs_canonical_and_advances_baseline(tmp_path):
    service, store, default_dir, runtime_dir, state_dir = _migration_service(tmp_path)
    base = b"---\nversion: 1\n---\nbase\n"
    canonical_v2 = b"---\nversion: 2\n---\ncanonical v2\n"
    _write_template(default_dir / "chat" / "main.md", base)
    _write_template(runtime_dir / "chat" / "main.md", base)
    store.adopt_in_sync("chat/main", baseline_version="1", modified_by="test")
    (default_dir / "chat" / "main.md").write_bytes(canonical_v2)

    plan = service.plan(template_keys=["chat/main"], modified_by="test")
    result = service.apply(plan["plan_id"])

    assert result["status"] == "applied"
    assert (runtime_dir / "chat" / "main.md").read_bytes() == canonical_v2
    assert store.read_baseline_bytes("chat/main") == canonical_v2
    assert store.audit("chat/main").drift_status == "in_sync"
    manifest = store.manifest_snapshot()
    assert manifest["revision"] == 2
    assert manifest["lineage"] == [result["operation_id"]]
    assert manifest["templates"]["chat/main"]["last_migration_id"] == result["operation_id"]
    operation = json.loads(
        (state_dir / "operations" / f'{result["operation_id"]}.json').read_text(
            encoding="utf-8"
        )
    )
    assert operation["status"] == "committed"
    assert not (state_dir / "journals" / "pending.json").exists()


def test_template_upgrade_plan_rejects_diverged_without_state_writes(tmp_path):
    from core.prompt_v2.template_migration import TemplateMigrationConflictError

    service, store, default_dir, runtime_dir, state_dir = _migration_service(tmp_path)
    _write_template(default_dir / "chat" / "main.md", b"base\n")
    _write_template(runtime_dir / "chat" / "main.md", b"base\n")
    store.adopt_in_sync("chat/main", baseline_version="1", modified_by="test")
    (default_dir / "chat" / "main.md").write_bytes(b"canonical v2\n")
    (runtime_dir / "chat" / "main.md").write_bytes(b"local\n")
    before = {
        path.relative_to(state_dir).as_posix(): path.read_bytes()
        for path in state_dir.rglob("*")
        if path.is_file()
    }

    with pytest.raises(TemplateMigrationConflictError, match="diverged"):
        service.plan(template_keys=["chat/main"], modified_by="test")

    after = {
        path.relative_to(state_dir).as_posix(): path.read_bytes()
        for path in state_dir.rglob("*")
        if path.is_file()
    }
    assert after == before


@pytest.mark.parametrize(
    ("strategy", "same_runtime", "merged_body", "expected_body", "expected_status"),
    [
        ("adopt-in-sync", True, None, b"canonical\n", "in_sync"),
        ("keep-runtime", False, None, b"local\n", "local_override"),
        ("use-default", False, None, b"canonical\n", "in_sync"),
        ("merged-file", False, b"merged\n", b"merged\n", "local_override"),
    ],
)
def test_template_explicit_resolution_strategies(
    tmp_path,
    strategy,
    same_runtime,
    merged_body,
    expected_body,
    expected_status,
):
    service, store, default_dir, runtime_dir, _state_dir = _migration_service(tmp_path)
    canonical = b"canonical\n"
    runtime = canonical if same_runtime else b"local\n"
    _write_template(default_dir / "chat" / "main.md", canonical)
    _write_template(runtime_dir / "chat" / "main.md", runtime)
    merged_file = None
    if merged_body is not None:
        merged_file = tmp_path / "merged.md"
        merged_file.write_bytes(merged_body)

    plan = service.resolve(
        template_key="chat/main",
        strategy=strategy,
        merged_file=merged_file,
        modified_by="test",
    )
    result = service.apply(plan["plan_id"])

    assert result["status"] == "applied"
    assert (runtime_dir / "chat" / "main.md").read_bytes() == expected_body
    assert store.audit("chat/main").drift_status == expected_status
    assert store.read_baseline_bytes("chat/main") == canonical


def test_template_resolution_plan_id_binds_strategy_and_merged_source(tmp_path):
    from core.prompt_v2.template_migration import TemplateMigrationStalePlanError

    service, _store_value, default_dir, runtime_dir, state_dir = _migration_service(tmp_path)
    _write_template(default_dir / "chat" / "main.md", b"canonical\n")
    _write_template(runtime_dir / "chat" / "main.md", b"local\n")
    merged_file = tmp_path / "merged.md"
    merged_file.write_bytes(b"local\n")

    keep = service.resolve(
        template_key="chat/main",
        strategy="keep-runtime",
        modified_by="test",
    )
    merged = service.resolve(
        template_key="chat/main",
        strategy="merged-file",
        merged_file=merged_file,
        modified_by="test",
    )

    assert keep["plan_id"] != merged["plan_id"]
    merged_file.write_bytes(b"changed merged source\n")
    manifest_before = (state_dir / "manifest.json").read_bytes() if (state_dir / "manifest.json").exists() else None
    runtime_before = (runtime_dir / "chat" / "main.md").read_bytes()
    with pytest.raises(TemplateMigrationStalePlanError, match="merged"):
        service.apply(merged["plan_id"])
    assert (runtime_dir / "chat" / "main.md").read_bytes() == runtime_before
    assert (
        (state_dir / "manifest.json").read_bytes()
        if (state_dir / "manifest.json").exists()
        else None
    ) == manifest_before


def test_template_apply_rejects_manifest_only_change(tmp_path):
    from core.prompt_v2.template_migration import TemplateMigrationStalePlanError

    service, store, default_dir, runtime_dir, state_dir = _migration_service(tmp_path)
    _write_template(default_dir / "chat" / "main.md", b"base\n")
    _write_template(runtime_dir / "chat" / "main.md", b"base\n")
    store.adopt_in_sync("chat/main", baseline_version="1", modified_by="test")
    (default_dir / "chat" / "main.md").write_bytes(b"canonical v2\n")
    plan = service.plan(template_keys=["chat/main"], modified_by="test")
    manifest_path = state_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["revision"] += 1
    manifest["lineage"].append("f" * 64)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    changed_manifest = manifest_path.read_bytes()
    runtime_before = (runtime_dir / "chat" / "main.md").read_bytes()

    with pytest.raises(TemplateMigrationStalePlanError, match="manifest"):
        service.apply(plan["plan_id"])

    assert manifest_path.read_bytes() == changed_manifest
    assert (runtime_dir / "chat" / "main.md").read_bytes() == runtime_before
    assert not (state_dir / "journals" / "pending.json").exists()


def _prepare_upgrade(
    tmp_path: Path,
    *,
    template_keys: tuple[str, ...] = ("chat/main",),
    failure_injector=None,
):
    from core.prompt_v2.template_migration import TemplateMigrationService

    store, default_dir, runtime_dir, state_dir = _store(tmp_path)
    for index, key in enumerate(template_keys, start=1):
        relative = Path(f"{key}.md")
        base = f"---\nversion: 1\n---\nbase-{index}\n".encode()
        canonical = f"---\nversion: 2\n---\ncanonical-{index}\n".encode()
        _write_template(default_dir / relative, base)
        _write_template(runtime_dir / relative, base)
        store.adopt_in_sync(key, baseline_version="1", modified_by="test")
        (default_dir / relative).write_bytes(canonical)
    service = TemplateMigrationService(
        default_dir=default_dir,
        runtime_dir=runtime_dir,
        state_dir=state_dir,
        failure_injector=failure_injector,
    )
    plan = service.plan(template_keys=list(template_keys), modified_by="test")
    return service, store, plan, default_dir, runtime_dir, state_dir


@pytest.mark.parametrize(
    "crash_point",
    [
        "after_journal_prepared",
        "after_file_installed",
        "after_files_installed",
        "after_manifest_committed",
        "after_state_committed",
    ],
)
def test_template_migration_recovers_each_durable_crash_boundary(
    tmp_path,
    crash_point,
):
    from core.prompt_v2.template_migration import TemplateMigrationService

    crashed = False

    def inject(point: str) -> None:
        nonlocal crashed
        if point == crash_point and not crashed:
            crashed = True
            raise RuntimeError(f"simulated crash at {point}")

    service, store, plan, default_dir, runtime_dir, state_dir = _prepare_upgrade(
        tmp_path,
        failure_injector=inject,
    )

    with pytest.raises(RuntimeError, match="simulated crash"):
        service.apply(plan["plan_id"])
    assert (state_dir / "journals" / "pending.json").exists()

    recovered = TemplateMigrationService(
        default_dir=default_dir,
        runtime_dir=runtime_dir,
        state_dir=state_dir,
    ).recover()

    assert recovered["status"] in {"recovered", "already_committed"}
    assert (runtime_dir / "chat" / "main.md").read_bytes() == (
        default_dir / "chat" / "main.md"
    ).read_bytes()
    assert store.audit("chat/main").drift_status == "in_sync"
    assert not (state_dir / "journals" / "pending.json").exists()
    assert (state_dir / "operations" / f'{recovered["operation_id"]}.json').exists()


def test_template_recovery_refuses_third_party_bytes_without_any_writes(tmp_path):
    from core.prompt_v2.template_migration import (
        TemplateMigrationRecoveryConflictError,
        TemplateMigrationService,
    )

    crashed = False

    def inject(point: str) -> None:
        nonlocal crashed
        if point == "after_file_installed" and not crashed:
            crashed = True
            raise RuntimeError("simulated crash")

    service, _store_value, plan, default_dir, runtime_dir, state_dir = _prepare_upgrade(
        tmp_path,
        template_keys=("chat/main", "chat/branch_private"),
        failure_injector=inject,
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        service.apply(plan["plan_id"])
    (runtime_dir / "chat" / "main.md").write_bytes(b"third-party edit\n")
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    with pytest.raises(TemplateMigrationRecoveryConflictError, match="人工"):
        TemplateMigrationService(
            default_dir=default_dir,
            runtime_dir=runtime_dir,
            state_dir=state_dir,
        ).recover()

    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_template_recovery_refuses_installed_file_restored_to_before(tmp_path):
    from core.prompt_v2.template_migration import (
        TemplateMigrationRecoveryConflictError,
        TemplateMigrationService,
    )

    crashed = False

    def inject(point: str) -> None:
        nonlocal crashed
        if point == "after_file_installed" and not crashed:
            crashed = True
            raise RuntimeError("simulated crash")

    service, _store_value, plan, default_dir, runtime_dir, state_dir = (
        _prepare_upgrade(
            tmp_path,
            template_keys=("chat/main", "chat/branch_private"),
            failure_injector=inject,
        )
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        service.apply(plan["plan_id"])

    journal = json.loads(
        (state_dir / "journals" / "pending.json").read_text(encoding="utf-8")
    )
    installed_item = next(item for item in journal["items"] if item["installed"])
    runtime_path = runtime_dir / installed_item["relative_path"]
    runtime_path.write_bytes(
        (state_dir / "blobs" / installed_item["before_blob_sha256"]).read_bytes()
    )
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    with pytest.raises(TemplateMigrationRecoveryConflictError, match="人工"):
        TemplateMigrationService(
            default_dir=default_dir,
            runtime_dir=runtime_dir,
            state_dir=state_dir,
        ).recover()

    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_template_apply_refuses_third_party_bytes_after_journal_prepared(tmp_path):
    from core.prompt_v2.template_migration import (
        TemplateMigrationRecoveryConflictError,
    )

    service, _store_value, plan, _default_dir, runtime_dir, state_dir = (
        _prepare_upgrade(tmp_path)
    )
    runtime_path = runtime_dir / "chat" / "main.md"

    def inject(point: str) -> None:
        if point == "after_journal_prepared":
            runtime_path.write_bytes(b"third-party edit\n")

    service._failure_injector = inject

    with pytest.raises(TemplateMigrationRecoveryConflictError, match="人工"):
        service.apply(plan["plan_id"])

    assert runtime_path.read_bytes() == b"third-party edit\n"
    assert (state_dir / "journals" / "pending.json").exists()


def test_template_recovery_refuses_committed_manifest_restored_to_before(tmp_path):
    from core.prompt_v2.template_migration import (
        TemplateMigrationRecoveryConflictError,
        TemplateMigrationService,
    )

    crashed = False

    def inject(point: str) -> None:
        nonlocal crashed
        if point == "after_state_committed" and not crashed:
            crashed = True
            raise RuntimeError("simulated crash")

    service, _store_value, plan, default_dir, runtime_dir, state_dir = (
        _prepare_upgrade(tmp_path, failure_injector=inject)
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        service.apply(plan["plan_id"])
    journal_path = state_dir / "journals" / "pending.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["state"] == "state_committed"
    (state_dir / "manifest.json").write_bytes(
        (
            state_dir
            / "blobs"
            / journal["manifest_before_blob_sha256"]
        ).read_bytes()
    )
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    with pytest.raises(TemplateMigrationRecoveryConflictError, match="人工"):
        TemplateMigrationService(
            default_dir=default_dir,
            runtime_dir=runtime_dir,
            state_dir=state_dir,
        ).recover()

    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_template_apply_same_plan_resumes_its_pending_journal(tmp_path):
    from core.prompt_v2.template_migration import TemplateMigrationService

    crashed = False

    def inject(point: str) -> None:
        nonlocal crashed
        if point == "after_file_installed" and not crashed:
            crashed = True
            raise RuntimeError("simulated crash")

    service, store, plan, default_dir, runtime_dir, state_dir = _prepare_upgrade(
        tmp_path,
        failure_injector=inject,
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        service.apply(plan["plan_id"])

    resumed = TemplateMigrationService(
        default_dir=default_dir,
        runtime_dir=runtime_dir,
        state_dir=state_dir,
    ).apply(plan["plan_id"])

    assert resumed["status"] == "recovered"
    assert store.audit("chat/main").drift_status == "in_sync"
    assert not (state_dir / "journals" / "pending.json").exists()


def test_template_apply_same_plan_is_idempotent_while_after_snapshot_is_live(
    tmp_path,
):
    service, _store_value, plan, _default_dir, _runtime_dir, state_dir = (
        _prepare_upgrade(tmp_path)
    )
    first = service.apply(plan["plan_id"])

    repeated = service.apply(plan["plan_id"])

    assert repeated == {
        "status": "already_applied",
        "operation_id": first["operation_id"],
        "plan_id": plan["plan_id"],
    }
    assert len(list((state_dir / "operations").glob("*.json"))) == 1


def test_template_rollback_restores_runtime_manifest_and_lineage(tmp_path):
    service, store, plan, _default_dir, runtime_dir, state_dir = _prepare_upgrade(tmp_path)
    manifest_before = (state_dir / "manifest.json").read_bytes()
    runtime_before = (runtime_dir / "chat" / "main.md").read_bytes()
    applied = service.apply(plan["plan_id"])

    rolled_back = service.rollback(
        applied["operation_id"],
        modified_by="test",
        reason="验证回滚",
    )

    assert rolled_back["status"] == "rolled_back"
    assert (runtime_dir / "chat" / "main.md").read_bytes() == runtime_before
    assert (state_dir / "manifest.json").read_bytes() == manifest_before
    assert store.manifest_snapshot()["lineage"] == []
    assert store.audit("chat/main").drift_status == "upgrade_available"
    assert (state_dir / "operations" / f'{applied["operation_id"]}.json').exists()
    assert (state_dir / "operations" / f'{rolled_back["operation_id"]}.json').exists()


def test_template_apply_same_plan_after_rollback_creates_new_operation(tmp_path):
    service, store, plan, default_dir, runtime_dir, state_dir = _prepare_upgrade(
        tmp_path
    )
    first_apply = service.apply(plan["plan_id"])
    rolled_back = service.rollback(
        first_apply["operation_id"],
        modified_by="test",
        reason="验证重复应用",
    )

    second_apply = service.apply(plan["plan_id"])

    assert second_apply["status"] == "applied"
    assert second_apply["operation_id"] != first_apply["operation_id"]
    assert (runtime_dir / "chat" / "main.md").read_bytes() == (
        default_dir / "chat" / "main.md"
    ).read_bytes()
    assert store.audit("chat/main").drift_status == "in_sync"
    for operation_id in (
        first_apply["operation_id"],
        rolled_back["operation_id"],
        second_apply["operation_id"],
    ):
        assert (state_dir / "operations" / f"{operation_id}.json").exists()


def test_template_rollback_rejects_later_runtime_edit_without_writes(tmp_path):
    from core.prompt_v2.template_migration import TemplateMigrationConflictError

    service, _store_value, plan, _default_dir, runtime_dir, state_dir = _prepare_upgrade(tmp_path)
    applied = service.apply(plan["plan_id"])
    runtime_path = runtime_dir / "chat" / "main.md"
    runtime_path.write_bytes(b"later edit\n")
    manifest_before = (state_dir / "manifest.json").read_bytes()

    with pytest.raises(TemplateMigrationConflictError, match="后续修改"):
        service.rollback(
            applied["operation_id"],
            modified_by="test",
            reason="不应覆盖",
        )

    assert runtime_path.read_bytes() == b"later edit\n"
    assert (state_dir / "manifest.json").read_bytes() == manifest_before
    assert not (state_dir / "journals" / "pending.json").exists()


def test_template_apply_rejects_corrupted_target_blob_before_journal(tmp_path):
    from core.prompt_v2.template_baseline import TemplateBlobIntegrityError

    service, _store_value, plan, _default_dir, runtime_dir, state_dir = _prepare_upgrade(tmp_path)
    target_sha256 = plan["items"][0]["target_sha256"]
    (state_dir / "blobs" / target_sha256).write_bytes(b"corrupted target\n")
    runtime_before = (runtime_dir / "chat" / "main.md").read_bytes()
    manifest_before = (state_dir / "manifest.json").read_bytes()

    with pytest.raises(TemplateBlobIntegrityError):
        service.apply(plan["plan_id"])

    assert (runtime_dir / "chat" / "main.md").read_bytes() == runtime_before
    assert (state_dir / "manifest.json").read_bytes() == manifest_before
    assert not (state_dir / "journals" / "pending.json").exists()


def test_template_apply_holds_global_write_lock_across_all_files(tmp_path):
    from core.prompt_v2.flow_storage import template_governance_read_lock

    first_installed = threading.Event()
    release_writer = threading.Event()
    errors: list[BaseException] = []

    def inject(point: str) -> None:
        if point == "after_file_installed" and not first_installed.is_set():
            first_installed.set()
            if not release_writer.wait(timeout=5):
                raise TimeoutError("等待释放迁移写锁超时")

    service, _store_value, plan, _default_dir, runtime_dir, _state_dir = _prepare_upgrade(
        tmp_path,
        template_keys=("chat/main", "chat/branch_private"),
        failure_injector=inject,
    )

    def apply_plan() -> None:
        try:
            service.apply(plan["plan_id"])
        except BaseException as exc:  # pragma: no cover - 跨线程传递
            errors.append(exc)

    observed: list[tuple[bytes, bytes]] = []

    def read_snapshot() -> None:
        try:
            with template_governance_read_lock(runtime_dir):
                observed.append(
                    (
                        (runtime_dir / "chat" / "main.md").read_bytes(),
                        (runtime_dir / "chat" / "branch_private.md").read_bytes(),
                    )
                )
        except BaseException as exc:  # pragma: no cover - 跨线程传递
            errors.append(exc)

    writer = threading.Thread(target=apply_plan)
    reader = threading.Thread(target=read_snapshot)
    writer.start()
    assert first_installed.wait(timeout=5)
    reader.start()
    time.sleep(0.05)
    assert reader.is_alive(), "读侧应等待整个跨文件事务提交"
    release_writer.set()
    writer.join(timeout=5)
    reader.join(timeout=5)

    assert errors == []
    assert observed == [
        (
            (runtime_dir / "chat" / "main.md").read_bytes(),
            (runtime_dir / "chat" / "branch_private.md").read_bytes(),
        )
    ]


def test_template_governance_read_lock_works_on_read_only_runtime_mount(tmp_path):
    from core.prompt_v2.flow_storage import template_governance_read_lock

    live_root = tmp_path / "live"
    runtime_dir = live_root / "runtime"
    runtime_dir.mkdir(parents=True)
    lock_path = live_root / ".prompt-template-governance.lock"
    lock_path.write_bytes(b"")
    lock_path.chmod(0o400)
    live_root.chmod(0o500)
    try:
        with template_governance_read_lock(runtime_dir):
            assert lock_path.is_file()
    finally:
        live_root.chmod(0o700)
        lock_path.chmod(0o600)


def test_template_provision_recovers_without_auto_adopting_legacy_file(tmp_path):
    from core.prompt_v2.template_migration import TemplateMigrationService

    store, default_dir, runtime_dir, state_dir = _store(tmp_path)
    canonical = b"---\nversion: 1\n---\ncanonical\n"
    _write_template(default_dir / "chat" / "main.md", canonical)
    crashed = False

    def inject(point: str) -> None:
        nonlocal crashed
        if point == "after_file_installed" and not crashed:
            crashed = True
            raise RuntimeError("simulated provision crash")

    service = TemplateMigrationService(
        default_dir=default_dir,
        runtime_dir=runtime_dir,
        state_dir=state_dir,
        failure_injector=inject,
    )
    with pytest.raises(RuntimeError, match="provision crash"):
        service.provision_missing("chat/main")
    assert (state_dir / "journals" / "pending.json").exists()

    recovered = TemplateMigrationService(
        default_dir=default_dir,
        runtime_dir=runtime_dir,
        state_dir=state_dir,
    ).recover()

    assert recovered["status"] == "recovered"
    assert (runtime_dir / "chat" / "main.md").read_bytes() == canonical
    assert store.audit("chat/main").drift_status == "in_sync"

    legacy_store, legacy_default, legacy_runtime, legacy_state = _store(
        tmp_path / "legacy"
    )
    _write_template(legacy_default / "chat" / "main.md", canonical)
    _write_template(legacy_runtime / "chat" / "main.md", canonical)
    legacy_service = TemplateMigrationService(
        default_dir=legacy_default,
        runtime_dir=legacy_runtime,
        state_dir=legacy_state,
    )
    assert legacy_service.provision_missing("chat/main") is False
    assert legacy_store.audit("chat/main").drift_status == "untracked_legacy"
    assert not (legacy_state / "manifest.json").exists()


def test_template_provision_after_rollback_uses_new_operation_identity(tmp_path):
    from core.prompt_v2.template_migration import TemplateMigrationService

    store, default_dir, runtime_dir, state_dir = _store(tmp_path)
    canonical = b"---\nversion: 1\nkind: chat\n---\ncanonical\n"
    _write_template(default_dir / "chat" / "main.md", canonical)
    service = TemplateMigrationService(
        default_dir=default_dir,
        runtime_dir=runtime_dir,
        state_dir=state_dir,
    )

    assert service.provision_missing("chat/main") is True
    first_operation_id = store.manifest_snapshot()["lineage"][-1]
    rolled_back = service.rollback(
        first_operation_id,
        modified_by="test",
        reason="验证初始化重试身份",
    )

    assert not (runtime_dir / "chat" / "main.md").exists()
    assert service.provision_missing("chat/main") is True

    manifest = store.manifest_snapshot()
    second_operation_id = manifest["lineage"][-1]
    assert second_operation_id not in {
        first_operation_id,
        rolled_back["operation_id"],
    }
    assert manifest["templates"]["chat/main"]["last_migration_id"] == (
        second_operation_id
    )
    assert len(list((state_dir / "operations").glob("*.json"))) == 3


def test_template_resolution_reads_baseline_drift_and_pending_journal_fails_closed(
    tmp_path,
    monkeypatch,
):
    from core.prompt_v2.template_baseline import TemplateBaselineError
    from core.prompt_v2.template_loader import load_template

    service, store, default_dir, runtime_dir, state_dir = _migration_service(tmp_path)
    base = b"---\nversion: 1\n---\nbase\n"
    _write_template(default_dir / "chat" / "main.md", base)
    _write_template(runtime_dir / "chat" / "main.md", base)
    store.adopt_in_sync("chat/main", baseline_version="1", modified_by="test")
    (default_dir / "chat" / "main.md").write_bytes(
        b"---\nversion: 2\n---\ncanonical v2\n"
    )
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_TEMPLATE_STATE_DIR", str(state_dir))

    loaded = load_template("chat/main")

    assert loaded.resolution is not None
    assert loaded.resolution.drift_status == "upgrade_available"
    assert loaded.resolution.baseline_version == "1"

    plan = service.plan(template_keys=["chat/main"], modified_by="test")

    def crash(point: str) -> None:
        if point == "after_journal_prepared":
            raise RuntimeError("simulated crash")

    crashing_service = type(service)(
        default_dir=default_dir,
        runtime_dir=runtime_dir,
        state_dir=state_dir,
        failure_injector=crash,
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        crashing_service.apply(plan["plan_id"])

    with pytest.raises(TemplateBaselineError, match="pending journal"):
        load_template("chat/main")


def test_template_runtime_startup_recovers_pending_journal_before_audit(
    tmp_path,
    monkeypatch,
):
    from core.prompt_v2.template_migration import TemplateMigrationService

    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    state_dir = tmp_path / "template-state"
    shutil.copytree(Path("prompts.v2.default"), default_dir)
    shutil.copytree(Path("prompts.v2.default"), runtime_dir)
    store = _store(tmp_path / "unused")[0]
    store = type(store)(
        default_dir=default_dir,
        runtime_dir=runtime_dir,
        state_dir=state_dir,
    )
    store.adopt_in_sync("chat/main", baseline_version="1", modified_by="test")
    (default_dir / "chat" / "main.md").write_bytes(
        b"---\nversion: 2\nkind: chat\n---\ncanonical v2\n"
    )
    service = TemplateMigrationService(
        default_dir=default_dir,
        runtime_dir=runtime_dir,
        state_dir=state_dir,
        failure_injector=lambda point: (
            (_ for _ in ()).throw(RuntimeError("simulated crash"))
            if point == "after_file_installed"
            else None
        ),
    )
    plan = service.plan(template_keys=["chat/main"], modified_by="test")
    with pytest.raises(RuntimeError, match="simulated crash"):
        service.apply(plan["plan_id"])
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_TEMPLATE_STATE_DIR", str(state_dir))

    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    result = init_prompt_v2_runtime_dir()

    assert result["template_recovery"]["status"] == "recovered"
    assert (runtime_dir / "chat" / "main.md").read_bytes() == (
        default_dir / "chat" / "main.md"
    ).read_bytes()
    assert not (state_dir / "journals" / "pending.json").exists()


def test_template_runtime_startup_fails_closed_on_corrupted_baseline_blob(
    tmp_path,
    monkeypatch,
):
    from core.prompt_v2.template_baseline import (
        TemplateBaselineError,
        TemplateBaselineStore,
    )

    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    state_dir = tmp_path / "template-state"
    shutil.copytree(Path("prompts.v2.default"), default_dir)
    shutil.copytree(Path("prompts.v2.default"), runtime_dir)
    store = TemplateBaselineStore(
        default_dir=default_dir,
        runtime_dir=runtime_dir,
        state_dir=state_dir,
    )
    store.adopt_in_sync("chat/main", baseline_version="1", modified_by="test")
    manifest = store.manifest_snapshot()
    blob_sha256 = manifest["templates"]["chat/main"]["baseline_blob_sha256"]
    (state_dir / "blobs" / blob_sha256).write_bytes(b"corrupted\n")
    before = {
        path.relative_to(runtime_dir).as_posix(): path.read_bytes()
        for path in runtime_dir.rglob("*")
        if path.is_file()
    }
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_TEMPLATE_STATE_DIR", str(state_dir))

    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    with pytest.raises(TemplateBaselineError, match="invalid"):
        init_prompt_v2_runtime_dir()

    after = {
        path.relative_to(runtime_dir).as_posix(): path.read_bytes()
        for path in runtime_dir.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_compile_holds_one_read_snapshot_while_template_apply_waits(
    tmp_path,
    monkeypatch,
):
    from core.prompt_v2 import compiler
    from core.prompt_v2.schema import PromptCompileRequest
    from core.prompt_v2.template_baseline import TemplateBaselineStore
    from core.prompt_v2.template_migration import TemplateMigrationService

    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    state_dir = tmp_path / "template-state"
    shutil.copytree(Path("prompts.v2.default"), default_dir)
    shutil.copytree(Path("prompts.v2.default"), runtime_dir)
    old_base = b"---\nversion: 1\nkind: chat\n---\nBASE_OLD\n"
    old_private = b"---\nversion: 1\nkind: chat\n---\nPRIVATE_OLD\n"
    new_base = b"---\nversion: 2\nkind: chat\n---\nBASE_NEW\n"
    new_private = b"---\nversion: 2\nkind: chat\n---\nPRIVATE_NEW\n"
    (default_dir / "chat" / "main.md").write_bytes(old_base)
    (runtime_dir / "chat" / "main.md").write_bytes(old_base)
    (default_dir / "chat" / "branch_private.md").write_bytes(old_private)
    (runtime_dir / "chat" / "branch_private.md").write_bytes(old_private)
    store = TemplateBaselineStore(
        default_dir=default_dir,
        runtime_dir=runtime_dir,
        state_dir=state_dir,
    )
    store.adopt_in_sync("chat/main", baseline_version="1", modified_by="test")
    store.adopt_in_sync(
        "chat/branch_private",
        baseline_version="1",
        modified_by="test",
    )
    (default_dir / "chat" / "main.md").write_bytes(new_base)
    (default_dir / "chat" / "branch_private.md").write_bytes(new_private)
    service = TemplateMigrationService(
        default_dir=default_dir,
        runtime_dir=runtime_dir,
        state_dir=state_dir,
    )
    migration_plan = service.plan(
        template_keys=["chat/main", "chat/branch_private"],
        modified_by="test",
    )
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_TEMPLATE_STATE_DIR", str(state_dir))
    first_template_loaded = threading.Event()
    release_compile = threading.Event()
    real_load_template = compiler.load_template
    load_count = 0

    def paused_load_template(template_key: str):
        nonlocal load_count
        loaded = real_load_template(template_key)
        load_count += 1
        if load_count == 1:
            first_template_loaded.set()
            if not release_compile.wait(timeout=5):
                raise TimeoutError("等待释放 compile 超时")
        return loaded

    monkeypatch.setattr(compiler, "load_template", paused_load_template)
    compiled: list[object] = []
    errors: list[BaseException] = []

    def run_compile() -> None:
        try:
            with asyncio.Runner() as runner:
                compiled.append(
                    runner.run(
                        compiler.compile_prompt_plan(
                            PromptCompileRequest(
                                chat_type="private",
                                platform="web",
                                session_id="private-template-snapshot",
                                user_id="template-snapshot-user",
                                user_input="检查快照",
                                runtime_tool_prompt="[RuntimeTool]",
                            )
                        )
                    )
                )
        except BaseException as exc:  # pragma: no cover - 跨线程传递
            errors.append(exc)

    def run_apply() -> None:
        try:
            service.apply(migration_plan["plan_id"])
        except BaseException as exc:  # pragma: no cover - 跨线程传递
            errors.append(exc)

    compile_thread = threading.Thread(target=run_compile)
    apply_thread = threading.Thread(target=run_apply)
    compile_thread.start()
    assert first_template_loaded.wait(timeout=5)
    apply_thread.start()
    time.sleep(0.05)
    assert apply_thread.is_alive(), "apply 应等待整次 compile 释放共享快照锁"
    release_compile.set()
    compile_thread.join(timeout=5)
    apply_thread.join(timeout=5)

    assert errors == []
    assert len(compiled) == 1
    message_text = "\n".join(
        str(message.get("content") or "")
        for message in compiled[0].messages
    )
    assert "BASE_OLD" in message_text
    assert "PRIVATE_OLD" in message_text
    assert "BASE_NEW" not in message_text
    assert "PRIVATE_NEW" not in message_text


def test_admin_template_save_serializes_after_cli_apply(tmp_path, monkeypatch):
    from core.prompt_v2.template_store import save_template

    first_installed = threading.Event()
    release_apply = threading.Event()
    errors: list[BaseException] = []

    def inject(point: str) -> None:
        if point == "after_file_installed" and not first_installed.is_set():
            first_installed.set()
            if not release_apply.wait(timeout=5):
                raise TimeoutError("等待释放 apply 超时")

    service, store, plan, default_dir, runtime_dir, state_dir = _prepare_upgrade(
        tmp_path,
        failure_injector=inject,
    )
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_TEMPLATE_STATE_DIR", str(state_dir))

    def run_apply() -> None:
        try:
            service.apply(plan["plan_id"])
        except BaseException as exc:  # pragma: no cover - 跨线程传递
            errors.append(exc)

    def run_admin_save() -> None:
        try:
            save_template("chat/main", "ADMIN_LOCAL")
        except BaseException as exc:  # pragma: no cover - 跨线程传递
            errors.append(exc)

    apply_thread = threading.Thread(target=run_apply)
    admin_thread = threading.Thread(target=run_admin_save)
    apply_thread.start()
    assert first_installed.wait(timeout=5)
    admin_thread.start()
    time.sleep(0.05)
    assert admin_thread.is_alive(), "Admin save 应等待 CLI apply 完整提交"
    release_apply.set()
    apply_thread.join(timeout=5)
    admin_thread.join(timeout=5)

    assert errors == []
    assert (runtime_dir / "chat" / "main.md").read_bytes() == b"ADMIN_LOCAL\n"
    assert store.audit("chat/main").drift_status == "local_override"


def test_admin_template_save_integrity_failure_keeps_runtime_unchanged(
    tmp_path,
    monkeypatch,
):
    from core.prompt_v2.template_baseline import TemplateBlobIntegrityError
    from core.prompt_v2.template_store import save_template

    _service, store, _plan, default_dir, runtime_dir, state_dir = _prepare_upgrade(
        tmp_path
    )
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_TEMPLATE_STATE_DIR", str(state_dir))
    manifest = store.manifest_snapshot()
    baseline_sha256 = manifest["templates"]["chat/main"][
        "baseline_blob_sha256"
    ]
    (state_dir / "blobs" / baseline_sha256).write_bytes(b"corrupted\n")
    runtime_path = runtime_dir / "chat" / "main.md"
    runtime_before = runtime_path.read_bytes()
    manifest_before = (state_dir / "manifest.json").read_bytes()

    with pytest.raises(TemplateBlobIntegrityError):
        save_template("chat/main", "ADMIN_LOCAL")

    assert runtime_path.read_bytes() == runtime_before
    assert (state_dir / "manifest.json").read_bytes() == manifest_before
    assert not (state_dir / "journals" / "pending.json").exists()


def test_admin_runtime_change_recovers_from_file_install_crash(tmp_path):
    from core.prompt_v2.template_migration import TemplateMigrationService

    crashed = False

    def inject(point: str) -> None:
        nonlocal crashed
        if point == "after_file_installed" and not crashed:
            crashed = True
            raise RuntimeError("simulated admin crash")

    _service, store, _plan, default_dir, runtime_dir, state_dir = _prepare_upgrade(
        tmp_path
    )
    service = TemplateMigrationService(
        default_dir=default_dir,
        runtime_dir=runtime_dir,
        state_dir=state_dir,
        failure_injector=inject,
    )

    with pytest.raises(RuntimeError, match="admin crash"):
        service.apply_runtime_change(
            "chat/main",
            target_bytes=b"ADMIN_LOCAL\n",
            modified_by="test",
            operation_type="admin-save",
        )

    assert (state_dir / "journals" / "pending.json").exists()
    recovered = TemplateMigrationService(
        default_dir=default_dir,
        runtime_dir=runtime_dir,
        state_dir=state_dir,
    ).recover()
    assert recovered["status"] == "recovered"
    assert (runtime_dir / "chat" / "main.md").read_bytes() == b"ADMIN_LOCAL\n"
    assert store.audit("chat/main").drift_status == "diverged"


def test_admin_flow_save_uses_configured_template_state_dir(tmp_path, monkeypatch):
    from core.prompt_v2.flow import save_flow

    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    state_dir = tmp_path / "configured-state"
    flow_bytes = Path("prompts.v2.default/chat/flow.json").read_bytes()
    _write_template(default_dir / "chat" / "flow.json", flow_bytes)
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_TEMPLATE_STATE_DIR", str(state_dir))

    result = save_flow(json.loads(flow_bytes.decode("utf-8")))

    assert result["saved"] is True
    assert json.loads(
        (runtime_dir / "chat" / "flow.json").read_text(encoding="utf-8")
    ) == result["flow"]
    assert len(list((state_dir / "operations").glob("*.json"))) == 1
    assert not (runtime_dir.parent / "prompt_template_state").exists()


@pytest.mark.parametrize(
    ("template_key", "valid_bytes", "invalid_bytes"),
    [
        (
            "chat/main",
            b"---\nversion: 1\nkind: chat\n---\nbase\n",
            b"\xff\xfeinvalid",
        ),
        (
            "tasks/memory_extract",
            (
                b"---\nversion: 1\nkind: task\n"
                b"tool_name: memory_extract\n---\n"
                b"{{ conversation }}\n{{ existing_memory }}\n"
            ),
            (
                b"---\nversion: 1\nkind: task\n"
                b"tool_name: memory_extract\n---\nmissing variables\n"
            ),
        ),
        (
            "chat/flow",
            Path("prompts.v2.default/chat/flow.json").read_bytes(),
            b'{"version": 2, "nodes": [], "edges": []}\n',
        ),
    ],
)
def test_template_resolve_rejects_invalid_merged_target_before_plan(
    tmp_path,
    template_key,
    valid_bytes,
    invalid_bytes,
):
    from core.prompt_v2.template_migration import (
        TemplateMigrationIntegrityError,
        TemplateMigrationService,
    )

    store, default_dir, runtime_dir, state_dir = _store(tmp_path)
    _key, default_path, runtime_path = store.template_paths(template_key)
    _write_template(default_path, valid_bytes)
    _write_template(runtime_path, valid_bytes)
    store.adopt_in_sync(
        template_key,
        baseline_version="1",
        modified_by="test",
    )
    merged_path = tmp_path / "merged.bin"
    merged_path.write_bytes(invalid_bytes)
    runtime_before = runtime_path.read_bytes()
    manifest_before = (state_dir / "manifest.json").read_bytes()
    invalid_sha256 = hashlib.sha256(invalid_bytes).hexdigest()

    with pytest.raises(TemplateMigrationIntegrityError, match="模板内容无效"):
        TemplateMigrationService(
            default_dir=default_dir,
            runtime_dir=runtime_dir,
            state_dir=state_dir,
        ).resolve(
            template_key=template_key,
            strategy="merged-file",
            modified_by="test",
            merged_file=merged_path,
        )

    assert runtime_path.read_bytes() == runtime_before
    assert (state_dir / "manifest.json").read_bytes() == manifest_before
    assert not (state_dir / "blobs" / invalid_sha256).exists()
    assert not (state_dir / "journals" / "pending.json").exists()


def test_template_audit_marks_invalid_runtime_content(tmp_path):
    store, _default_path, runtime_path, _state_dir = _adopted_store(tmp_path)
    runtime_path.write_bytes(b"\xff\xfeinvalid")

    report = store.audit("chat/main")

    assert report.drift_status == "invalid"
    assert report.invalid_component == "runtime_content"
    assert "模板内容无效" in str(report.invalid_reason)


@pytest.mark.parametrize("tracked", [False, True], ids=["untracked", "tracked"])
@pytest.mark.parametrize(
    ("strategy", "expected_body", "expected_status"),
    [
        ("use-default", b"---\nversion: 1\nkind: chat\n---\ncanonical\n", "in_sync"),
        ("merged-file", b"---\nversion: 2\nkind: chat\n---\nmerged\n", "local_override"),
    ],
)
def test_template_resolution_repairs_runtime_content_only_invalid(
    tmp_path,
    tracked,
    strategy,
    expected_body,
    expected_status,
):
    from core.prompt_v2.template_migration import TemplateMigrationService

    store, default_dir, runtime_dir, state_dir = _store(tmp_path)
    canonical = b"---\nversion: 1\nkind: chat\n---\ncanonical\n"
    default_path = default_dir / "chat" / "main.md"
    runtime_path = runtime_dir / "chat" / "main.md"
    _write_template(default_path, canonical)
    _write_template(runtime_path, canonical)
    if tracked:
        store.adopt_in_sync(
            "chat/main",
            baseline_version="1",
            modified_by="test",
        )
    runtime_path.write_bytes(b"\xff\xfeinvalid runtime")
    report = store.audit("chat/main")
    assert report.drift_status == "invalid"
    assert report.invalid_component == "runtime_content"

    merged_file = None
    if strategy == "merged-file":
        merged_file = tmp_path / "merged.md"
        merged_file.write_bytes(expected_body)
    service = TemplateMigrationService(
        default_dir=default_dir,
        runtime_dir=runtime_dir,
        state_dir=state_dir,
    )

    plan = service.resolve(
        template_key="chat/main",
        strategy=strategy,
        modified_by="test",
        merged_file=merged_file,
    )
    result = service.apply(plan["plan_id"])

    assert result["status"] == "applied"
    assert runtime_path.read_bytes() == expected_body
    assert store.audit("chat/main").drift_status == expected_status
    assert store.read_baseline_bytes("chat/main") == canonical


def test_template_resolution_rejects_keeping_invalid_runtime(tmp_path):
    from core.prompt_v2.template_migration import (
        TemplateMigrationIntegrityError,
        TemplateMigrationService,
    )

    store, default_dir, runtime_dir, state_dir = _store(tmp_path)
    canonical = b"---\nversion: 1\nkind: chat\n---\ncanonical\n"
    _write_template(default_dir / "chat" / "main.md", canonical)
    _write_template(runtime_dir / "chat" / "main.md", b"\xff\xfeinvalid runtime")
    assert store.audit("chat/main").invalid_component == "runtime_content"

    with pytest.raises(TemplateMigrationIntegrityError, match="runtime"):
        TemplateMigrationService(
            default_dir=default_dir,
            runtime_dir=runtime_dir,
            state_dir=state_dir,
        ).resolve(
            template_key="chat/main",
            strategy="keep-runtime",
            modified_by="test",
        )

    assert not (state_dir / "plans").exists()


def test_template_resolution_cannot_repair_invalid_canonical(tmp_path):
    from core.prompt_v2.template_migration import (
        TemplateMigrationIntegrityError,
        TemplateMigrationService,
    )

    store, default_dir, runtime_dir, state_dir = _store(tmp_path)
    canonical = b"---\nversion: 1\nkind: chat\n---\ncanonical\n"
    default_path = default_dir / "chat" / "main.md"
    _write_template(default_path, canonical)
    _write_template(runtime_dir / "chat" / "main.md", canonical)
    store.adopt_in_sync("chat/main", baseline_version="1", modified_by="test")
    default_path.write_bytes(b"\xff\xfeinvalid canonical")

    report = store.audit("chat/main")
    assert report.drift_status == "invalid"
    assert report.invalid_component == "canonical_content"
    with pytest.raises(TemplateMigrationIntegrityError, match="模板内容无效"):
        TemplateMigrationService(
            default_dir=default_dir,
            runtime_dir=runtime_dir,
            state_dir=state_dir,
        ).resolve(
            template_key="chat/main",
            strategy="use-default",
            modified_by="test",
        )

    assert not (state_dir / "plans").exists()


def test_template_keep_runtime_rejects_untracked_contract_violation(tmp_path):
    from core.prompt_v2.template_migration import (
        TemplateMigrationIntegrityError,
        TemplateMigrationService,
    )

    store, default_dir, runtime_dir, state_dir = _store(tmp_path)
    template_key = "tasks/memory_extract"
    _key, default_path, runtime_path = store.template_paths(template_key)
    _write_template(
        default_path,
        (
            b"---\nversion: 1\nkind: task\n"
            b"tool_name: memory_extract\n---\n"
            b"{{ conversation }}\n{{ existing_memory }}\n"
        ),
    )
    _write_template(runtime_path, b"legacy without required variables\n")
    assert store.audit(template_key).drift_status == "untracked_legacy"

    with pytest.raises(TemplateMigrationIntegrityError, match="模板内容无效"):
        TemplateMigrationService(
            default_dir=default_dir,
            runtime_dir=runtime_dir,
            state_dir=state_dir,
        ).resolve(
            template_key=template_key,
            strategy="keep-runtime",
            modified_by="test",
        )

    assert not (state_dir / "plans").exists()


def _run_template_cli(
    args: list[str],
    *,
    default_dir: Path,
    runtime_dir: Path,
    state_dir: Path,
):
    env = {
        **os.environ,
        "NANOBOT_PROMPT_DEFAULT_DIR": str(default_dir),
        "NANOBOT_PROMPT_RUNTIME_DIR": str(runtime_dir),
        "NANOBOT_PROMPT_TEMPLATE_STATE_DIR": str(state_dir),
    }
    for key in (
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
    ):
        env.pop(key, None)
    return subprocess.run(
        [sys.executable, "scripts/manage_prompt_templates.py", *args],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_prompt_template_cli_full_lifecycle_outputs_json_without_body(tmp_path):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    state_dir = tmp_path / "template-state"
    sentinel = "CLI_BODY_MUST_NOT_LEAK"
    base = f"---\nversion: 1\n---\n{sentinel}\n".encode()
    _write_template(default_dir / "chat" / "main.md", base)
    _write_template(runtime_dir / "chat" / "main.md", base)

    audit = _run_template_cli(
        ["audit", "--template-key", "chat/main"],
        default_dir=default_dir,
        runtime_dir=runtime_dir,
        state_dir=state_dir,
    )
    assert audit.returncode == 0
    audit_payload = json.loads(audit.stdout)
    assert audit_payload["ok"] is True
    assert audit_payload["command"] == "audit"
    assert audit_payload["result"][0]["drift_status"] == "untracked_legacy"

    resolved = _run_template_cli(
        [
            "resolve",
            "--template-key",
            "chat/main",
            "--strategy",
            "adopt-in-sync",
            "--modified-by",
            "test-cli",
        ],
        default_dir=default_dir,
        runtime_dir=runtime_dir,
        state_dir=state_dir,
    )
    assert resolved.returncode == 0
    resolve_payload = json.loads(resolved.stdout)
    adopt_plan_id = resolve_payload["result"]["plan_id"]

    adopted = _run_template_cli(
        ["apply", "--plan-id", adopt_plan_id],
        default_dir=default_dir,
        runtime_dir=runtime_dir,
        state_dir=state_dir,
    )
    assert adopted.returncode == 0
    assert json.loads(adopted.stdout)["result"]["status"] == "applied"

    canonical_v2 = f"---\nversion: 2\n---\n{sentinel}-v2\n".encode()
    (default_dir / "chat" / "main.md").write_bytes(canonical_v2)
    planned = _run_template_cli(
        ["plan", "--template-key", "chat/main", "--modified-by", "test-cli"],
        default_dir=default_dir,
        runtime_dir=runtime_dir,
        state_dir=state_dir,
    )
    assert planned.returncode == 0
    upgrade_plan_id = json.loads(planned.stdout)["result"]["plan_id"]
    upgraded = _run_template_cli(
        ["apply", "--plan-id", upgrade_plan_id],
        default_dir=default_dir,
        runtime_dir=runtime_dir,
        state_dir=state_dir,
    )
    assert upgraded.returncode == 0
    upgraded_result = json.loads(upgraded.stdout)["result"]
    assert (runtime_dir / "chat" / "main.md").read_bytes() == canonical_v2

    rolled_back = _run_template_cli(
        [
            "rollback",
            "--operation-id",
            upgraded_result["operation_id"],
            "--reason",
            "CLI 验证回滚",
            "--modified-by",
            "test-cli",
        ],
        default_dir=default_dir,
        runtime_dir=runtime_dir,
        state_dir=state_dir,
    )
    assert rolled_back.returncode == 0
    assert json.loads(rolled_back.stdout)["result"]["status"] == "rolled_back"
    assert (runtime_dir / "chat" / "main.md").read_bytes() == base

    combined_output = "".join(
        result.stdout + result.stderr
        for result in (audit, resolved, adopted, planned, upgraded, rolled_back)
    )
    assert sentinel not in combined_output


def test_prompt_template_cli_argument_errors_are_json(tmp_path):
    result = _run_template_cli(
        ["apply"],
        default_dir=tmp_path / "defaults",
        runtime_dir=tmp_path / "runtime",
        state_dir=tmp_path / "template-state",
    )

    assert result.returncode == 2
    assert result.stdout == ""
    payload = json.loads(result.stderr)
    assert payload["ok"] is False
    assert payload["command"] == "apply"
    assert payload["error"]["code"] == "invalid_arguments"
