from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


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
    "persona_update": "tools/persona_update/usage",
    "schedule_task": "tools/schedule_task/usage",
    "sticker_search": "tools/sticker_search/usage",
    "timing_gate": "tasks/timing_gate",
    "memory_extract": "tasks/memory_extract",
    "reply_contract_retry": "tasks/reply_contract_retry",
}

_TASK_TOOL_NAMES: dict[str, str] = {
    "tasks/timing_gate": "timing_gate",
    "tasks/memory_extract": "memory_extract",
    "tasks/reply_contract_retry": "reply",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_template_dir() -> Path:
    return Path(
        os.environ.get("NANOBOT_PROMPT_V2_DIR")
        or os.environ.get("NANOBOT_PROMPT_V2_DEFAULT_DIR")
        or (_repo_root() / "prompts.v2.default")
    )


def runtime_template_dir() -> Path:
    return Path(os.environ.get("NANOBOT_PROMPT_V2_RUNTIME_DIR") or (_repo_root() / "data" / "prompts_v2"))


def init_prompt_v2_runtime_dir() -> dict[str, Any]:
    source_dir = default_template_dir()
    runtime_dir = runtime_template_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    if not source_dir.exists():
        return {"source_dir": str(source_dir), "runtime_dir": str(runtime_dir), "copied": copied}

    for source_path in sorted(source_dir.rglob("*")):
        if not source_path.is_file() or source_path.suffix not in {".md", ".json"}:
            continue
        rel = source_path.relative_to(source_dir)
        target_path = runtime_dir / rel
        if target_path.exists():
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
        copied.append(rel.as_posix())

    return {"source_dir": str(source_dir), "runtime_dir": str(runtime_dir), "copied": copied}


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

                meta, _body = split_frontmatter_text(path.read_text(encoding="utf-8"))
                frontmatter.update(meta)
            except Exception:
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
