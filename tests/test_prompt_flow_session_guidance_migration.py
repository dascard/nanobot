from __future__ import annotations

import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import pytest


def _canonical_flow() -> dict:
    return json.loads(
        Path("prompts.v2.default/chat/flow.json").read_text(encoding="utf-8")
    )


def _old_flow() -> dict:
    flow = _canonical_flow()
    flow["nodes"] = [
        node for node in flow["nodes"] if node["id"] != "session_guidance"
    ]
    flow["edges"] = [
        edge
        for edge in flow["edges"]
        if "session_guidance" not in {edge["from"], edge["to"]}
    ]
    flow["edges"].append(
        {"from": "identity_context", "to": "persona_reference"}
    )
    return flow


def _old_custom_flow() -> dict:
    flow = _old_flow()
    identity_index = next(
        index
        for index, node in enumerate(flow["nodes"])
        if node["id"] == "identity_context"
    )
    flow["nodes"].insert(
        identity_index + 1,
        {
            "id": "custom_node",
            "type": "template",
            "template_key": "chat/custom",
            "label": "system: custom",
        },
    )
    flow["edges"] = [
        edge
        for edge in flow["edges"]
        if (edge["from"], edge["to"])
        != ("identity_context", "persona_reference")
    ]
    flow["edges"].extend(
        [
            {"from": "identity_context", "to": "custom_node"},
            {"from": "custom_node", "to": "persona_reference"},
        ]
    )
    return flow


def _write_flow(path: Path, flow: dict, *, compact: bool = False) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        flow,
        ensure_ascii=False,
        separators=(",", ":") if compact else None,
        indent=None if compact else 2,
    ) + "\n"
    data = text.encode("utf-8")
    path.write_bytes(data)
    return data


def _flow_v1() -> dict:
    flow = _canonical_flow()
    flow["version"] = 1
    private_edges = [
        edge
        for edge in flow["edges"]
        if (edge.get("from"), edge.get("to"))
        == ("base_contract", "private_policy")
    ]
    assert len(private_edges) == 1
    private_edges[0]["platforms"] = ["web"]
    return flow


def test_migrate_internal_private_flow_v2_only_extends_unique_core_edge():
    from core.prompt_v2.flow_migrations import migrate_internal_private_flow_v2

    original = _flow_v1()
    original["custom_top_level"] = {"owner": "operator"}
    private_edge = next(
        edge
        for edge in original["edges"]
        if (edge.get("from"), edge.get("to"))
        == ("base_contract", "private_policy")
    )
    private_edge["custom_note"] = "preserve-this-field"
    before = copy.deepcopy(original)

    migrated, changed = migrate_internal_private_flow_v2(original)

    assert changed is True
    assert original == before
    assert migrated["version"] == 2
    migrated_edge = next(
        edge
        for edge in migrated["edges"]
        if (edge.get("from"), edge.get("to"))
        == ("base_contract", "private_policy")
    )
    assert migrated_edge["platforms"] == [
        "web",
        "internal",
        "external_private",
    ]
    assert migrated_edge["chat_types"] == ["private"]
    assert migrated_edge["custom_note"] == "preserve-this-field"
    assert migrated["custom_top_level"] == {"owner": "operator"}

    expected = copy.deepcopy(before)
    expected["version"] = 2
    expected_edge = next(
        edge
        for edge in expected["edges"]
        if (edge.get("from"), edge.get("to"))
        == ("base_contract", "private_policy")
    )
    expected_edge["platforms"] = [
        "web",
        "internal",
        "external_private",
    ]
    assert migrated == expected


def test_migrate_internal_private_flow_v2_is_idempotent():
    from core.prompt_v2.flow_migrations import migrate_internal_private_flow_v2

    migrated, changed = migrate_internal_private_flow_v2(_flow_v1())
    repeated, repeated_changed = migrate_internal_private_flow_v2(migrated)

    assert changed is True
    assert repeated_changed is False
    assert repeated == migrated
    assert repeated is not migrated


@pytest.mark.parametrize(
    "case",
    [
        "missing_edge",
        "duplicate_edge",
        "changed_platforms",
        "changed_chat_types",
        "partial_v2",
        "future_version",
    ],
)
def test_migrate_internal_private_flow_v2_rejects_core_conflicts(case):
    from core.prompt_v2.flow_migrations import (
        PromptFlowMigrationError,
        migrate_internal_private_flow_v2,
    )

    flow = _flow_v1()
    private_edge = next(
        edge
        for edge in flow["edges"]
        if (edge.get("from"), edge.get("to"))
        == ("base_contract", "private_policy")
    )
    if case == "missing_edge":
        flow["edges"].remove(private_edge)
    elif case == "duplicate_edge":
        flow["edges"].append(copy.deepcopy(private_edge))
    elif case == "changed_platforms":
        private_edge["platforms"] = ["qq"]
    elif case == "changed_chat_types":
        private_edge["chat_types"] = ["group"]
    elif case == "partial_v2":
        private_edge["platforms"] = ["web", "internal"]
    elif case == "future_version":
        flow["version"] = 3
    before = copy.deepcopy(flow)

    with pytest.raises(PromptFlowMigrationError):
        migrate_internal_private_flow_v2(flow)

    assert flow == before


