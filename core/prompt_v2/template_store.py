from __future__ import annotations

from pathlib import Path
from typing import Any

from core.prompt_v2.section_renderer import sha256_text
from core.prompt_v2.template_loader import (
    default_template_dir,
    load_template,
    runtime_template_dir,
    split_frontmatter_text,
)
from core.prompt_v2.template_registry import (
    classify_template,
    first_existing_template_path,
    list_template_keys,
    resolve_template_key,
    template_path_for,
)
from core.prompt_v2.variables import list_variables, validate_scoped_template


def _read_body(path: Path | None) -> str:
    if not path or not path.exists():
        return ""
    _frontmatter, body = split_frontmatter_text(path.read_text(encoding="utf-8"))
    return body.strip()


def _read_frontmatter(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    frontmatter, _body = split_frontmatter_text(path.read_text(encoding="utf-8"))
    return frontmatter


def _frontmatter_text(values: dict[str, Any]) -> str:
    lines = ["---"]
    for key in ("name", "version", "kind", "tool_name", "description"):
        value = values.get(key)
        if value is None or value == "":
            continue
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _template_record(key: str, *, db=None) -> dict[str, Any]:
    canonical = resolve_template_key(key)
    default_path = first_existing_template_path(canonical, runtime=False)
    runtime_path = first_existing_template_path(canonical, runtime=True)
    template = load_template(canonical)
    frontmatter = {
        **_read_frontmatter(default_path),
        **_read_frontmatter(runtime_path),
    }
    classified = classify_template(canonical, frontmatter)
    tool_schema = None
    if classified.kind == "tool" and classified.tool_name:
        try:
            from core.tool_schema_preview import build_tool_schema

            tool_schema = build_tool_schema(classified.tool_name, db=db)
        except Exception:
            tool_schema = None
    return {
        "template_key": canonical,
        "name": str(frontmatter.get("name") or classified.display_name or canonical),
        "description": str(frontmatter.get("description") or ""),
        "version": frontmatter.get("version", ""),
        "kind": classified.kind,
        "category": classified.category,
        "tool_name": classified.tool_name,
        "tool_schema": tool_schema,
        "source": "runtime" if runtime_path else "default",
        "active_path": str(runtime_path or default_path or template.path),
        "runtime_path": str(runtime_path or template_path_for(canonical, runtime=True)),
        "default_path": str(default_path or template_path_for(canonical, runtime=False)),
        "sha256": sha256_text(template.body),
        "size": len(template.body.encode("utf-8")),
        "variables": list_variables(canonical),
        "frontmatter": frontmatter,
    }


def _build_tree(items: list[dict[str, Any]]) -> dict[str, Any]:
    tree: dict[str, Any] = {"chat": [], "tools": {}, "tasks": []}
    for item in items:
        key = str(item.get("template_key") or "")
        category = str(item.get("category") or "")
        if category == "tools":
            parts = key.split("/")
            tool_name = parts[1] if len(parts) > 1 else str(item.get("tool_name") or "")
            tree["tools"].setdefault(tool_name, []).append(item)
        elif category == "tasks":
            tree["tasks"].append(item)
        else:
            tree["chat"].append(item)
    for key in list(tree["tools"]):
        tree["tools"][key] = sorted(tree["tools"][key], key=lambda item: item["template_key"])
    tree["chat"] = sorted(tree["chat"], key=lambda item: item["template_key"])
    tree["tasks"] = sorted(tree["tasks"], key=lambda item: item["template_key"])
    return tree


def list_templates(*, db=None) -> dict[str, Any]:
    items = [_template_record(key, db=db) for key in list_template_keys()]
    items = sorted(items, key=lambda item: item["template_key"])
    return {
        "items": items,
        "tree": _build_tree(items),
        "default_dir": str(default_template_dir()),
        "runtime_dir": str(runtime_template_dir()),
    }


def get_template(template_key: str, *, db=None) -> dict[str, Any]:
    key = resolve_template_key(template_key)
    record = _template_record(key, db=db)
    default_path = first_existing_template_path(key, runtime=False)
    runtime_path = first_existing_template_path(key, runtime=True)
    return {
        **record,
        "content": _read_body(runtime_path or default_path),
        "default_content": _read_body(default_path),
        "runtime_content": _read_body(runtime_path),
    }


def create_template(
    template_key: str,
    *,
    content: str,
    name: str = "",
    kind: str = "tool",
    tool_name: str = "",
    description: str = "",
) -> dict[str, Any]:
    key = resolve_template_key(template_key)
    text = str(content or "")
    validate_scoped_template(key, text)
    path = template_path_for(key, runtime=True)
    if path.exists():
        raise ValueError("运行时模板已存在")
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = {
        "name": name or key,
        "version": 1,
        "kind": kind,
        "tool_name": tool_name,
        "description": description,
    }
    normalized = text.rstrip() + "\n"
    path.write_text(_frontmatter_text(frontmatter) + normalized, encoding="utf-8")
    from core.prompt_v2.tool_templates import clear_tool_template_policy_cache

    clear_tool_template_policy_cache()
    return {
        "saved": True,
        "created": True,
        "template_key": key,
        "runtime_path": str(path),
        "after_hash": sha256_text(normalized.rstrip("\n")),
    }


def save_template(template_key: str, content: str) -> dict[str, Any]:
    key = resolve_template_key(template_key)
    text = str(content or "")
    validate_scoped_template(key, text)
    path = template_path_for(key, runtime=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    before = _read_body(path)
    normalized = text.rstrip() + "\n"
    path.write_text(normalized, encoding="utf-8")
    from core.prompt_v2.tool_templates import clear_tool_template_policy_cache

    clear_tool_template_policy_cache()
    return {
        "saved": True,
        "template_key": key,
        "runtime_path": str(path),
        "before_hash": sha256_text(before),
        "after_hash": sha256_text(normalized.rstrip("\n")),
    }


def delete_runtime_template(template_key: str) -> dict[str, Any]:
    key = resolve_template_key(template_key)
    path = template_path_for(key, runtime=True)
    existed = path.exists()
    if existed:
        path.unlink()
        from core.prompt_v2.tool_templates import clear_tool_template_policy_cache

        clear_tool_template_policy_cache()
    return {
        "deleted": existed,
        "template_key": key,
        "runtime_path": str(path),
    }


def reset_template(template_key: str) -> dict[str, Any]:
    result = delete_runtime_template(template_key)
    result["reset"] = True
    return result
