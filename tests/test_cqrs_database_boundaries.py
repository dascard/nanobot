"""CQRS-lite 数据库边界与首批垂直切片测试。"""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, replace
from datetime import datetime
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _relative_paths(paths):
    return {
        str(Path(path).relative_to(ROOT)).replace("\\", "/")
        for path in paths
    }


def test_architecture_gate_registers_cqrs_contracts_consumers_and_adapters():
    from scripts import check_architecture

    assert _relative_paths(
        check_architecture.DATABASE_PORT_CONTRACT_PATHS
    ) >= {
        "core/db/contracts.py",
        "core/db/group_memory_contracts.py",
        "core/db/settings_contracts.py",
    }
    assert _relative_paths(
        check_architecture.DATABASE_PORT_MIGRATED_PATHS
    ) >= {
        "api/admin/group_memory_routes.py",
        "api/admin/model_routes.py",
        "app/group_analysis/service.py",
        "app/group_memory/extraction_service.py",
        "app/group_memory/injection_service.py",
        "app/group_memory/retrieval_service.py",
        "core/settings_service.py",
    }
    assert _relative_paths(
        check_architecture.DATABASE_SQL_ADAPTER_PATHS
    ) >= {
        "app/group_analysis/repository.py",
        "core/db/adapter.py",
        "core/db/group_memory_adapter.py",
        "core/db/settings_adapter.py",
    }


def test_core_database_is_compatibility_facade_without_orm_models():
    source = (ROOT / "core" / "database.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    model_classes = [
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    ]

    assert model_classes == []
    assert len(source.splitlines()) < 420


def test_cqrs_contract_modules_do_not_import_sqlalchemy_or_business_modules():
    for relative_path in (
        "core/db/group_memory_contracts.py",
        "core/db/settings_contracts.py",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        assert not any(
            name == "sqlalchemy" or name.startswith("sqlalchemy.")
            for name in imported
        )
        assert not any(
            name == "api"
            or name.startswith("api.")
            or name == "app"
            or name.startswith("app.")
            or name == "core"
            or (
                name.startswith("core.")
                and not name.startswith("core.db.")
            )
            for name in imported
        )


def test_migrated_group_memory_and_setting_consumers_do_not_query_orm():
    migrated_paths = (
        "api/admin/group_memory_routes.py",
        "app/group_memory/extraction_service.py",
        "app/group_memory/injection_service.py",
        "app/group_memory/retrieval_service.py",
        "core/settings_service.py",
        "api/admin/model_routes.py",
    )
    for relative_path in migrated_paths:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(
                node.func,
                ast.Attribute,
            ):
                assert node.func.attr != "query", (
                    f"{relative_path}:{node.lineno} 仍直接调用 ORM query"
                )
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "core.database"
            ):
                imported_names = {alias.name for alias in node.names}
                assert imported_names <= {
                    "release_clean_session_transaction",
                }, (
                    f"{relative_path}:{node.lineno} 仍从兼容 God Module "
                    f"导入 {sorted(imported_names)}"
                )


def test_group_analysis_releases_read_transaction_before_model_await():
    source = (
        ROOT / "app" / "group_analysis" / "service.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    release_lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (
                isinstance(node.func, ast.Name)
                and node.func.id == "release_clean_session_transaction"
            )
            or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr
                == "release_clean_session_transaction"
            )
        )
    ]
    model_await_lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "process_payload"
    ]

    assert release_lines
    assert model_await_lines
    assert min(release_lines) < min(model_await_lines)


def _memory_record(**changes):
    from core.db.group_memory_contracts import GroupMemoryRecord

    base = GroupMemoryRecord(
        id=7,
        chat_stream_id="qq:42:group",
        group_id="group_42",
        memory_type="topic",
        content="原始内容",
        content_hash="old-hash",
        cluster_key="原始内容",
        evidence_log_ids_json="[1,2]",
        confidence=0.8,
        evidence_count=2,
        first_seen=datetime(2026, 7, 1),
        last_seen=datetime(2026, 7, 2),
        updated_at=datetime(2026, 7, 3),
        decay_score=1.0,
        status="review",
        inject_policy="auto",
        disabled_reason="",
        rejected_reason="",
        merged_into_id=None,
        last_injected_at=None,
        injected_count=0,
        source="group_analysis",
        meta_json="{}",
        created_at=datetime(2026, 7, 1),
    )
    return replace(base, **changes)