def test_flow_v2_plan_then_apply_creates_exact_backup(tmp_path):
    from core.prompt_v2.flow_migrations import (
        apply_runtime_flow_v2,
        plan_runtime_flow_v2,
    )

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    plan_dir = tmp_path / "plans"
    backup_dir = tmp_path / "backups"
    original = _write_flow(runtime_path, _flow_v1(), compact=True)

    plan = plan_runtime_flow_v2(runtime_path, plan_dir=plan_dir)

    assert runtime_path.read_bytes() == original
    assert plan["changed"] is True
    assert len(plan["plan_id"]) == 64
    assert plan["source_sha256"] == hashlib.sha256(original).hexdigest()
    plan_record = json.loads(Path(plan["plan_path"]).read_text(encoding="utf-8"))
    assert "nodes" not in plan_record
    assert "edges" not in plan_record

    result = apply_runtime_flow_v2(
        runtime_path,
        plan_dir=plan_dir,
        backup_dir=backup_dir,
        plan_id=plan["plan_id"],
    )

    assert result["applied"] is True
    assert Path(result["backup_path"]).read_bytes() == original
    applied = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert applied["version"] == 2
    applied_edge = next(
        edge
        for edge in applied["edges"]
        if (edge.get("from"), edge.get("to"))
        == ("base_contract", "private_policy")
    )
    assert applied_edge["platforms"] == [
        "web",
        "internal",
        "external_private",
    ]

    applied_bytes = runtime_path.read_bytes()
    fixed_time_ns = 1_700_000_000_000_000_000
    os.utime(runtime_path, ns=(fixed_time_ns, fixed_time_ns))
    repeated_plan = plan_runtime_flow_v2(runtime_path, plan_dir=plan_dir)
    assert repeated_plan["changed"] is False
    repeated = apply_runtime_flow_v2(
        runtime_path,
        plan_dir=plan_dir,
        backup_dir=backup_dir,
        plan_id=repeated_plan["plan_id"],
    )
    assert repeated["applied"] is False
    assert repeated["backup_path"] == ""
    assert runtime_path.read_bytes() == applied_bytes
    assert runtime_path.stat().st_mtime_ns == fixed_time_ns
    assert len(list(backup_dir.glob("*.bak"))) == 1


def test_flow_v2_apply_uses_atomic_replace_helper(tmp_path, monkeypatch):
    from core.prompt_v2 import flow_migrations

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    plan_dir = tmp_path / "plans"
    backup_dir = tmp_path / "backups"
    _write_flow(runtime_path, _flow_v1(), compact=True)
    plan = flow_migrations.plan_runtime_flow_v2(
        runtime_path,
        plan_dir=plan_dir,
    )
    calls: list[tuple[Path, bytes]] = []
    real_atomic_replace = flow_migrations.atomic_replace_bytes

    def observed_atomic_replace(target, data):
        calls.append((Path(target), data))
        real_atomic_replace(target, data)

    monkeypatch.setattr(
        flow_migrations,
        "atomic_replace_bytes",
        observed_atomic_replace,
    )

    result = flow_migrations.apply_runtime_flow_v2(
        runtime_path,
        plan_dir=plan_dir,
        backup_dir=backup_dir,
        plan_id=plan["plan_id"],
    )

    assert result["applied"] is True
    assert calls == [(runtime_path, runtime_path.read_bytes())]
    assert hashlib.sha256(calls[0][1]).hexdigest() == plan["target_sha256"]


def test_flow_v2_apply_atomic_replace_failure_preserves_runtime(tmp_path, monkeypatch):
    from core.prompt_v2 import flow_migrations

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    plan_dir = tmp_path / "plans"
    backup_dir = tmp_path / "backups"
    original = _write_flow(runtime_path, _flow_v1(), compact=True)
    plan = flow_migrations.plan_runtime_flow_v2(
        runtime_path,
        plan_dir=plan_dir,
    )
    real_replace = Path.replace

    def fail_runtime_replace(path, target):
        if Path(target) == runtime_path and path.parent == runtime_path.parent:
            raise OSError("注入原子替换失败")
        return real_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_runtime_replace)

    with pytest.raises(OSError, match="注入原子替换失败"):
        flow_migrations.apply_runtime_flow_v2(
            runtime_path,
            plan_dir=plan_dir,
            backup_dir=backup_dir,
            plan_id=plan["plan_id"],
        )

    assert runtime_path.read_bytes() == original
    backups = list(backup_dir.glob("*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert list(runtime_path.parent.glob(f".{runtime_path.name}.*.tmp")) == []


def test_flow_v2_apply_replay_returns_already_applied_without_new_backup(tmp_path):
    from core.prompt_v2.flow_migrations import (
        apply_runtime_flow_v2,
        plan_runtime_flow_v2,
    )

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    plan_dir = tmp_path / "plans"
    backup_dir = tmp_path / "backups"
    _write_flow(runtime_path, _flow_v1(), compact=True)
    plan = plan_runtime_flow_v2(runtime_path, plan_dir=plan_dir)

    first = apply_runtime_flow_v2(
        runtime_path,
        plan_dir=plan_dir,
        backup_dir=backup_dir,
        plan_id=plan["plan_id"],
    )
    applied_bytes = runtime_path.read_bytes()
    replay = apply_runtime_flow_v2(
        runtime_path,
        plan_dir=plan_dir,
        backup_dir=backup_dir,
        plan_id=plan["plan_id"],
    )

    assert first["applied"] is True
    assert first["already_applied"] is False
    assert replay["applied"] is False
    assert replay["already_applied"] is True
    assert replay["backup_path"] == ""
    assert runtime_path.read_bytes() == applied_bytes
    assert len(list(backup_dir.glob("*.bak"))) == 1


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("schema_version", True, "计划版本不支持"),
        ("to_version", True, "计划状态非法"),
        ("source_sha256", int("1" * 64), "文件摘要非法"),
    ],
)
def test_flow_v2_replay_rejects_plan_fields_with_wrong_json_types(
    tmp_path,
    field,
    value,
    error,
):
    from core.prompt_v2 import flow_migrations

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    plan_dir = tmp_path / "plans"
    backup_dir = tmp_path / "backups"
    _write_flow(runtime_path, _flow_v1(), compact=True)
    plan = flow_migrations.plan_runtime_flow_v2(
        runtime_path,
        plan_dir=plan_dir,
    )
    flow_migrations.apply_runtime_flow_v2(
        runtime_path,
        plan_dir=plan_dir,
        backup_dir=backup_dir,
        plan_id=plan["plan_id"],
    )

    record = json.loads(Path(plan["plan_path"]).read_text(encoding="utf-8"))
    record[field] = value
    fields = {key: item for key, item in record.items() if key != "plan_id"}
    forged_id = flow_migrations._flow_v2_plan_id(fields)
    record["plan_id"] = forged_id
    forged_path = plan_dir / f"flow-v2.{forged_id}.json"
    forged_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(flow_migrations.PromptFlowMigrationError, match=error):
        flow_migrations.apply_runtime_flow_v2(
            runtime_path,
            plan_dir=plan_dir,
            backup_dir=backup_dir,
            plan_id=forged_id,
        )

    assert len(list(backup_dir.glob("*.bak"))) == 1


