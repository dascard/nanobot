from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from core.prompt_v2.flow_storage import (
    assert_no_symlink_components,
    ensure_directory_without_symlinks,
    read_regular_bytes,
)


@dataclass(frozen=True)
class TemplateRecord:
    template_key: str
    category: str
    kind: str
    tool_name: str = ""
    display_name: str = ""
    editable: bool = True
    runtime_effective: bool = True
    runtime_status: str = "runtime_template"
    owner_module: str = ""
    domain: str = ""
    source_precedence: tuple[str, ...] = ("runtime", "default")
    failure_policy: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source_precedence"] = list(self.source_precedence)
        return data


_LEGACY_ALIASES = MappingProxyType({
    "chat_main": "chat/main",
    "chat_branch_group": "chat/branch_group",
    "chat_branch_private": "chat/branch_private",
    "identity_context": "chat/identity_context",
    "reply": "tools/reply/usage",
    "no_reply": "tools/no_reply/usage",
    "sql_analysis": "tools/sql_analysis/usage",
    "python_sandbox": "tools/python_sandbox/usage",
    "group_analysis": "tools/group_analysis/usage",
    "group_analysis_system": "tools/group_analysis/system",
    "group_analysis_topics": "tools/group_analysis/topics",
    "group_analysis_titles": "tools/group_analysis/titles",
    "group_analysis_quotes": "tools/group_analysis/quotes",
    "group_analysis_quality": "tools/group_analysis/quality",
    "image_summary": "tools/image_summary/usage",
    "image_summary_system": "tools/image_summary/system",
    "image_summary_user": "tools/image_summary/user",
    "image_generation": "tools/image_generation/usage",
    "news_digest_system": "tools/ai_daily/digest_system",
    "news_digest_user": "tools/ai_daily/digest_user",
    "ai_daily": "tools/ai_daily/usage",
    "ai_daily_quality_system": "tools/ai_daily/quality_system",
    "ai_daily_quality_user": "tools/ai_daily/quality_user",
    "memory_query": "tools/memory_query/usage",
    "knowledge_query": "tools/knowledge_query/usage",
    "web_search": "tools/web_search/usage",
    "persona_update": "tools/persona_update/usage",
    "schedule_task": "tools/schedule_task/usage",
    "sticker_search": "tools/sticker_search/usage",
    "sandbox_exec": "tools/sandbox_exec/usage",
    "workspace_list": "tools/workspace_list/usage",
    "workspace_read": "tools/workspace_read/usage",
    "workspace_search": "tools/workspace_search/usage",
    "workspace_write": "tools/workspace_write/usage",
    "asset_import": "tools/asset_import/usage",
    "asset_publish": "tools/asset_publish/usage",
    "classifier_legacy": "tasks/classifier_legacy",
    "private_decision": "tasks/private_decision",
    "timing_gate": "tasks/timing_gate",
    "timing_proactive": "tasks/timing_proactive",
    "memory_extract": "tasks/memory_extract",
    "persona_candidate_system": "tasks/persona_candidate_system",
    "model_scout_system": "tasks/model_scout_system",
    "memory_digest_system": "tasks/memory_digest_system",
    "memory_digest_user": "tasks/memory_digest_user",
    "session_summary_system": "tasks/session_summary_system",
    "session_summary_output": "tasks/session_summary_output",
    "reply_contract_retry": "tasks/reply_contract_retry",
    "outreach_extract": "tasks/outreach_extract",
    "outreach_judge": "tasks/outreach_judge",
    "outreach_generate": "tasks/outreach_generate",
    "proactive_research": "tasks/proactive_research",
})

_TASK_TOOL_NAMES = MappingProxyType({
    "tasks/classifier_legacy": "classifier_legacy",
    "tasks/private_decision": "private_decision",
    "tasks/timing_gate": "timing_gate",
    "tasks/timing_proactive": "timing_proactive",
    "tasks/memory_extract": "memory_extract",
    "tasks/persona_candidate_system": "persona_candidate_system",
    "tasks/model_scout_system": "model_scout",
    "tasks/memory_digest_system": "memory_digest",
    "tasks/memory_digest_user": "memory_digest",
    "tasks/session_summary_system": "session_summary",
    "tasks/session_summary_output": "session_summary",
    "tasks/reply_contract_retry": "reply",
    "tasks/outreach_extract": "outreach_extract",
    "tasks/outreach_judge": "outreach_judge",
    "tasks/outreach_generate": "outreach_generate",
    "tasks/proactive_research": "web_search",
})

