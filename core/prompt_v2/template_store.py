from __future__ import annotations

from pathlib import Path
from typing import Any

from core.prompt_v2.section_renderer import sha256_text
from core.prompt_v2.template_loader import (
    default_template_dir,
    load_template,
    runtime_template_dir,
)
from core.prompt_v2.variables import list_variables, validate_scoped_template


def _safe_template_key(template_key: str) -> str:
    key = str(template_key or "").removesuffix(".md").strip()
    if not key:
        raise ValueError("template_key 不能为空")
    if not all(ch.isalnum() or ch in {"_", "-", "."} for ch in key):
        raise ValueError("template_key 包含非法字符")
    return key


def _body_from(path: Path) -> str:
    if not path.exists():
        return ""
    return load_template(path.stem, template_dir=path.parent).body


def _load_optional_template(path: Path):
    if not path.exists():
        return None
    return load_template(path.stem, template_dir=path.parent)


def _template_kind(key: str, frontmatter: dict[str, Any]) -> str:
    raw = str(frontmatter.get("kind") or "").strip()
    if raw:
        return raw
    if key.startswith("chat_") or key in {"identity_context"}:
        return "chat"
    return "tool"


def _tool_name(key: str, frontmatter: dict[str, Any], kind: str) -> str:
    raw = str(frontmatter.get("tool_name") or "").strip()
    if raw:
        return raw
    if kind != "tool":
        return ""
    if key == "reply_contract_retry":
        return "reply"
    return key


def _template_record(key: str) -> dict[str, Any]:
    default_path = default_template_dir() / f"{key}.md"
    runtime_path = runtime_template_dir() / f"{key}.md"
    default_template = _load_optional_template(default_path)
    runtime_template = _load_optional_template(runtime_path)
    template = runtime_template or default_template
    if template is None:
        raise FileNotFoundError(key)
    active_path = runtime_path if runtime_template else default_path
    content = template.body
    frontmatter = {
        **(default_template.frontmatter if default_template else {}),
        **(runtime_template.frontmatter if runtime_template else {}),
    }
    kind = _template_kind(key, frontmatter)
    return {
        "template_key": key,
        "name": str(frontmatter.get("name") or key),
        "description": str(frontmatter.get("description") or ""),
        "version": frontmatter.get("version", ""),
        "kind": kind,
        "tool_name": _tool_name(key, frontmatter, kind),
        "source": "runtime" if runtime_path.exists() else "default",
        "active_path": str(active_path),
        "runtime_path": str(runtime_path),
        "default_path": str(default_path),
        "sha256": sha256_text(content),
        "size": len(content.encode("utf-8")),
        "variables": list_variables(key),
    }


def list_templates() -> dict[str, Any]:
    default_dir = default_template_dir()
    runtime_dir = runtime_template_dir()
    keys = {
        path.stem
        for base in (default_dir, runtime_dir)
        if base.exists()
        for path in base.glob("*.md")
    }
    return {
        "items": [_template_record(key) for key in sorted(keys)],
        "default_dir": str(default_dir),
        "runtime_dir": str(runtime_dir),
    }


def get_template(template_key: str) -> dict[str, Any]:
    key = _safe_template_key(template_key)
    record = _template_record(key)
    default_path = Path(record["default_path"])
    runtime_path = Path(record["runtime_path"])
    return {
        **record,
        "content": _body_from(runtime_path if runtime_path.exists() else default_path),
        "default_content": _body_from(default_path),
        "runtime_content": _body_from(runtime_path),
    }


def save_template(template_key: str, content: str) -> dict[str, Any]:
    key = _safe_template_key(template_key)
    text = str(content or "")
    validate_scoped_template(key, text)
    runtime_dir = runtime_template_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    path = runtime_dir / f"{key}.md"
    before = _body_from(path) if path.exists() else ""
    normalized = text.rstrip() + "\n"
    path.write_text(normalized, encoding="utf-8")
    return {
        "saved": True,
        "template_key": key,
        "runtime_path": str(path),
        "before_hash": sha256_text(before),
        "after_hash": sha256_text(normalized.rstrip("\n")),
    }