def test_flow_v2_concurrent_apply_replay_writes_once(tmp_path, monkeypatch):
    from core.prompt_v2 import flow_migrations

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    plan_dir = tmp_path / "plans"
    backup_dir = tmp_path / "backups"
    _write_flow(runtime_path, _flow_v1(), compact=True)
    plan = flow_migrations.plan_runtime_flow_v2(
        runtime_path,
        plan_dir=plan_dir,
    )

    backup_ready = threading.Event()
    release_first_apply = threading.Event()
    second_lock_attempted = threading.Event()
    second_lock_entered = threading.Event()
    results: list[dict] = []
    errors: list[BaseException] = []
    real_create_backup = flow_migrations._create_exact_backup
    real_flow_write_lock = flow_migrations.flow_write_lock
    second_thread_name = "flow-v2-second-apply"

    def paused_create_backup(*args, **kwargs):
        backup_path = real_create_backup(*args, **kwargs)
        backup_ready.set()
        if not release_first_apply.wait(timeout=5):
            raise TimeoutError("等待第二个 apply 超时")
        return backup_path

    monkeypatch.setattr(
        flow_migrations,
        "_create_exact_backup",
        paused_create_backup,
    )

    @contextmanager
    def observed_flow_write_lock(target):
        is_second = threading.current_thread().name == second_thread_name
        if is_second:
            second_lock_attempted.set()
        with real_flow_write_lock(target):
            if is_second:
                second_lock_entered.set()
            yield

    monkeypatch.setattr(
        flow_migrations,
        "flow_write_lock",
        observed_flow_write_lock,
    )

    def run_apply():
        try:
            results.append(
                flow_migrations.apply_runtime_flow_v2(
                    runtime_path,
                    plan_dir=plan_dir,
                    backup_dir=backup_dir,
                    plan_id=plan["plan_id"],
                )
            )
        except BaseException as exc:  # pragma: no cover - 仅用于跨线程传递
            errors.append(exc)

    first_thread = threading.Thread(target=run_apply)
    second_thread = threading.Thread(
        target=run_apply,
        name=second_thread_name,
    )
    first_thread.start()
    assert backup_ready.wait(timeout=5)
    second_thread.start()
    assert second_lock_attempted.wait(timeout=5)
    assert not second_lock_entered.wait(timeout=0.05)
    assert second_thread.is_alive()
    release_first_apply.set()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert second_lock_entered.is_set()
    assert errors == []
    assert sorted(
        (result["applied"], result["already_applied"])
        for result in results
    ) == [(False, True), (True, False)]
    assert len(list(backup_dir.glob("*.bak"))) == 1
    assert json.loads(runtime_path.read_text(encoding="utf-8"))["version"] == 2


def test_flow_v2_apply_rejects_source_hash_change_without_writes(tmp_path):
    from core.prompt_v2.flow_migrations import (
        PromptFlowMigrationError,
        apply_runtime_flow_v2,
        plan_runtime_flow_v2,
    )

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    plan_dir = tmp_path / "plans"
    backup_dir = tmp_path / "backups"
    original = _write_flow(runtime_path, _flow_v1(), compact=True)
    plan = plan_runtime_flow_v2(runtime_path, plan_dir=plan_dir)
    changed_after_plan = original + b" "
    runtime_path.write_bytes(changed_after_plan)

    with pytest.raises(PromptFlowMigrationError, match="源文件.*变化"):
        apply_runtime_flow_v2(
            runtime_path,
            plan_dir=plan_dir,
            backup_dir=backup_dir,
            plan_id=plan["plan_id"],
        )

    assert runtime_path.read_bytes() == changed_after_plan
    assert not backup_dir.exists()


def test_flow_v2_apply_rejects_malicious_plan_id_without_writes(tmp_path):
    from core.prompt_v2.flow_migrations import (
        PromptFlowMigrationError,
        apply_runtime_flow_v2,
    )

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    backup_dir = tmp_path / "backups"
    original = _write_flow(runtime_path, _flow_v1(), compact=True)

    with pytest.raises(PromptFlowMigrationError, match="plan_id 非法"):
        apply_runtime_flow_v2(
            runtime_path,
            plan_dir=tmp_path / "plans",
            backup_dir=backup_dir,
            plan_id="../flow-v2-plan",
        )

    assert runtime_path.read_bytes() == original
    assert not backup_dir.exists()


def test_flow_v2_apply_rejects_symlinked_plan_directory_without_writes(tmp_path):
    from core.prompt_v2.flow_migrations import (
        PromptFlowMigrationError,
        apply_runtime_flow_v2,
        plan_runtime_flow_v2,
    )

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    real_plan_dir = tmp_path / "real-plans"
    linked_plan_dir = tmp_path / "linked-plans"
    backup_dir = tmp_path / "backups"
    original = _write_flow(runtime_path, _flow_v1(), compact=True)
    plan = plan_runtime_flow_v2(runtime_path, plan_dir=real_plan_dir)
    linked_plan_dir.symlink_to(real_plan_dir, target_is_directory=True)

    with pytest.raises(PromptFlowMigrationError, match="迁移计划目录"):
        apply_runtime_flow_v2(
            runtime_path,
            plan_dir=linked_plan_dir,
            backup_dir=backup_dir,
            plan_id=plan["plan_id"],
        )

    assert runtime_path.read_bytes() == original
    assert not backup_dir.exists()


def test_flow_v2_apply_rejects_symlinked_backup_directory_without_writes(tmp_path):
    from core.prompt_v2.flow_migrations import (
        PromptFlowMigrationError,
        apply_runtime_flow_v2,
        plan_runtime_flow_v2,
    )

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    plan_dir = tmp_path / "plans"
    real_backup_dir = tmp_path / "real-backups"
    linked_backup_dir = tmp_path / "linked-backups"
    original = _write_flow(runtime_path, _flow_v1(), compact=True)
    plan = plan_runtime_flow_v2(runtime_path, plan_dir=plan_dir)
    real_backup_dir.mkdir()
    linked_backup_dir.symlink_to(real_backup_dir, target_is_directory=True)

    with pytest.raises(PromptFlowMigrationError, match="备份目录"):
        apply_runtime_flow_v2(
            runtime_path,
            plan_dir=plan_dir,
            backup_dir=linked_backup_dir,
            plan_id=plan["plan_id"],
        )

    assert runtime_path.read_bytes() == original
    assert list(real_backup_dir.iterdir()) == []