def tool_template_keys(tool_name: str) -> tuple[str, ...]:
    """从类型化 ToolDescriptor 获取唯一 Prompt 模板绑定。"""

    from core.tool_registry import get_tool_descriptor

    descriptor = get_tool_descriptor(tool_name)
    return descriptor.prompt_template_keys if descriptor is not None else ()

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_template_dir() -> Path:
    return Path(
        os.environ.get("NANOBOT_PROMPT_DEFAULT_DIR")
        or os.environ.get("NANOBOT_PROMPT_V2_DIR")
        or os.environ.get("NANOBOT_PROMPT_V2_DEFAULT_DIR")
        or (_repo_root() / "prompts.v2.default")
    )


def runtime_template_dir() -> Path:
    return Path(
        os.environ.get("NANOBOT_PROMPT_RUNTIME_DIR")
        or os.environ.get("NANOBOT_PROMPT_V2_RUNTIME_DIR")
        or (_repo_root() / "data" / "prompts_v2")
    )


def _active_runtime_template_keys(
) -> set[str]:
    """返回当前 live flow 与已登记任务实际可能读取的模板 key。"""
    from core.prompt_v2.flow import load_flow, ordered_nodes_for_chat
    from core.prompt_v2.flow_contract import LIVE_PROMPT_BRANCHES
    from core.prompt_v2.task_contracts import (
        get_task_contract,
        list_task_contract_keys,
    )
    from core.tool_registry import (
        list_user_tool_descriptors,
        validate_tool_descriptor_registry,
    )

    validate_tool_descriptor_registry()

    active_keys = {"chat/flow"}
    active_flow = load_flow()
    for platform, chat_type in LIVE_PROMPT_BRANCHES:
        for node in ordered_nodes_for_chat(
            active_flow.flow,
            chat_type,
            platform=platform,
        ):
            if str(node.get("type") or "") != "template":
                continue
            template_key = str(node.get("template_key") or "").strip()
            if template_key:
                active_keys.add(resolve_template_key(template_key))

    for template_key in list_task_contract_keys():
        contract = get_task_contract(template_key)
        if contract is not None and contract.render_mode != "code_fallback_only":
            active_keys.add(template_key)

    for descriptor in list_user_tool_descriptors():
        tool_name = descriptor.name
        definition = descriptor.definition
        if definition.force_disabled:
            continue
        if not (
            definition.force_enabled
            or definition.private_default
            or definition.group_default
        ):
            continue
        active_keys.update(tool_template_keys(tool_name))
    return active_keys


def init_prompt_v2_runtime_dir() -> dict[str, Any]:
    source_dir = default_template_dir()
    runtime_dir = ensure_directory_without_symlinks(runtime_template_dir())
    from core.prompt_v2.template_baseline import default_template_state_dir
    from core.prompt_v2.template_migration import TemplateMigrationService

    migration_service = TemplateMigrationService(
        default_dir=source_dir,
        runtime_dir=runtime_dir,
        state_dir=default_template_state_dir(runtime_dir),
    )
    template_recovery = migration_service.recover()
    active_template_keys = _active_runtime_template_keys()
    _raise_for_invalid_active_templates(
        [
            migration_service.store.audit(template_key).to_dict()
            for template_key in sorted(active_template_keys)
        ],
        active_template_keys=active_template_keys,
        ignore_journal_state=True,
        check_missing_sources=False,
    )

    from core.prompt_v2.flow_storage import flow_write_lock

    with flow_write_lock(runtime_dir / "chat" / "flow.json"):
        return _init_prompt_v2_runtime_dir_locked(
            source_dir=source_dir,
            runtime_dir=runtime_dir,
            template_recovery=template_recovery,
        )


