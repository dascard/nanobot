"""Agent Skills 规范、治理生命周期与生产请求接入测试。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.tool_services.skill import execute_skill
from core.agent_runtime import (
    AgentTurnRequest,
    RequestRuntimeContext,
    RuntimeActor,
    RuntimeActorType,
    RuntimeAttribute,
    RuntimeChatType,
    RuntimeOwnerType,
    RuntimePlanKind,
    RuntimePrincipal,
)
from core.agent_runtime.request_scope import runtime_context_scope
from core.db.models.skill import SkillLifecycleEventRow
from core.skills import (
    BundledSkillCatalog,
    SkillBundleFile,
    SkillContractError,
    SkillLifecycleError,
    SkillLifecycleService,
    SkillResolutionContext,
    SkillScopeTarget,
    SkillVersionConflictError,
    SqlAlchemySkillProvider,
    parse_skill_bundle,
    runtime_skill_targets,
)
from core.tool_plan import build_tool_plan
from nanobot_kt.skill_runtime import build_skill_bridge_binding
from tests.async_helpers import run_async


def _skill_md(
    name: str,
    version: str,
    *,
    description: str = "测试 Skill 描述",
    allowed_tools: str = "",
    dependencies: str = "",
    permissions: str = "",
    license_text: str = "",
    compatibility: str = "",
    body: str = "# 测试指导\n\n按当前用户要求完成任务。",
) -> bytes:
    allowed_line = f"allowed-tools: {allowed_tools}\n" if allowed_tools else ""
    license_line = f"license: {license_text}\n" if license_text else ""
    compatibility_line = (
        f"compatibility: {compatibility}\n" if compatibility else ""
    )
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"{license_line}"
        f"{compatibility_line}"
        "metadata:\n"
        f'  version: "{version}"\n'
        f'  nanobot.dependencies: "{dependencies}"\n'
        f'  nanobot.permissions: "{permissions}"\n'
        f"{allowed_line}"
        "---\n\n"
        f"{body}\n"
    ).encode("utf-8")


def _bundle(
    name: str,
    version: str,
    **kwargs,
):
    files = kwargs.pop("files", ())
    return parse_skill_bundle(
        _skill_md(name, version, **kwargs),
        files=tuple(files),
    )


def _install(
    db_session,
    target: SkillScopeTarget,
    name: str,
    version: str,
    *,
    expected_generation: int | None = None,
    pin: bool = True,
    **kwargs,
):
    return SkillLifecycleService(db_session).install(
        target,
        _bundle(name, version, **kwargs),
        actor_id="tester",
        trusted_source=True,
        pin=pin,
        expected_generation=expected_generation,
    )


def test_skill_bundle_strictly_validates_agent_skills_extensions():
    bundle = _bundle(
        "research-guide",
        "1.2.3",
        allowed_tools="web_search sql_analysis",
        dependencies="base-guide@1.0.0",
        permissions="network:search,tool:web_search",
        files=(SkillBundleFile("references/guide.md", b"# reference"),),
    )

    assert bundle.name == "research-guide"
    assert bundle.version == "1.2.3"
    assert bundle.allowed_tools == ("sql_analysis", "web_search")
    assert bundle.dependencies == ("base-guide@1.0.0",)
    assert bundle.required_permissions == ("network:search", "tool:web_search")
    assert bundle.resource_paths == ("references/guide.md",)
    assert len(bundle.bundle_sha256) == 64
    standard_only = parse_skill_bundle(
        b"---\nname: standard-only\ndescription: standard skill\n---\nbody"
    )
    assert standard_only.version == "0.0.0"

    with pytest.raises(SkillContractError, match="重复字段"):
        parse_skill_bundle(
            b"---\nname: duplicate\nname: duplicate\ndescription: x\n"
            b"metadata:\n  version: '1.0.0'\n---\nbody"
        )
    with pytest.raises(SkillContractError, match="未知顶层字段"):
        parse_skill_bundle(
            b"---\nname: unknown-field\ndescription: x\nversion: 1\n"
            b"metadata:\n  version: '1.0.0'\n---\nbody"
        )
    with pytest.raises(SkillContractError, match="name 必须"):
        parse_skill_bundle(
            _skill_md("wrong-name", "1.0.0"),
            expected_name="expected-name",
        )
    with pytest.raises(SkillContractError, match="metadata 的值"):
        parse_skill_bundle(
            b"---\nname: bad-metadata\ndescription: x\n"
            b"metadata:\n  version: 1\n---\nbody"
        )
    with pytest.raises(SkillContractError, match="POSIX 相对路径|不能越界"):
        SkillBundleFile("../secret", b"secret")
    with pytest.raises(SkillContractError, match="规范 POSIX"):
        SkillBundleFile("references//guide.md", b"secret")
    with pytest.raises(SkillContractError, match="SemVer"):
        _bundle("bad-version", "1.0.0-01")


def test_bundled_catalog_is_fixed_bounded_and_fault_isolated(tmp_path: Path):
    root = tmp_path.resolve() / "skills"
    valid = root / "valid-guide"
    invalid = root / "invalid-guide"
    valid.mkdir(parents=True)
    invalid.mkdir()
    (valid / "SKILL.md").write_bytes(_skill_md("valid-guide", "1.0.0"))
    (invalid / "SKILL.md").write_text("not frontmatter", encoding="utf-8")
    (valid / "reference.md").write_text("资料", encoding="utf-8")

    catalog = BundledSkillCatalog(root)

    assert [item.entry.name for item in catalog.records()] == ["valid-guide"]
    assert catalog.records()[0].bundle.resource_paths == ("reference.md",)
    assert catalog.diagnostics == (
        "bundled_invalid:invalid-guide:SkillContractError",
    )
    with pytest.raises(SkillContractError, match="绝对路径"):
        BundledSkillCatalog(Path("relative/skills"))


def test_bundled_catalog_rejects_symlink_resources_without_blocking_peers(
    tmp_path: Path,
):
    root = tmp_path.resolve() / "skills"
    linked = root / "linked-guide"
    valid = root / "valid-guide"
    linked.mkdir(parents=True)
    valid.mkdir()
    (linked / "SKILL.md").write_bytes(_skill_md("linked-guide", "1.0.0"))
    (valid / "SKILL.md").write_bytes(_skill_md("valid-guide", "1.0.0"))
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (linked / "outside.txt").symlink_to(outside)

    catalog = BundledSkillCatalog(root)

    assert [item.entry.name for item in catalog.records()] == ["valid-guide"]
    assert catalog.diagnostics == (
        "bundled_invalid:linked-guide:SkillContractError",
    )


def test_bundled_catalog_rejects_oversized_resource_before_loading_peer(
    tmp_path: Path,
):
    from core.skills.contracts import SKILL_FILE_MAX_BYTES

    root = tmp_path.resolve() / "skills"
    oversized = root / "oversized-guide"
    valid = root / "valid-guide"
    oversized.mkdir(parents=True)
    valid.mkdir()
    (oversized / "SKILL.md").write_bytes(
        _skill_md("oversized-guide", "1.0.0")
    )
    with (oversized / "large.bin").open("wb") as handle:
        handle.truncate(SKILL_FILE_MAX_BYTES + 1)
    (valid / "SKILL.md").write_bytes(_skill_md("valid-guide", "1.0.0"))

    catalog = BundledSkillCatalog(root)

    assert [item.entry.name for item in catalog.records()] == ["valid-guide"]
    assert catalog.diagnostics == (
        "bundled_invalid:oversized-guide:SkillContractError",
    )


def test_skill_lifecycle_is_explicit_reversible_and_optimistic(db_session):
    target = SkillScopeTarget("user", "qq:user:u1")
    service = SkillLifecycleService(db_session)
    first = _install(db_session, target, "writer-guide", "1.0.0")
    second = _install(
        db_session,
        target,
        "writer-guide",
        "2.0.0",
        expected_generation=first.generation,
    )
    repeated_second = _install(
        db_session,
        target,
        "writer-guide",
        "2.0.0",
        expected_generation=first.generation,
    )

    assert second.active_version == "1.0.0"
    assert second.generation == 2
    assert repeated_second == second
    unpinned = service.set_pinned(
        target,
        "writer-guide",
        pinned=False,
        expected_generation=second.generation,
        actor_id="tester",
    )
    upgraded = service.upgrade(
        target,
        "writer-guide",
        "2.0.0",
        expected_generation=unpinned.generation,
        actor_id="tester",
    )
    assert upgraded.active_version == "2.0.0"
    assert upgraded.previous_version == "1.0.0"
    rolled_back = service.rollback(
        target,
        "writer-guide",
        expected_generation=upgraded.generation,
        actor_id="tester",
    )
    assert rolled_back.active_version == "1.0.0"
    assert rolled_back.previous_version == "2.0.0"
    uninstalled = service.uninstall(
        target,
        "writer-guide",
        expected_generation=rolled_back.generation,
        actor_id="tester",
    )
    assert uninstalled.status == "uninstalled"
    assert uninstalled.active_package_id == ""
    reinstalled = service.install(
        target,
        _bundle("writer-guide", "2.0.0"),
        actor_id="tester",
        trusted_source=True,
        expected_generation=uninstalled.generation,
    )
    db_session.commit()

    assert reinstalled.status == "active"
    assert reinstalled.active_version == "2.0.0"
    assert reinstalled.pinned is True
    assert [
        row.event_kind
        for row in db_session.query(SkillLifecycleEventRow)
        .filter_by(binding_id=first.binding_id)
        .order_by(SkillLifecycleEventRow.generation)
    ] == [
        "installed",
        "version_added",
        "unpinned",
        "upgraded",
        "rolled_back",
        "uninstalled",
        "reinstalled",
    ]


def test_skill_binding_rejects_cross_scope_package_projection(db_session):
    first_target = SkillScopeTarget("user", "qq:user:u1")
    second_target = SkillScopeTarget("user", "qq:user:u2")
    first = _install(db_session, first_target, "safe-guide", "1.0.0")
    second = _install(db_session, second_target, "safe-guide", "1.0.0")
    db_session.flush()
    db_session.execute(
        text(
            "UPDATE skill_bindings SET active_package_id=:package_id "
            "WHERE binding_id=:binding_id"
        ),
        {
            "package_id": second.active_package_id,
            "binding_id": first.binding_id,
        },
    )
    db_session.expire_all()

    with pytest.raises(SkillLifecycleError, match="投影不一致"):
        SkillLifecycleService(db_session).list_bindings(target=first_target)


def test_skill_lifecycle_rejects_untrusted_drift_pin_upgrade_and_stale_cas(
    db_session,
):
    target = SkillScopeTarget("agent", "nanobot")
    service = SkillLifecycleService(db_session)
    with pytest.raises(SkillLifecycleError, match="未由管理员标记"):
        service.install(
            target,
            _bundle("safe-guide", "1.0.0"),
            actor_id="tester",
            trusted_source=False,
        )
    first = _install(db_session, target, "safe-guide", "1.0.0")
    with pytest.raises(SkillVersionConflictError, match="正文不可变"):
        service.install(
            target,
            _bundle("safe-guide", "1.0.0", body="# changed"),
            actor_id="tester",
            trusted_source=True,
            expected_generation=first.generation,
        )
    second = _install(
        db_session,
        target,
        "safe-guide",
        "2.0.0",
        expected_generation=first.generation,
    )
    with pytest.raises(SkillLifecycleError, match="先显式解除 pin"):
        service.upgrade(
            target,
            "safe-guide",
            "2.0.0",
            expected_generation=second.generation,
            actor_id="tester",
        )
    with pytest.raises(SkillVersionConflictError, match="generation 已变化"):
        service.set_pinned(
            target,
            "safe-guide",
            pinned=False,
            expected_generation=1,
            actor_id="tester",
        )


def test_skill_scope_precedence_permissions_dependencies_and_group_privacy(
    db_session,
    tmp_path: Path,
):
    root = tmp_path.resolve() / "skills"
    builtin = root / "shared-guide"
    builtin.mkdir(parents=True)
    (builtin / "SKILL.md").write_bytes(_skill_md("shared-guide", "1.0.0"))
    catalog = BundledSkillCatalog(root)
    agent = SkillScopeTarget("agent", "nanobot")
    user = SkillScopeTarget("user", "qq:user:u1")
    project = SkillScopeTarget("project", "project:p1")
    _install(db_session, agent, "shared-guide", "2.0.0")
    _install(db_session, user, "shared-guide", "3.0.0")
    _install(db_session, project, "shared-guide", "4.0.0")
    _install(
        db_session,
        project,
        "missing-tool",
        "1.0.0",
        allowed_tools="not_available",
    )
    _install(
        db_session,
        project,
        "depends-on-missing",
        "1.0.0",
        dependencies="absent-guide@1.0.0",
    )
    _install(
        db_session,
        project,
        "cycle-a",
        "1.0.0",
        dependencies="cycle-b@1.0.0",
    )
    _install(
        db_session,
        project,
        "cycle-b",
        "1.0.0",
        dependencies="cycle-a@1.0.0",
    )
    db_session.commit()
    provider = SqlAlchemySkillProvider(db_session, bundled_catalog=catalog)
    lock = provider.resolve_lock(
        SkillResolutionContext(
            targets=(SkillScopeTarget("builtin", "builtin"), agent, user, project),
            executable_tool_names=frozenset({"reply"}),
        )
    )

    assert [(entry.name, entry.scope.value, entry.version) for entry in lock.entries] == [
        ("shared-guide", "project", "4.0.0")
    ]
    assert "permission_denied:missing-tool" in lock.diagnostics
    assert "dependency_missing:depends-on-missing" in lock.diagnostics
    assert "dependency_cycle:cycle-a" in lock.diagnostics
    assert "dependency_cycle:cycle-b" in lock.diagnostics
    assert runtime_skill_targets(
        platform="qq",
        is_group=True,
        owner_id="group-1",
        agent_id="nanobot",
    ) == (
        SkillScopeTarget("builtin", "builtin"),
        SkillScopeTarget("agent", "nanobot"),
    )


def test_exact_skill_lock_survives_active_version_switch(db_session):
    target = SkillScopeTarget("user", "qq:user:u1")
    first = _install(db_session, target, "locked-guide", "1.0.0")
    provider = SqlAlchemySkillProvider(db_session, bundled_catalog=BundledSkillCatalog(
        Path(__file__).resolve().parent / "missing-skills"
    ))
    context = SkillResolutionContext(
        targets=(target,),
        executable_tool_names=frozenset(),
    )
    old_lock = provider.resolve_lock(context)
    second = _install(
        db_session,
        target,
        "locked-guide",
        "2.0.0",
        expected_generation=first.generation,
    )
    unpinned = SkillLifecycleService(db_session).set_pinned(
        target,
        "locked-guide",
        pinned=False,
        expected_generation=second.generation,
        actor_id="tester",
    )
    SkillLifecycleService(db_session).upgrade(
        target,
        "locked-guide",
        "2.0.0",
        expected_generation=unpinned.generation,
        actor_id="tester",
    )
    db_session.commit()

    loaded = provider.load_locked(old_lock.entries[0], visible_targets=(target,))
    new_lock = provider.resolve_lock(context)
    assert "测试指导" in loaded.body
    assert old_lock.entries[0].version == "1.0.0"
    assert new_lock.entries[0].version == "2.0.0"
    assert old_lock.sha256 != new_lock.sha256


def test_skill_lock_and_port_snapshot_preserve_standard_metadata(db_session):
    target = SkillScopeTarget("user", "qq:user:u1")
    _install(
        db_session,
        target,
        "metadata-guide",
        "1.0.0",
        license_text="Apache-2.0",
        compatibility="需要 Python 3.12",
    )
    db_session.commit()
    provider = SqlAlchemySkillProvider(
        db_session,
        bundled_catalog=BundledSkillCatalog(
            Path(__file__).resolve().parent / "missing-skills"
        ),
    )
    lock = provider.resolve_lock(
        SkillResolutionContext(
            targets=(target,),
            executable_tool_names=frozenset(),
        )
    )

    restored = type(lock).from_runtime_json(lock.to_runtime_json())
    snapshot = run_async(
        provider.snapshot(
            owner=RuntimePrincipal("qq", RuntimeOwnerType.USER, "u1")
        )
    )

    assert restored == lock
    assert restored.entries[0].license_text == "Apache-2.0"
    assert restored.entries[0].compatibility == "需要 Python 3.12"
    assert snapshot.skills[0].license_text == "Apache-2.0"
    assert snapshot.skills[0].compatibility == "需要 Python 3.12"


def test_kt_adapter_disables_upstream_skill_discovery(monkeypatch):
    import kohakuterrarium.bootstrap.agent_init as kt_agent_init
    from kohakuterrarium.core.agent import Agent as KtAgent
    from nanobot_kt.optional_agent_api import resolve_kt_agent_api

    def forbidden_discovery(**_kwargs):
        raise AssertionError("KT 不应扫描 cwd 或 HOME Skill")

    monkeypatch.setattr(kt_agent_init, "discover_skills", forbidden_discovery)
    agent_type, _loader = resolve_kt_agent_api(None, None)
    repeated_type, _repeated_loader = resolve_kt_agent_api(None, None)
    agent = agent_type.__new__(agent_type)
    agent.session = type("Session", (), {"extra": {"skills_registry": object()}})()

    agent._init_skills()

    assert agent_type is repeated_type
    assert agent_type is not KtAgent
    assert issubclass(agent_type, KtAgent)
    assert agent.skills is None
    assert agent.skill_path_scanner is None
    assert "skills_registry" not in agent.session.extra


def test_skill_bridge_exposes_bounded_catalog_enum_and_plan_mode_isolation(
    db_session,
):
    base_plan = build_tool_plan(
        chat_type="private",
        user_id="u1",
        platform="qq",
        session_id="private-u1",
        db=db_session,
    )
    binding = build_skill_bridge_binding(
        db=db_session,
        tool_plan=base_plan,
        project_context="existing project context",
        platform="qq",
        runtime_chat_type="private",
        is_group=False,
        owner_id="u1",
        agent_id="nanobot",
        session_id="private-u1",
    )

    assert binding.lock is not None
    assert binding.plan_ref is not None
    assert binding.plan_ref.kind is RuntimePlanKind.SKILL
    assert binding.tool_plan.can_execute("skill")
    skill_schema = next(
        item
        for item in binding.tool_plan.sent_tool_schemas
        if item["function"]["name"] == "skill"
    )
    assert skill_schema["function"]["parameters"]["properties"]["name"]["enum"] == [
        "ai-daily",
        "schedule-task",
        "sql-analysis",
    ]
    assert '<skill_catalog trust="untrusted_routing_metadata">' in binding.project_context
    assert "# AI 日报与资讯聚合" not in binding.project_context
    assert all("instructions" not in str(item.value) for item in binding.runtime_attributes)

    plan_mode = build_skill_bridge_binding(
        db=db_session,
        tool_plan=base_plan,
        project_context="",
        platform="qq",
        runtime_chat_type="private",
        is_group=False,
        owner_id="u1",
        agent_id="nanobot",
        session_id="private-u1",
        session_goal_mode="plan",
    )
    assert plan_mode.lock is None
    assert not plan_mode.tool_plan.can_execute("skill")
    assert plan_mode.runtime_attributes == ()

    source_disabled_plan = build_tool_plan(
        chat_type="private",
        user_id="u1",
        platform="qq",
        session_id="private-u1",
        db=db_session,
        extra_disabled={"skill": "来源上下文禁用(防递归)"},
    )
    source_disabled = build_skill_bridge_binding(
        db=db_session,
        tool_plan=source_disabled_plan,
        project_context="",
        platform="qq",
        runtime_chat_type="private",
        is_group=False,
        owner_id="u1",
        agent_id="nanobot",
        session_id="private-u1",
    )
    assert source_disabled.lock is None
    assert source_disabled.tool_plan.disabled["skill"] == "来源上下文禁用(防递归)"


def test_skill_tool_loads_only_locked_body_and_requested_utf8_resource(db_session):
    db_session.execute(text("PRAGMA foreign_keys = ON"))
    target = SkillScopeTarget("user", "qq:user:u1")
    _install(
        db_session,
        target,
        "managed-guide",
        "1.0.0",
        files=(
            SkillBundleFile("references/info.md", "可信资料".encode("utf-8")),
            SkillBundleFile(
                "assets/image.bin",
                b"\x00\x01",
                "application/octet-stream",
            ),
        ),
    )
    db_session.commit()
    base_plan = build_tool_plan(
        chat_type="private",
        user_id="u1",
        platform="qq",
        session_id="private-u1",
        db=db_session,
    )
    binding = build_skill_bridge_binding(
        db=db_session,
        tool_plan=base_plan,
        project_context="",
        platform="qq",
        runtime_chat_type="private",
        is_group=False,
        owner_id="u1",
        agent_id="nanobot",
        session_id="private-u1",
    )
    context = {
        "platform": "qq",
        "is_group": False,
        "owner_id": "u1",
        "session_id": "private-u1",
        **{item.key: item.value for item in binding.runtime_attributes},
    }

    with runtime_context_scope(context):
        body_result = run_async(execute_skill({"name": "managed-guide"}))
        resource_result = run_async(execute_skill({
            "name": "managed-guide",
            "resource": "references/info.md",
        }))
        binary_result = run_async(execute_skill({
            "name": "managed-guide",
            "resource": "assets/image.bin",
        }))
        unknown_result = run_async(execute_skill({"name": "unknown-guide"}))
        injected_result = run_async(execute_skill({
            "name": "managed-guide",
            "owner_id": "other",
        }))

    body = json.loads(body_result.output)
    resource = json.loads(resource_result.output)
    assert body["_nanobot_skill"]["version"] == "1.0.0"
    assert body["_nanobot_skill"]["trust"] == "authorized_skill_instructions"
    assert body["resources"] == ["assets/image.bin", "references/info.md"]
    assert resource["text"] == "可信资料"
    assert resource["_nanobot_skill_resource"]["trust"] == (
        "authorized_skill_resource_data"
    )
    assert "二进制资源" in binary_result.error
    assert "授权目录" in unknown_result.error
    assert "只接受 name" in injected_result.error


def test_native_and_kt_runtime_adapters_forward_only_present_skill_lock_fields():
    from core.agent_runtime.native import _runtime_context_payload
    from nanobot_kt.runtime_adapter import KtRuntimeAdapter

    context = RequestRuntimeContext(
        request_id="request-skill",
        principal=RuntimePrincipal("qq", RuntimeOwnerType.USER, "u1"),
        session_id="private-u1",
        chat_type=RuntimeChatType.PRIVATE,
        trace_id="trace-skill",
        run_id="run-skill",
        actor=RuntimeActor(RuntimeActorType.USER, "u1"),
    )
    attributes = (
        RuntimeAttribute("skill_lock_json", '{"schema_version":1}'),
        RuntimeAttribute("skill_lock_sha256", "a" * 64),
        RuntimeAttribute("skill_scope_targets_json", "[]"),
        RuntimeAttribute("skill_agent_id", "nanobot"),
        RuntimeAttribute("skill_project_id", ""),
    )
    request = AgentTurnRequest(context, "测试", event_attributes=attributes)

    native = _runtime_context_payload(request)
    kt = KtRuntimeAdapter._request_context(object(), request)
    assert native["skill_lock_sha256"] == "a" * 64
    assert kt["skill_agent_id"] == "nanobot"
    no_lock_request = AgentTurnRequest(context, "测试")
    assert "skill_lock_json" not in _runtime_context_payload(no_lock_request)
    assert "skill_lock_json" not in KtRuntimeAdapter._request_context(
        object(),
        no_lock_request,
    )


def test_skill_admin_api_has_literal_upload_and_full_reversible_lifecycle(
    client,
    monkeypatch,
):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "skill-token")
    headers = {"Authorization": "Bearer skill-token"}
    first_payload = {
        "scope": "user",
        "scope_key": "qq:user:api-user",
        "skill_md": _skill_md("api-guide", "1.0.0").decode("utf-8"),
        "resources": [
            {
                "path": "references/info.md",
                "content_base64": base64.b64encode("资料".encode()).decode(),
                "media_type": "text/markdown",
            }
        ],
        "source_label": "admin-test",
        "trusted_source": True,
        "pin": True,
    }
    assert client.post("/api/v1/admin/skills/install", json=first_payload).status_code == 401
    first = client.post(
        "/api/v1/admin/skills/install",
        json=first_payload,
        headers=headers,
    )
    assert first.status_code == 200
    assert first.json()["active_version"] == "1.0.0"
    generation = first.json()["generation"]

    second_payload = {
        **first_payload,
        "skill_md": _skill_md("api-guide", "2.0.0").decode("utf-8"),
        "resources": [],
        "expected_generation": generation,
    }
    second = client.post(
        "/api/v1/admin/skills/install",
        json=second_payload,
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["active_version"] == "1.0.0"
    generation = second.json()["generation"]
    listed = client.get(
        "/api/v1/admin/skills",
        params={"scope": "user", "scope_key": "qq:user:api-user"},
        headers=headers,
    )
    assert listed.status_code == 200
    assert [item["version"] for item in listed.json()["versions"]] == [
        "1.0.0",
        "2.0.0",
    ]

    unpinned = client.post(
        "/api/v1/admin/skills/pin",
        json={
            "scope": "user",
            "scope_key": "qq:user:api-user",
            "skill_name": "api-guide",
            "expected_generation": generation,
            "pinned": False,
        },
        headers=headers,
    )
    assert unpinned.status_code == 200
    upgraded = client.post(
        "/api/v1/admin/skills/upgrade",
        json={
            "scope": "user",
            "scope_key": "qq:user:api-user",
            "skill_name": "api-guide",
            "expected_generation": unpinned.json()["generation"],
            "target_version": "2.0.0",
        },
        headers=headers,
    )
    assert upgraded.status_code == 200
    assert upgraded.json()["active_version"] == "2.0.0"
    rolled_back = client.post(
        "/api/v1/admin/skills/rollback",
        json={
            "scope": "user",
            "scope_key": "qq:user:api-user",
            "skill_name": "api-guide",
            "expected_generation": upgraded.json()["generation"],
        },
        headers=headers,
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["active_version"] == "1.0.0"
    uninstalled = client.post(
        "/api/v1/admin/skills/uninstall",
        json={
            "scope": "user",
            "scope_key": "qq:user:api-user",
            "skill_name": "api-guide",
            "expected_generation": rolled_back.json()["generation"],
        },
        headers=headers,
    )
    assert uninstalled.status_code == 200
    assert uninstalled.json()["status"] == "uninstalled"

    unsafe = client.post(
        "/api/v1/admin/skills/install",
        json={**first_payload, "installer_url": "https://example.invalid/install"},
        headers=headers,
    )
    assert unsafe.status_code == 422


def test_skill_schema_migration_creates_immutable_version_tables():
    from core.schema_migrations import (
        _AGENT_SKILLS_LIFECYCLE_V1_VERSION,
        MIGRATIONS,
        _agent_skills_lifecycle_v1,
    )

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _agent_skills_lifecycle_v1(connection, engine, None)
        connection.execute(text(
            "INSERT INTO skill_packages("
            "package_id, scope, scope_key, skill_name, version, description, "
            "metadata_json, allowed_tools_json, dependencies_json, "
            "required_permissions_json, skill_md, skill_md_sha256, "
            "skill_md_size, bundle_sha256, bundle_size, file_count, "
            "source_kind, trusted, created_by"
            ") VALUES ("
            "'skillpkg_0123456789abcdef0123456789abcdef', 'user', "
            "'qq:user:u1', 'immutable-guide', '1.0.0', 'description', "
            "'{}', '[]', '[]', '[]', X'23', "
            "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
            "1, "
            "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
            "1, 0, 'managed', 1, 'tester')"
        ))
    assert {
        "skill_packages",
        "skill_package_files",
        "skill_bindings",
        "skill_lifecycle_events",
    } <= set(inspect(engine).get_table_names())
    assert MIGRATIONS[-1][0] == _AGENT_SKILLS_LIFECYCLE_V1_VERSION
    with pytest.raises(IntegrityError, match="skill_packages_immutable"):
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE skill_packages SET description='changed' "
                "WHERE skill_name='immutable-guide'"
            ))