def test_flow_v2_apply_and_admin_save_share_write_lock(tmp_path, monkeypatch):
    from core.prompt_v2 import flow as flow_module
    from core.prompt_v2 import flow_migrations

    runtime_dir = tmp_path / "runtime"
    runtime_path = runtime_dir / "chat" / "flow.json"
    plan_dir = tmp_path / "plans"
    backup_dir = tmp_path / "backups"
    _write_flow(runtime_path, _flow_v1(), compact=True)
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))
    plan = flow_migrations.plan_runtime_flow_v2(
        runtime_path,
        plan_dir=plan_dir,
    )

    backup_ready = threading.Event()
    release_apply = threading.Event()
    save_lock_attempted = threading.Event()
    save_lock_entered = threading.Event()
    errors: list[BaseException] = []
    real_create_backup = flow_migrations._create_exact_backup
    real_save_flow_lock = flow_module.flow_write_lock

    def paused_create_backup(*args, **kwargs):
        backup_path = real_create_backup(*args, **kwargs)
        backup_ready.set()
        if not release_apply.wait(timeout=5):
            raise TimeoutError("等待并发保存超时")
        return backup_path

    monkeypatch.setattr(
        flow_migrations,
        "_create_exact_backup",
        paused_create_backup,
    )

    @contextmanager
    def observed_save_flow_lock(target):
        save_lock_attempted.set()
        with real_save_flow_lock(target):
            save_lock_entered.set()
            yield

    monkeypatch.setattr(
        flow_module,
        "flow_write_lock",
        observed_save_flow_lock,
    )

    def run_apply():
        try:
            flow_migrations.apply_runtime_flow_v2(
                runtime_path,
                plan_dir=plan_dir,
                backup_dir=backup_dir,
                plan_id=plan["plan_id"],
            )
        except BaseException as exc:  # pragma: no cover - 仅用于跨线程传递
            errors.append(exc)

    concurrent_flow = _canonical_flow()
    concurrent_node = next(
        node
        for node in concurrent_flow["nodes"]
        if node["id"] == "session_guidance"
    )
    concurrent_node["concurrent_note"] = "admin-save-must-win"

    def run_save():
        try:
            flow_module.save_flow(concurrent_flow)
        except BaseException as exc:  # pragma: no cover - 仅用于跨线程传递
            errors.append(exc)

    apply_thread = threading.Thread(target=run_apply)
    save_thread = threading.Thread(target=run_save)
    apply_thread.start()
    assert backup_ready.wait(timeout=5)
    save_thread.start()
    assert save_lock_attempted.wait(timeout=5)
    assert not save_lock_entered.wait(timeout=0.05)
    assert save_thread.is_alive()
    release_apply.set()
    apply_thread.join(timeout=5)
    save_thread.join(timeout=5)

    assert not apply_thread.is_alive()
    assert not save_thread.is_alive()
    assert save_lock_entered.is_set()
    assert errors == []
    final_flow = json.loads(runtime_path.read_text(encoding="utf-8"))
    final_node = next(
        node
        for node in final_flow["nodes"]
        if node["id"] == "session_guidance"
    )
    assert final_node["concurrent_note"] == "admin-save-must-win"
    assert len(list(backup_dir.glob("*.bak"))) == 1


def test_flow_v2_plan_conflict_preserves_original_bytes(tmp_path):
    from core.prompt_v2.flow_migrations import (
        PromptFlowMigrationError,
        plan_runtime_flow_v2,
    )

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    plan_dir = tmp_path / "plans"
    flow = _flow_v1()
    private_edge = next(
        edge
        for edge in flow["edges"]
        if (edge.get("from"), edge.get("to"))
        == ("base_contract", "private_policy")
    )
    private_edge["platforms"] = ["web", "internal"]
    original = _write_flow(runtime_path, flow, compact=True)

    with pytest.raises(PromptFlowMigrationError):
        plan_runtime_flow_v2(runtime_path, plan_dir=plan_dir)

    assert runtime_path.read_bytes() == original
    assert not plan_dir.exists()


def test_flow_v2_plan_rejects_pre_session_guidance_baseline_with_clear_error(
    tmp_path,
):
    from core.prompt_v2.flow_migrations import (
        PromptFlowMigrationError,
        plan_runtime_flow_v2,
    )

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    plan_dir = tmp_path / "plans"
    legacy = _old_flow()
    legacy["version"] = 1
    private_edge = next(
        edge
        for edge in legacy["edges"]
        if (edge.get("from"), edge.get("to"))
        == ("base_contract", "private_policy")
    )
    private_edge["platforms"] = ["web"]
    original = _write_flow(runtime_path, legacy, compact=True)

    with pytest.raises(
        PromptFlowMigrationError,
        match="先完成 session_guidance Flow 迁移",
    ):
        plan_runtime_flow_v2(runtime_path, plan_dir=plan_dir)

    assert runtime_path.read_bytes() == original
    assert not plan_dir.exists()


def test_migrate_flow_inserts_guidance_after_identity_and_preserves_custom_node():
    from core.prompt_v2.flow_migrations import migrate_session_guidance_flow

    original = _old_custom_flow()
    migrated, changed = migrate_session_guidance_flow(original)

    assert changed is True
    assert original == _old_custom_flow()
    assert [node["id"] for node in migrated["nodes"]].count(
        "session_guidance"
    ) == 1
    assert {
        f"{edge['from']}->{edge['to']}" for edge in migrated["edges"]
    } >= {
        "identity_context->session_guidance",
        "session_guidance->custom_node",
        "custom_node->persona_reference",
    }