def _init_prompt_v2_runtime_dir_locked(
    *,
    source_dir: Path,
    runtime_dir: Path,
    template_recovery: dict[str, Any],
) -> dict[str, Any]:
    """在 flow 写锁内完成恢复、首次安装与最终审计。"""
    from core.prompt_v2.template_baseline import default_template_state_dir
    from core.prompt_v2.template_migration import TemplateMigrationService

    migration_service = TemplateMigrationService(
        default_dir=source_dir,
        runtime_dir=runtime_dir,
        state_dir=default_template_state_dir(runtime_dir),
    )
    locked_recovery = migration_service.recover()
    if locked_recovery.get("status") != "clean":
        template_recovery = locked_recovery
    baseline_store = migration_service.store
    active_template_keys = _active_runtime_template_keys()
    copied: list[str] = []
    baseline_provisioned: list[str] = []
    if source_dir.exists():
        for source_path in sorted(source_dir.rglob("*")):
            if not source_path.is_file() or source_path.suffix not in {".md", ".json"}:
                continue
            rel = source_path.relative_to(source_dir)
            target_path = runtime_dir / rel
            assert_no_symlink_components(target_path)
            if target_path.exists():
                continue
            template_key = rel.with_suffix("").as_posix()
            preflight = baseline_store.audit(template_key)
            if preflight.drift_status == "invalid":
                continue
            if migration_service.provision_missing(template_key):
                copied.append(rel.as_posix())
                baseline_provisioned.append(template_key)

    flow_result = {
        "flow_migrated": False,
        "flow_backup_path": "",
    }

    from core.prompt_v2.task_templates import inspect_live_task_templates

    task_contracts = inspect_live_task_templates()
    audit_keys = set(baseline_store.list_template_keys()) | active_template_keys
    template_audit = [
        baseline_store.audit(template_key).to_dict()
        for template_key in sorted(audit_keys)
    ]
    _raise_for_invalid_active_templates(
        template_audit,
        active_template_keys=active_template_keys,
    )

    return {
        "source_dir": str(source_dir),
        "runtime_dir": str(runtime_dir),
        "copied": copied,
        "migrated": [],
        "baseline_provisioned": baseline_provisioned,
        "template_audit": template_audit,
        "template_recovery": template_recovery,
        "task_contracts": task_contracts,
        "flow_migrated": flow_result["flow_migrated"],
        "flow_backup_path": flow_result["flow_backup_path"],
    }


def _raise_for_invalid_active_templates(
    template_audit: list[dict[str, Any]],
    *,
    active_template_keys: set[str],
    ignore_journal_state: bool = False,
    check_missing_sources: bool = True,
) -> None:
    from core.prompt_v2.template_baseline import TemplateBaselineError

    invalid_templates = [
        item["template_key"]
        for item in template_audit
        if item["template_key"] in active_template_keys
        and not (
            ignore_journal_state
            and item.get("invalid_component") == "journal_state"
        )
        and (
            item["drift_status"] == "invalid"
            or (
                check_missing_sources
                and item["default_sha256"] is None
                and item["runtime_sha256"] is None
            )
        )
    ]
    if invalid_templates:
        raise TemplateBaselineError(
            "Prompt Runtime 模板基线 invalid: "
            + ", ".join(invalid_templates)
        )


def _normalize(raw_key: str) -> str:
    raw = str(raw_key or "").removesuffix(".md").strip()
    if raw.startswith("/"):
        raise ValueError("template_key 包含非法路径")
    key = raw.strip("/")
    if not key:
        raise ValueError("template_key 不能为空")
    if "\\" in key or key.startswith("/") or "//" in key:
        raise ValueError("template_key 包含非法路径")
    parts = key.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("template_key 包含非法路径")
    if not all(all(ch.isalnum() or ch in {"_", "-", "."} for ch in part) for part in parts):
        raise ValueError("template_key 包含非法字符")
    return key


def resolve_template_key(template_key: str) -> str:
    key = _normalize(template_key)
    return _LEGACY_ALIASES.get(key, key)


def legacy_aliases_for(template_key: str) -> list[str]:
    canonical = resolve_template_key(template_key)
    return sorted(alias for alias, target in _LEGACY_ALIASES.items() if target == canonical)


def template_path_for(template_key: str, *, runtime: bool = False) -> Path:
    base = runtime_template_dir() if runtime else default_template_dir()
    return base / f"{resolve_template_key(template_key)}.md"


def template_candidate_paths(template_key: str, *, runtime: bool = False) -> list[Path]:
    base = runtime_template_dir() if runtime else default_template_dir()
    canonical = resolve_template_key(template_key)
    paths = [base / f"{canonical}.md"]
    paths.extend(base / f"{alias}.md" for alias in legacy_aliases_for(canonical))
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        marker = str(path)
        if marker not in seen:
            seen.add(marker)
            deduped.append(path)
    return deduped


def first_existing_template_path(template_key: str, *, runtime: bool = False) -> Path | None:
    for path in template_candidate_paths(template_key, runtime=runtime):
        if path.exists():
            return path
    return None


