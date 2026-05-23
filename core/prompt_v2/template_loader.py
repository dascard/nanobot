from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.prompt_v2.template_registry import (
    default_template_dir,
    first_existing_template_path,
    resolve_template_key,
    runtime_template_dir,
    template_path_for,
)


@dataclass(frozen=True)
class PromptV2Template:
    prompt_key: str
    path: Path
    frontmatter: dict[str, Any]
    body: str
    raw: str


def _safe_prompt_key(prompt_key: str) -> str:
    return resolve_template_key(prompt_key)


def _parse_scalar(raw: str) -> Any:
    value = raw.strip()
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        return value.strip("\"'")


def _split_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    if not raw.startswith("---"):
        return {}, raw
    lines = raw.splitlines()
    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        return {}, raw
    meta: dict[str, Any] = {}
    current_key = ""
    for line in lines[1:end_idx]:
        if not line.strip() or line.strip().startswith("#"):
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and current_key:
            meta.setdefault(current_key, []).append(stripped[2:].strip().strip("\"'"))
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value:
            meta[key] = _parse_scalar(value)
            current_key = ""
        else:
            meta[key] = []
            current_key = key
    body = "\n".join(lines[end_idx + 1:])
    if raw.endswith("\n"):
        body += "\n"
    return meta, body


def split_frontmatter_text(raw: str) -> tuple[dict[str, Any], str]:
    return _split_frontmatter(raw)


def load_template(prompt_key: str, *, template_dir: str | Path | None = None) -> PromptV2Template:
    key = _safe_prompt_key(prompt_key)
    if template_dir:
        path = Path(template_dir) / f"{key}.md"
        raw = path.read_text(encoding="utf-8")
        frontmatter, body = _split_frontmatter(raw)
    else:
        runtime_path = first_existing_template_path(key, runtime=True)
        default_path = first_existing_template_path(key, runtime=False)
        path = runtime_path or default_path or template_path_for(key, runtime=False)
        raw = path.read_text(encoding="utf-8")
        frontmatter, body = _split_frontmatter(raw)
        if runtime_path and default_path:
            default_raw = default_path.read_text(encoding="utf-8")
            default_frontmatter, _default_body = _split_frontmatter(default_raw)
            frontmatter = {**default_frontmatter, **frontmatter}
    return PromptV2Template(
        prompt_key=key,
        path=path,
        frontmatter=frontmatter,
        body=body.strip(),
        raw=raw,
    )