def test_migrate_flow_preserves_disjoint_edge_conditions():
    from core.prompt_v2.flow_migrations import migrate_session_guidance_flow

    flow = _old_flow()
    flow["nodes"] = [
        node for node in flow["nodes"] if node["id"] != "persona_reference"
    ] + [
        {
            "id": "custom_group",
            "type": "template",
            "template_key": "chat/custom_group",
            "chat_types": ["group"],
        },
        {
            "id": "custom_private",
            "type": "template",
            "template_key": "chat/custom_private",
            "chat_types": ["private"],
        },
        next(
            node
            for node in _old_flow()["nodes"]
            if node["id"] == "persona_reference"
        ),
    ]
    flow["edges"] = [
        edge
        for edge in flow["edges"]
        if (edge["from"], edge["to"])
        != ("identity_context", "persona_reference")
    ]
    flow["edges"].extend(
        [
            {
                "from": "identity_context",
                "to": "custom_group",
                "chat_types": ["group"],
                "note": "keep-group",
            },
            {
                "from": "identity_context",
                "to": "custom_private",
                "chat_types": ["private"],
                "platforms": ["qq", "web"],
                "note": "keep-private",
            },
            {
                "from": "custom_group",
                "to": "persona_reference",
                "chat_types": ["group"],
            },
            {
                "from": "custom_private",
                "to": "persona_reference",
                "chat_types": ["private"],
            },
        ]
    )

    migrated, changed = migrate_session_guidance_flow(flow)

    assert changed is True
    outgoing = [
        edge
        for edge in migrated["edges"]
        if edge["from"] == "session_guidance"
    ]
    assert {
        (edge["to"], tuple(edge.get("chat_types", [])), edge.get("note", ""))
        for edge in outgoing
    } == {
        ("custom_group", ("group",), "keep-group"),
        ("custom_private", ("private",), "keep-private"),
    }
    private_edge = next(edge for edge in outgoing if edge["to"] == "custom_private")
    assert private_edge["platforms"] == ["qq", "web"]


def test_migrate_flow_is_idempotent_for_current_contract():
    from core.prompt_v2.flow_migrations import migrate_session_guidance_flow

    flow = _canonical_flow()

    migrated, changed = migrate_session_guidance_flow(flow)

    assert changed is False
    assert migrated == flow
    assert migrated is not flow


def test_runtime_flow_migrates_old_canonical_context_order():
    from core.prompt_v2.flow import validate_runtime_contract
    from core.prompt_v2.flow_migrations import migrate_runtime_flow

    flow = _canonical_flow()
    flow["edges"] = [
        edge
        for edge in flow["edges"]
        if (edge["from"], edge["to"])
        not in {
            ("session_guidance", "summary_context"),
            ("history_messages", "group_context"),
            ("history_messages", "project_context"),
            ("project_context", "persona_reference"),
        }
    ]
    flow["edges"].extend([
        {
            "from": "session_guidance",
            "to": "group_context",
            "chat_types": ["group"],
        },
        {
            "from": "session_guidance",
            "to": "project_context",
            "chat_types": ["private"],
        },
        {"from": "project_context", "to": "summary_context"},
        {"from": "history_messages", "to": "persona_reference"},
    ])

    migrated, changed = migrate_runtime_flow(flow)

    assert changed is True
    validate_runtime_contract(migrated)
    assert {
        (edge["from"], edge["to"])
        for edge in migrated["edges"]
    } >= {
        ("session_guidance", "summary_context"),
        ("history_messages", "group_context"),
        ("history_messages", "project_context"),
        ("project_context", "persona_reference"),
    }


@pytest.mark.asyncio
async def test_migrated_runtime_flow_strict_compiles_all_live_branches(
    tmp_path,
    monkeypatch,
):
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.flow_migrations import migrate_session_guidance_flow
    from core.prompt_v2.schema import PromptCompileRequest

    migrated, changed = migrate_session_guidance_flow(_old_flow())
    assert changed is True
    runtime_flow = tmp_path / "chat" / "flow.json"
    _write_flow(runtime_flow, migrated)
    monkeypatch.setattr(
        "core.prompt_v2.flow.runtime_flow_path",
        lambda: runtime_flow,
    )

    for platform, chat_type in (
        ("qq", "group"),
        ("qq", "private"),
        ("web", "group"),
        ("web", "private"),
    ):
        plan = await compile_prompt_plan(
            PromptCompileRequest(
                platform=platform,
                chat_type=chat_type,
                session_id=(
                    "group_migrated-flow"
                    if chat_type == "group"
                    else "private_migrated-flow"
                ),
                group_id="migrated-flow" if chat_type == "group" else "",
                user_id="migration-user",
                user_input="验证迁移后的运行时 flow",
                session_guidance=f"{platform}/{chat_type} 指导",
                session_guidance_chat_stream_id=(
                    f"{platform}:migrated-flow:{chat_type}"
                ),
                debug={"session_guidance_resolution_status": "configured"},
            ),
            strict_audit=True,
        )

        section = next(
            item
            for item in plan.flow_sections
            if item["node_id"] == "session_guidance"
        )
        assert section["status"] == "emitted"
        assert len(section["message_indexes"]) == 1
        assert plan.debug["flow_source"] == "runtime"
        assert plan.messages[-1]["role"] == "user"