def test_group_memory_query_service_returns_immutable_dto():
    from app.group_memory.query_service import GroupMemoryQueryService

    class FakeRepository:
        def __init__(self):
            self.arguments = None

        def list_memories(self, **kwargs):
            self.arguments = kwargs
            return (_memory_record(),)

    repository = FakeRepository()
    result = GroupMemoryQueryService(repository).list_memories(
        "group_42",
        platform="qq",
        memory_type="topic",
        limit=20,
    )

    assert result.chat_stream_id == "qq:42:group"
    assert result.group_id == "group_42"
    assert result.total == 1
    assert result.memories[0].content == "原始内容"
    assert repository.arguments["chat_stream_id"] == "qq:42:group"
    assert "group_42" in repository.arguments["legacy_group_ids"]
    with pytest.raises(FrozenInstanceError):
        result.memories[0].content = "被 ORM 修改"


def test_group_memory_command_service_commits_one_atomic_update():
    from app.group_memory.command_service import GroupMemoryCommandService

    class FakeRepository:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0
            self.updated = None

        def get_memory(self, memory_id):
            assert memory_id == 7
            return _memory_record()

        def find_duplicate(self, **kwargs):
            assert kwargs["exclude_id"] == 7
            return None

        def update_memory(self, memory_id, **values):
            self.updated = dict(values)
            return _memory_record(**values)

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    repository = FakeRepository()
    result = GroupMemoryCommandService(repository).update_memory(
        7,
        content="新的稳定内容",
        status="active",
        inject_policy="manual_only",
    )

    assert result.content == "新的稳定内容"
    assert result.status == "active"
    assert result.inject_policy == "manual_only"
    assert repository.updated["content_hash"]
    assert repository.commits == 1
    assert repository.rollbacks == 0


def test_system_setting_services_use_dto_and_atomic_repository_boundary():
    from core.db.settings_contracts import SystemSettingRecord
    from core.settings_admin_service import (
        SystemSettingCommandService,
        SystemSettingQueryService,
        SystemSettingWrite,
    )

    class FakeRepository:
        def __init__(self):
            self.rows = {
                "model.reply": SystemSettingRecord(
                    key="model.reply",
                    value="old-model",
                    description="旧值",
                    updated_at=None,
                ),
            }
            self.commits = 0
            self.rollbacks = 0

        def get(self, key):
            return self.rows.get(key)

        def list_all(self):
            return tuple(self.rows.values())

        def list_by_keys(self, keys):
            return tuple(self.rows[key] for key in keys if key in self.rows)

        def upsert_many(self, writes):
            for write in writes:
                self.rows[write.key] = SystemSettingRecord(
                    key=write.key,
                    value=write.value,
                    description=write.description,
                    updated_at=None,
                )
            return tuple(self.rows[write.key] for write in writes)

        def delete_many(self, keys):
            deleted = 0
            for key in keys:
                if self.rows.pop(key, None) is not None:
                    deleted += 1
            return deleted

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    repository = FakeRepository()
    query = SystemSettingQueryService(repository)
    command = SystemSettingCommandService(repository)

    before = query.get("model.reply")
    assert before is not None
    with pytest.raises(FrozenInstanceError):
        before.value = "mutated"

    rows = command.upsert_many((
        SystemSettingWrite(
            key="model.reply",
            value="new-model",
            description="主回复模型",
        ),
        SystemSettingWrite(
            key="model.fast",
            value="fast-model",
            description="快速模型",
        ),
    ))

    assert [row.value for row in rows] == ["new-model", "fast-model"]
    assert command.delete_many(("model.fast", "missing")) == 1
    assert query.get("model.fast") is None
    assert repository.commits == 2
    assert repository.rollbacks == 0


def test_system_setting_command_rolls_back_when_repository_write_fails():
    from core.settings_admin_service import (
        SystemSettingCommandService,
        SystemSettingWrite,
    )

    class FailingRepository:
        def __init__(self):
            self.rollbacks = 0

        def upsert_many(self, writes):
            del writes
            raise RuntimeError("write failed")

        def commit(self):
            raise AssertionError("失败写入不能提交")

        def rollback(self):
            self.rollbacks += 1

    repository = FailingRepository()
    with pytest.raises(RuntimeError, match="write failed"):
        SystemSettingCommandService(repository).upsert_many((
            SystemSettingWrite(
                key="model.reply",
                value="new-model",
                description="",
            ),
        ))

    assert repository.rollbacks == 1
