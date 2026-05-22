from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PromptV2Template:
    prompt_key: str
    path: Path
    frontmatter: dict[str, Any]
    body: str
    raw: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_template_dir() -> Path:
    return Path(os.environ.get("NANOBOT_PROMPT_V2_DIR") or (_repo_root() / "prompts.v2.default"))


def _safe_prompt_key(prompt_key: str) -> str:
    key = str(prompt_key or "").removesuffix(".md").strip()
    if not key:
        raise ValueError("prompt_key 不能为空")
    if not all(ch.isalnum() or ch in {"_", "-", "."} for ch in key):
        raise ValueError("prompt_key 包含非法字符")
    return key


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


def load_template(prompt_key: str, *, template_dir: str | Path | None = None) -> PromptV2Template:
    key = _safe_prompt_key(prompt_key)
    base = Path(template_dir) if template_dir else default_template_dir()
    path = base / f"{key}.md"
    raw = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(raw)
    return PromptV2Template(
        prompt_key=key,
        path=path,
        frontmatter=frontmatter,
        body=body.strip(),
        raw=raw,
    )