@pytest.mark.parametrize(
    "case",
    [
        "missing_identity",
        "identity_without_downstream",
        "existing_wrong_type",
        "existing_wrong_runtime_key",
        "existing_duplicate",
        "existing_wrong_edge",
        "existing_conditional_identity_edge",
        "existing_extra_identity_edge",
        "existing_extra_guidance_incoming",
        "existing_without_downstream",
    ],
)
def test_migrate_flow_rejects_unsafe_structures(case):
    from core.prompt_v2.flow_migrations import (
        PromptFlowMigrationError,
        migrate_session_guidance_flow,
    )

    flow = _canonical_flow() if case.startswith("existing_") else _old_flow()
    if case == "missing_identity":
        flow["nodes"] = [
            node for node in flow["nodes"] if node["id"] != "identity_context"
        ]
        flow["edges"] = [
            edge
            for edge in flow["edges"]
            if "identity_context" not in {edge["from"], edge["to"]}
        ]
    elif case == "identity_without_downstream":
        flow["edges"] = [
            edge
            for edge in flow["edges"]
            if edge["from"] != "identity_context"
        ]
    elif case == "existing_wrong_type":
        node = next(
            node for node in flow["nodes"] if node["id"] == "session_guidance"
        )
        node["type"] = "template"
        node["template_key"] = "chat/main"
        node.pop("runtime_key")
    elif case == "existing_wrong_runtime_key":
        node = next(
            node for node in flow["nodes"] if node["id"] == "session_guidance"
        )
        node["runtime_key"] = "persona_reference"
    elif case == "existing_duplicate":
        flow["nodes"].append(
            {
                "id": "session_guidance_copy",
                "type": "runtime",
                "runtime_key": "session_guidance",
            }
        )
    elif case == "existing_wrong_edge":
        flow["edges"] = [
            edge
            for edge in flow["edges"]
            if (edge["from"], edge["to"])
            != ("identity_context", "session_guidance")
        ]
        flow["edges"].append(
            {"from": "identity_context", "to": "persona_reference"}
        )
    elif case == "existing_conditional_identity_edge":
        edge = next(
            edge
            for edge in flow["edges"]
            if (edge["from"], edge["to"])
            == ("identity_context", "session_guidance")
        )
        edge["chat_types"] = ["private"]
    elif case == "existing_extra_identity_edge":
        flow["edges"].append(
            {"from": "identity_context", "to": "persona_reference"}
        )
    elif case == "existing_extra_guidance_incoming":
        flow["edges"].append(
            {"from": "runtime_context", "to": "session_guidance"}
        )
    elif case == "existing_without_downstream":
        flow["edges"] = [
            edge
            for edge in flow["edges"]
            if edge["from"] != "session_guidance"
        ]

    with pytest.raises(PromptFlowMigrationError):
        migrate_session_guidance_flow(flow)


def test_upgrade_runtime_flow_creates_one_exact_backup_and_is_idempotent(tmp_path):
    from core.prompt_v2.flow_migrations import (
        list_session_guidance_flow_backups,
        upgrade_runtime_flow_file,
    )

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    backup_dir = tmp_path / "backups"
    original = _write_flow(runtime_path, _old_custom_flow(), compact=True)

    result = upgrade_runtime_flow_file(runtime_path, backup_dir=backup_dir)

    assert result["flow_migrated"] is True
    backup_path = Path(result["flow_backup_path"])
    assert backup_path.parent == backup_dir
    assert backup_path.read_bytes() == original
    assert json.loads(runtime_path.read_text(encoding="utf-8")) != json.loads(
        original.decode("utf-8")
    )
    first_upgraded = runtime_path.read_bytes()
    fixed_time_ns = 1_700_000_000_000_000_000
    os.utime(runtime_path, ns=(fixed_time_ns, fixed_time_ns))
    assert len(list_session_guidance_flow_backups(backup_dir=backup_dir)) == 1

    repeated = upgrade_runtime_flow_file(runtime_path, backup_dir=backup_dir)

    assert repeated["flow_migrated"] is False
    assert repeated["flow_backup_path"] == ""
    assert runtime_path.read_bytes() == first_upgraded
    assert runtime_path.stat().st_mtime_ns == fixed_time_ns
    assert len(list_session_guidance_flow_backups(backup_dir=backup_dir)) == 1


def test_upgrade_runtime_flow_replace_failure_preserves_original_bytes(
    tmp_path,
    monkeypatch,
):
    from core.prompt_v2.flow_migrations import upgrade_runtime_flow_file

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    backup_dir = tmp_path / "backups"
    original = _write_flow(runtime_path, _old_flow(), compact=True)
    real_replace = Path.replace

    def fail_runtime_replace(path, target):
        if Path(target) == runtime_path:
            raise OSError("replace failed")
        return real_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_runtime_replace)

    with pytest.raises(OSError, match="replace failed"):
        upgrade_runtime_flow_file(runtime_path, backup_dir=backup_dir)

    assert runtime_path.read_bytes() == original
    assert list(runtime_path.parent.glob("*.tmp")) == []


