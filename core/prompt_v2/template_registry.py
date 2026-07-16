from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_LEGACY_ALIASES: dict[str, str] = {
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
    "classifier_legacy": "tasks/classifier_legacy",
    "private_decision": "tasks/private_decision",
    "timing_gate": "tasks/timing_gate",
    "memory_extract": "tasks/memory_extract",
    "memory_digest_system": "tasks/memory_digest_system",
    "memory_digest_user": "tasks/memory_digest_user",
    "reply_contract_retry": "tasks/reply_contract_retry",
    "outreach_extract": "tasks/outreach_extract",
    "outreach_judge": "tasks/outreach_judge",
    "outreach_generate": "tasks/outreach_generate",
    "proactive_research": "tasks/proactive_research",
}

_TASK_TOOL_NAMES: dict[str, str] = {
    "tasks/classifier_legacy": "classifier_legacy",
    "tasks/private_decision": "private_decision",
    "tasks/timing_gate": "timing_gate",
    "tasks/memory_extract": "memory_extract",
    "tasks/memory_digest_system": "memory_digest",
    "tasks/memory_digest_user": "memory_digest",
    "tasks/reply_contract_retry": "reply",
    "tasks/outreach_extract": "outreach_extract",
    "tasks/outreach_judge": "outreach_judge",
    "tasks/outreach_generate": "outreach_generate",
    "tasks/proactive_research": "web_search",
}

_TOOL_WORKFLOW_TEMPLATE_KEYS: dict[str, tuple[str, ...]] = {
    "group_analysis": (
        "tools/group_analysis/system",
        "tools/group_analysis/topics",
        "tools/group_analysis/titles",
        "tools/group_analysis/quotes",
        "tools/group_analysis/quality",
    ),
    "image_summary": (
        "tools/image_summary/system",
        "tools/image_summary/user",
    ),
    "ai_daily": (
        "tools/ai_daily/digest_system",
        "tools/ai_daily/digest_user",
        "tools/ai_daily/quality_system",
        "tools/ai_daily/quality_user",
    ),
}

_TOOL_USAGE_TEMPLATE_KEYS: dict[str, str] = {
    tool_name: f"tools/{tool_name}/usage"
    for tool_name in (
        "reply",
        "no_reply",
        "sticker_search",
        "image_generation",
        "sql_analysis",
        "python_sandbox",
        "ai_daily",
        "memory_query",
        "knowledge_query",
        "web_search",
        "image_summary",
        "group_analysis",
        "persona_update",
        "schedule_task",
    )
}

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
    from core.tool_registry import TOOL_METADATA

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

    for tool_name, definition in TOOL_METADATA.items():
        if definition.force_disabled:
            continue
        if not (
            definition.force_enabled
            or definition.private_default
            or definition.group_default
        ):
            continue
        candidates = set(_TOOL_WORKFLOW_TEMPLATE_KEYS.get(tool_name, ()))
        usage_key = _TOOL_USAGE_TEMPLATE_KEYS.get(tool_name)
        if usage_key is not None:
            candidates.add(usage_key)
        active_keys.update(candidates)
    return active_keys


def init_prompt_v2_runtime_dir() -> dict[str, Any]:
    source_dir = default_template_dir()
    runtime_dir = ensure_directory_without_symlinks(runtime_template_dir())
    from core.prompt_v2.template_baseline import (
        TemplateBaselineError,
        default_template_state_dir,
    )
    from core.prompt_v2.template_migration import TemplateMigrationService

    migration_service = TemplateMigrationService(
        default_dir=source_dir,
        runtime_dir=runtime_dir,
        state_dir=default_template_state_dir(runtime_dir),
    )
    template_recovery = migration_service.recover()
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
    invalid_templates = [
        item["template_key"]
        for item in template_audit
        if item["template_key"] in active_template_keys
        and (
            item["drift_status"] == "invalid"
            or (
                item["default_sha256"] is None
                and item["runtime_sha256"] is None
            )
        )
    ]
    if invalid_templates:
        raise TemplateBaselineError(
            "Prompt Runtime 模板基线 invalid: "
            + ", ".join(invalid_templates)
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
    return TemplateRecord(
        template_key=key,
        category=category,
        kind=kind,
        tool_name=tool_name,
        display_name=display_name,
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