def classify_template(template_key: str, frontmatter: dict[str, Any] | None = None) -> TemplateRecord:
    key = resolve_template_key(template_key)
    frontmatter = frontmatter or {}
    parts = key.split("/")
    category = parts[0] if parts else "other"
    kind = str(frontmatter.get("kind") or "").strip()
    tool_name = str(frontmatter.get("tool_name") or "").strip()
    if not kind:
        if category == "chat":
            kind = "chat"
        elif category == "tasks":
            kind = "task"
        elif category == "tools":
            kind = "tool"
        else:
            kind = "tool"
    if not tool_name:
        if category == "tools" and len(parts) >= 2:
            tool_name = parts[1]
        elif key in _TASK_TOOL_NAMES:
            tool_name = _TASK_TOOL_NAMES[key]
    display_name = str(frontmatter.get("name") or key)
    editable = True
    runtime_effective = True
    runtime_status = "runtime_template"
    owner_module = ""
    domain = ""
    source_precedence = ("runtime", "default")
    failure_policy = ""
    if category == "chat":
        from core.prompt_v2.section_descriptors import descriptor_for_template_key

        descriptor = descriptor_for_template_key(key)
        if descriptor is None:
            editable = False
            runtime_effective = False
            runtime_status = "unregistered_chat_section"
            source_precedence = ()
            failure_policy = "fail_closed"
        else:
            editable = descriptor.editable
            owner_module = descriptor.owner_module
            domain = descriptor.domain
            source_precedence = descriptor.source_precedence
            failure_policy = descriptor.failure_policy
    elif category == "tasks":
        from core.prompt_v2.task_contracts import get_task_contract

        contract = get_task_contract(key)
        if contract is None:
            editable = False
            runtime_effective = False
            runtime_status = "unregistered_task"
            source_precedence = ()
            failure_policy = "fail_closed"
        else:
            editable = contract.editable
            owner_module = contract.owner_module
            domain = contract.domain
            source_precedence = contract.source_precedence
            failure_policy = contract.template_failure_policy
        if contract is not None and contract.render_mode == "code_fallback_only":
            editable = False
            runtime_effective = False
            runtime_status = "code_fallback_only"
    elif category == "tools":
        from core.tool_registry import get_tool_descriptor

        descriptor = get_tool_descriptor(tool_name)
        if descriptor is None or key not in descriptor.prompt_template_keys:
            editable = False
            runtime_effective = False
            runtime_status = "unregistered_tool_template"
            source_precedence = ()
            failure_policy = "fail_closed"
        else:
            editable = descriptor.prompt_editable
            runtime_effective = descriptor.availability_policy != "force_disabled"
            runtime_status = (
                "runtime_template"
                if runtime_effective
                else "force_disabled_tool"
            )
            owner_module = descriptor.owner_module
            domain = descriptor.domain
            source_precedence = descriptor.prompt_source_precedence
            failure_policy = "fail_closed"
    return TemplateRecord(
        template_key=key,
        category=category,
        kind=kind,
        tool_name=tool_name,
        display_name=display_name,
        editable=editable,
        runtime_effective=runtime_effective,
        runtime_status=runtime_status,
        owner_module=owner_module,
        domain=domain,
        source_precedence=source_precedence,
        failure_policy=failure_policy,
    )


def assert_template_editable(template_key: str) -> None:
    """拒绝保存运行时不会读取的伪可编辑模板。"""

    record = classify_template(template_key)
    if not record.editable:
        raise ValueError(
            f"template {record.template_key} is {record.runtime_status}; "
            "运行时不读取该模板，禁止创建无效覆盖"
        )


def _scan_keys(base: Path) -> set[str]:
    if not base.exists():
        return set()
    keys: set[str] = set()
    for path in base.rglob("*.md"):
        rel = path.relative_to(base).with_suffix("").as_posix()
        try:
            keys.add(resolve_template_key(rel))
        except ValueError:
            continue
    return keys


def list_template_keys() -> list[str]:
    keys = _scan_keys(default_template_dir()) | _scan_keys(runtime_template_dir())
    return sorted(keys)


def list_template_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for key in list_template_keys():
        default_path = first_existing_template_path(key, runtime=False)
        runtime_path = first_existing_template_path(key, runtime=True)
        frontmatter: dict[str, Any] = {}
        for path in (default_path, runtime_path):
            if not path:
                continue
            try:
                from core.prompt_v2.template_loader import split_frontmatter_text

                raw_bytes = read_regular_bytes(path)
                if raw_bytes is None:
                    continue
                meta, _body = split_frontmatter_text(raw_bytes.decode("utf-8"))
                frontmatter.update(meta)
            except (OSError, UnicodeError, ValueError):
                continue
        record = classify_template(key, frontmatter).to_dict()
        record.update({
            "source": "runtime" if runtime_path else "default",
            "default_path": str(default_path or template_path_for(key, runtime=False)),
            "runtime_path": str(runtime_path or template_path_for(key, runtime=True)),
            "active_path": str(runtime_path or default_path or template_path_for(key, runtime=False)),
        })
        records.append(record)
    return records


def live_task_template_keys() -> list[str]:
    return sorted(_TASK_TOOL_NAMES)