def test_upgrade_runtime_flow_rejects_backup_parent_symlink(tmp_path):
    from core.prompt_v2.flow_migrations import (
        PromptFlowMigrationError,
        upgrade_runtime_flow_file,
    )

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    original = _write_flow(runtime_path, _old_flow(), compact=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_parent = tmp_path / "prompt_template_backups"
    linked_parent.symlink_to(outside, target_is_directory=True)
    backup_dir = linked_parent / "session_guidance_flow"

    with pytest.raises(PromptFlowMigrationError, match="符号链接"):
        upgrade_runtime_flow_file(runtime_path, backup_dir=backup_dir)

    assert runtime_path.read_bytes() == original
    assert not (outside / "session_guidance_flow").exists()


def test_backup_listing_returns_metadata_without_flow_body(tmp_path):
    from core.prompt_v2.flow_migrations import (
        list_session_guidance_flow_backups,
        upgrade_runtime_flow_file,
    )

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    backup_dir = tmp_path / "backups"
    flow = _old_flow()
    flow["private_note"] = "DO_NOT_RETURN_FLOW_BODY"
    _write_flow(runtime_path, flow)
    upgrade_runtime_flow_file(runtime_path, backup_dir=backup_dir)
    (backup_dir / "unrelated.txt").write_text("ignore", encoding="utf-8")

    items = list_session_guidance_flow_backups(backup_dir=backup_dir)

    assert len(items) == 1
    assert set(items[0]) == {"name", "created_at", "size_bytes", "sha256"}
    assert len(items[0]["sha256"]) == 64
    assert "DO_NOT_RETURN_FLOW_BODY" not in json.dumps(items, ensure_ascii=False)


def test_backup_listing_ignores_symbolic_links(tmp_path):
    from core.prompt_v2.flow_migrations import (
        list_session_guidance_flow_backups,
        upgrade_runtime_flow_file,
    )

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    backup_dir = tmp_path / "backups"
    _write_flow(runtime_path, _old_flow())
    upgraded = upgrade_runtime_flow_file(runtime_path, backup_dir=backup_dir)
    backup_name = Path(upgraded["flow_backup_path"]).name
    link_name = "chat-flow.20260713T120000000000Z.abcdef123456.json.bak"
    (backup_dir / link_name).symlink_to(backup_name)

    items = list_session_guidance_flow_backups(backup_dir=backup_dir)

    assert [item["name"] for item in items] == [backup_name]


def test_rollback_restores_exact_old_bytes_and_guards_current_flow(tmp_path):
    from core.prompt_v2.flow_migrations import (
        list_session_guidance_flow_backups,
        rollback_session_guidance_flow,
        upgrade_runtime_flow_file,
    )

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    backup_dir = tmp_path / "backups"
    original = _write_flow(runtime_path, _old_custom_flow(), compact=True)
    upgraded = upgrade_runtime_flow_file(runtime_path, backup_dir=backup_dir)
    upgraded_bytes = runtime_path.read_bytes()
    backup_name = Path(upgraded["flow_backup_path"]).name

    restored = rollback_session_guidance_flow(
        runtime_path,
        backup_dir=backup_dir,
        backup_name=backup_name,
    )

    assert restored == runtime_path
    assert runtime_path.read_bytes() == original
    backups = list_session_guidance_flow_backups(backup_dir=backup_dir)
    assert len(backups) == 2
    guard = next(item for item in backups if item["name"] != backup_name)
    assert (backup_dir / guard["name"]).read_bytes() == upgraded_bytes


@pytest.mark.parametrize(
    "backup_name",
    [
        "../chat-flow.json.bak",
        "..",
        "/tmp/chat-flow.json.bak",
        "nested/chat-flow.json.bak",
        r"nested\chat-flow.json.bak",
    ],
)
def test_rollback_rejects_unsafe_backup_names(tmp_path, backup_name):
    from core.prompt_v2.flow_migrations import (
        PromptFlowMigrationError,
        rollback_session_guidance_flow,
    )

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    _write_flow(runtime_path, _canonical_flow())

    with pytest.raises(PromptFlowMigrationError):
        rollback_session_guidance_flow(
            runtime_path,
            backup_dir=tmp_path / "backups",
            backup_name=backup_name,
        )


def test_rollback_rejects_invalid_backup_without_changing_runtime(tmp_path):
    from core.prompt_v2.flow_migrations import (
        PromptFlowMigrationError,
        rollback_session_guidance_flow,
    )

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    backup_dir = tmp_path / "backups"
    current = _write_flow(runtime_path, _canonical_flow(), compact=True)
    backup_dir.mkdir()
    backup_name = "chat-flow.20260713T120000000000Z.abcdef123456.json.bak"
    (backup_dir / backup_name).write_text('{"nodes": [], "edges": []}\n')

    with pytest.raises(PromptFlowMigrationError):
        rollback_session_guidance_flow(
            runtime_path,
            backup_dir=backup_dir,
            backup_name=backup_name,
        )

    assert runtime_path.read_bytes() == current
    assert len(list(backup_dir.iterdir())) == 1


def test_rollback_rejects_symbolic_link_backup(tmp_path):
    from core.prompt_v2.flow_migrations import (
        PromptFlowMigrationError,
        rollback_session_guidance_flow,
    )

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    backup_dir = tmp_path / "backups"
    current = _write_flow(runtime_path, _canonical_flow(), compact=True)
    backup_dir.mkdir()
    target = backup_dir / "target.json"
    _write_flow(target, _old_flow(), compact=True)
    backup_name = "chat-flow.20260713T120000000000Z.abcdef123456.json.bak"
    (backup_dir / backup_name).symlink_to(target.name)

    with pytest.raises(PromptFlowMigrationError):
        rollback_session_guidance_flow(
            runtime_path,
            backup_dir=backup_dir,
            backup_name=backup_name,
        )

    assert runtime_path.read_bytes() == current


def test_rollback_rejects_backup_whose_content_does_not_match_name_digest(
    tmp_path,
):
    from core.prompt_v2.flow_migrations import (
        PromptFlowMigrationError,
        list_session_guidance_flow_backups,
        rollback_session_guidance_flow,
        upgrade_runtime_flow_file,
    )

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    backup_dir = tmp_path / "backups"
    _write_flow(runtime_path, _old_flow(), compact=True)
    upgraded = upgrade_runtime_flow_file(runtime_path, backup_dir=backup_dir)
    current = runtime_path.read_bytes()
    backup_path = Path(upgraded["flow_backup_path"])
    backup_path.write_bytes(
        (json.dumps(_canonical_flow(), ensure_ascii=False) + "\n").encode("utf-8")
    )

    with pytest.raises(PromptFlowMigrationError, match="摘要"):
        rollback_session_guidance_flow(
            runtime_path,
            backup_dir=backup_dir,
            backup_name=backup_path.name,
        )

    assert runtime_path.read_bytes() == current
    assert list_session_guidance_flow_backups(backup_dir=backup_dir) == []
    assert list(backup_dir.iterdir()) == [backup_path]


def test_upgrade_and_admin_save_share_write_lock(tmp_path, monkeypatch):
    from core.prompt_v2 import flow as flow_module
    from core.prompt_v2 import flow_migrations

    runtime_dir = tmp_path / "runtime"
    runtime_path = runtime_dir / "chat" / "flow.json"
    backup_dir = tmp_path / "backups"
    _write_flow(runtime_path, _old_flow(), compact=True)
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))

    backup_ready = threading.Event()
    release_upgrade = threading.Event()
    save_started = threading.Event()
    errors: list[BaseException] = []
    real_create_backup = flow_migrations._create_exact_backup

    def paused_create_backup(*args, **kwargs):
        backup_path = real_create_backup(*args, **kwargs)
        backup_ready.set()
        if not release_upgrade.wait(timeout=5):
            raise TimeoutError("等待并发保存超时")
        return backup_path

    monkeypatch.setattr(
        flow_migrations,
        "_create_exact_backup",
        paused_create_backup,
    )

    def run_upgrade():
        try:
            flow_migrations.upgrade_runtime_flow_file(
                runtime_path,
                backup_dir=backup_dir,
            )
        except BaseException as exc:  # pragma: no cover - 仅用于跨线程传递
            errors.append(exc)

    concurrent_flow = _canonical_flow()
    concurrent_node = next(
        node
        for node in concurrent_flow["nodes"]
        if node["id"] == "session_guidance"
    )
    concurrent_node["concurrent_note"] = "admin-save-must-win"

    def run_save():
        save_started.set()
        try:
            flow_module.save_flow(concurrent_flow)
        except BaseException as exc:  # pragma: no cover - 仅用于跨线程传递
            errors.append(exc)

    upgrade_thread = threading.Thread(target=run_upgrade)
    save_thread = threading.Thread(target=run_save)
    upgrade_thread.start()
    assert backup_ready.wait(timeout=5)
    save_thread.start()
    assert save_started.wait(timeout=5)
    time.sleep(0.05)
    assert save_thread.is_alive()
    release_upgrade.set()
    upgrade_thread.join(timeout=5)
    save_thread.join(timeout=5)

    assert not upgrade_thread.is_alive()
    assert not save_thread.is_alive()
    assert errors == []
    final_flow = json.loads(runtime_path.read_text(encoding="utf-8"))
    final_node = next(
        node
        for node in final_flow["nodes"]
        if node["id"] == "session_guidance"
    )
    assert final_node["concurrent_note"] == "admin-save-must-win"


