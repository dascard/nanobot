"""从受信 creature 目录加载可注册 Agent 元数据。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import yaml

from core.registry.validation import validate_identifier


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_FIELDS = frozenset({
    "schema_version",
    "agent_id",
    "display_name",
    "description",
    "profile_file",
    "config_file",
    "allowed_tools",
    "allow_dynamic_tools",
    "allowed_entrypoints",
    "default",
    "model_profile_id",
    "manifest_snapshot_sha256",
})
DEFAULT_CREATURES_ROOT = Path(__file__).resolve().parents[1] / "creatures"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_child_file(directory: Path, raw_name: object, field_name: str) -> Path:
    name = str(raw_name or "").strip()
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError(f"{field_name} 必须是 creature 目录内的文件名")
    path = directory / name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{field_name} 文件不存在或不允许符号链接")
    resolved = path.resolve(strict=True)
    if resolved.parent != directory:
        raise ValueError(f"{field_name} 越过 creature 目录边界")
    return resolved


def _optional_sha256(value: object, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized and _SHA256_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} 必须是 SHA-256 十六进制摘要")
    return normalized


@dataclass(frozen=True, slots=True)
class CreatureAgentSpec:
    """构造一个 BridgePool 所需的冻结 creature 事实。"""

    agent_id: str
    display_name: str
    description: str
    creature_path: str
    profile: str
    allowed_tool_names: frozenset[str] | None
    allow_dynamic_tools: bool
    allowed_entrypoints: tuple[str, ...]
    default: bool
    model_profile_id: str
    source_sha256: str
    profile_sha256: str
    tool_policy_sha256: str
    manifest_snapshot_sha256: str = ""


def load_creature_agent_spec(
    agent_id: str,
    *,
    creatures_root: Path | None = None,
) -> CreatureAgentSpec:
    """只按显式 ``agent_id`` 加载一个 creature，不扫描或自动注册目录。"""

    normalized_id = validate_identifier(agent_id, field_name="agent_id")
    root = (creatures_root or DEFAULT_CREATURES_ROOT).resolve(strict=True)
    directory = root / normalized_id
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"Agent creature 不存在：{normalized_id}")
    directory = directory.resolve(strict=True)
    if directory.parent != root or directory.name != normalized_id:
        raise ValueError("Agent creature 越过受信目录边界")
    manifest_path = _safe_child_file(directory, "agent.yaml", "agent.yaml")
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("agent.yaml 必须是对象")
    unknown = sorted(set(raw) - _ALLOWED_FIELDS)
    if unknown:
        raise ValueError(f"agent.yaml 包含未知字段：{', '.join(unknown)}")
    if raw.get("schema_version") != 1:
        raise ValueError("agent.yaml schema_version 当前只支持 1")
    declared_id = validate_identifier(
        raw.get("agent_id"),
        field_name="agent.yaml.agent_id",
    )
    if declared_id != normalized_id:
        raise ValueError("agent.yaml agent_id 与目录名不一致")

    display_name = str(raw.get("display_name") or "").strip()
    description = str(raw.get("description") or "").strip()
    if not display_name or len(display_name) > 128:
        raise ValueError("agent.yaml display_name 必须是 1..128 字符")
    if len(description) > 1000:
        raise ValueError("agent.yaml description 不能超过 1000 字符")
    profile_path = _safe_child_file(
        directory,
        raw.get("profile_file", "profile.md"),
        "profile_file",
    )
    config_path = _safe_child_file(
        directory,
        raw.get("config_file", "config.yaml"),
        "config_file",
    )
    profile = profile_path.read_text(encoding="utf-8").strip()
    if not profile or len(profile) > 8000:
        raise ValueError("Agent profile 必须是 1..8000 字符")

    raw_tools = raw.get("allowed_tools")
    allowed_tool_names: frozenset[str] | None
    if raw_tools == "*":
        allowed_tool_names = None
    else:
        if not isinstance(raw_tools, list) or not raw_tools:
            raise ValueError("allowed_tools 必须是非空数组或 '*' ")
        allowed_tool_names = frozenset(
            validate_identifier(value, field_name="allowed_tools")
            for value in raw_tools
        )
        if len(allowed_tool_names) != len(raw_tools):
            raise ValueError("allowed_tools 不能重复")

    raw_entrypoints = raw.get("allowed_entrypoints")
    if not isinstance(raw_entrypoints, list) or not raw_entrypoints:
        raise ValueError("allowed_entrypoints 必须是非空数组")
    allowed_entrypoints = tuple(sorted({
        validate_identifier(value, field_name="allowed_entrypoints")
        for value in raw_entrypoints
    }))
    if len(allowed_entrypoints) != len(raw_entrypoints):
        raise ValueError("allowed_entrypoints 不能重复")

    model_profile_id = str(raw.get("model_profile_id") or "").strip()
    if model_profile_id:
        validate_identifier(model_profile_id, field_name="model_profile_id")
    profile_sha256 = _file_sha256(profile_path)
    tool_policy_sha256 = hashlib.sha256(_canonical_json({
        "allow_dynamic_tools": bool(raw.get("allow_dynamic_tools", False)),
        "allowed_tools": (
            "*" if allowed_tool_names is None else sorted(allowed_tool_names)
        ),
    }).encode("utf-8")).hexdigest()
    source_sha256 = hashlib.sha256(_canonical_json({
        "agent_yaml_sha256": _file_sha256(manifest_path),
        "config_sha256": _file_sha256(config_path),
        "profile_sha256": profile_sha256,
    }).encode("utf-8")).hexdigest()
    return CreatureAgentSpec(
        agent_id=normalized_id,
        display_name=display_name,
        description=description,
        creature_path=str(directory),
        profile=profile,
        allowed_tool_names=allowed_tool_names,
        allow_dynamic_tools=bool(raw.get("allow_dynamic_tools", False)),
        allowed_entrypoints=allowed_entrypoints,
        default=bool(raw.get("default", False)),
        model_profile_id=model_profile_id,
        source_sha256=source_sha256,
        profile_sha256=profile_sha256,
        tool_policy_sha256=tool_policy_sha256,
        manifest_snapshot_sha256=_optional_sha256(
            raw.get("manifest_snapshot_sha256"),
            "manifest_snapshot_sha256",
        ),
    )


def load_creature_agent_specs(
    agent_ids: tuple[str, ...] | list[str],
    *,
    creatures_root: Path | None = None,
) -> tuple[CreatureAgentSpec, ...]:
    normalized = tuple(
        validate_identifier(value, field_name="agent_id") for value in agent_ids
    )
    if not normalized or len(set(normalized)) != len(normalized):
        raise ValueError("Agent ID 列表不能为空或重复")
    return tuple(
        load_creature_agent_spec(value, creatures_root=creatures_root)
        for value in normalized
    )


__all__ = [
    "CreatureAgentSpec",
    "DEFAULT_CREATURES_ROOT",
    "load_creature_agent_spec",
    "load_creature_agent_specs",
]