def test_manage_prompt_flow_help_and_check_are_read_only(tmp_path):
    runtime_dir = tmp_path / "runtime"
    runtime_path = runtime_dir / "chat" / "flow.json"
    original = _write_flow(runtime_path, _old_flow(), compact=True)
    env = dict(os.environ)
    env["NANOBOT_PROMPT_RUNTIME_DIR"] = str(runtime_dir)

    help_result = subprocess.run(
        [sys.executable, "scripts/manage_prompt_flow.py", "--help"],
        cwd=Path.cwd(),
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )
    check_result = subprocess.run(
        [sys.executable, "scripts/manage_prompt_flow.py", "check-session-guidance"],
        cwd=Path.cwd(),
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )

    assert help_result.returncode == 0, help_result.stderr
    assert "check-session-guidance" in help_result.stdout
    assert "list-session-guidance-backups" in help_result.stdout
    assert "rollback-session-guidance" in help_result.stdout
    assert "plan" in help_result.stdout
    assert "apply" in help_result.stdout
    assert check_result.returncode == 0, check_result.stderr
    assert json.loads(check_result.stdout)["needs_migration"] is True
    assert runtime_path.read_bytes() == original


def test_prompt_runtime_init_does_not_rewrite_existing_legacy_flow(
    tmp_path,
    monkeypatch,
):
    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    default_flow_path = default_dir / "chat" / "flow.json"
    shutil.copytree(
        Path("prompts.v2.default/chat"),
        default_dir / "chat",
        dirs_exist_ok=True,
    )
    default_flow_path.write_text(
        Path("prompts.v2.default/chat/flow.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    shutil.copytree(
        Path("prompts.v2.default/tasks"),
        default_dir / "tasks",
    )
    shutil.copytree(
        Path("prompts.v2.default/tools"),
        default_dir / "tools",
    )
    runtime_path = runtime_dir / "chat" / "flow.json"
    legacy = _old_flow()
    legacy["version"] = 1
    private_edge = next(
        edge
        for edge in legacy["edges"]
        if (edge.get("from"), edge.get("to"))
        == ("base_contract", "private_policy")
    )
    private_edge["platforms"] = ["web"]
    original = _write_flow(runtime_path, legacy, compact=True)
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))

    result = init_prompt_v2_runtime_dir()

    assert result["flow_migrated"] is False
    assert result["flow_backup_path"] == ""
    assert runtime_path.read_bytes() == original
    assert not (tmp_path / "prompt_template_backups").exists()


def test_manage_prompt_flow_requires_plan_before_apply(tmp_path):
    runtime_dir = tmp_path / "runtime"
    runtime_path = runtime_dir / "chat" / "flow.json"
    original = _write_flow(runtime_path, _flow_v1(), compact=True)
    env = dict(os.environ)
    env["NANOBOT_PROMPT_RUNTIME_DIR"] = str(runtime_dir)

    missing_plan = subprocess.run(
        [sys.executable, "scripts/manage_prompt_flow.py", "apply"],
        cwd=Path.cwd(),
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )
    assert missing_plan.returncode != 0
    assert runtime_path.read_bytes() == original

    plan_result = subprocess.run(
        [sys.executable, "scripts/manage_prompt_flow.py", "plan"],
        cwd=Path.cwd(),
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )
    assert plan_result.returncode == 0, plan_result.stderr
    plan = json.loads(plan_result.stdout)
    assert len(plan["plan_id"]) == 64
    assert plan["changed"] is True
    assert runtime_path.read_bytes() == original
    assert "nodes" not in plan
    assert "edges" not in plan

    apply_result = subprocess.run(
        [
            sys.executable,
            "scripts/manage_prompt_flow.py",
            "apply",
            "--plan-id",
            plan["plan_id"],
        ],
        cwd=Path.cwd(),
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )
    assert apply_result.returncode == 0, apply_result.stderr
    applied = json.loads(apply_result.stdout)
    assert applied["applied"] is True
    assert json.loads(runtime_path.read_text(encoding="utf-8"))["version"] == 2


def test_manage_prompt_flow_check_rejects_symlinked_runtime_parent(tmp_path):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    escaped_chat = tmp_path / "escaped-chat"
    escaped_chat.mkdir()
    _write_flow(escaped_chat / "flow.json", _old_flow(), compact=True)
    (runtime_dir / "chat").symlink_to(
        escaped_chat,
        target_is_directory=True,
    )
    env = dict(os.environ)
    env["NANOBOT_PROMPT_RUNTIME_DIR"] = str(runtime_dir)

    result = subprocess.run(
        [sys.executable, "scripts/manage_prompt_flow.py", "check-session-guidance"],
        cwd=Path.cwd(),
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 1
    assert "符号链接" in result.stderr
