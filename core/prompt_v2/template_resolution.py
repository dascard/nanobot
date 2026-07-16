from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from core.prompt_v2.flow_storage import read_regular_bytes


TemplateSource = Literal["runtime", "default", "built_in"]


@dataclass(frozen=True)
class TemplateResolution:
    template_key: str
    active_source: TemplateSource
    active_path: str | None
    runtime_path: str | None
    default_path: str | None
    active_sha256: str
    runtime_sha256: str | None
    default_sha256: str | None
    baseline_version: str | None
    drift_status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResolvedTemplateFiles:
    resolution: TemplateResolution
    active_bytes: bytes
    default_bytes: bytes | None


_RESOLUTION_FIELDS = (
    "template_key",
    "active_source",
    "active_path",
    "runtime_path",
    "default_path",
    "active_sha256",
    "runtime_sha256",
    "default_sha256",
    "baseline_version",
    "drift_status",
)
_ACTIVE_SOURCES = frozenset({"runtime", "default", "built_in"})
_RUNTIME_DRIFT_STATUSES = frozenset(
    {
        "in_sync",
        "upgrade_available",
        "local_override",
        "diverged",
        "runtime_missing",
        "untracked_legacy",
        "invalid",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _read_template_bytes(path: Path) -> bytes:
    content = read_regular_bytes(path)
    if content is None:  # pragma: no cover - missing_ok=False 保证不会发生
        raise FileNotFoundError(path)
    return content


def resolve_template_files(
    template_key: str,
    *,
    runtime_path: Path | None = None,
    default_path: Path | None = None,
    built_in_path: Path | None = None,
) -> ResolvedTemplateFiles:
    """解析一次模板加载使用的真实文件及其原始字节摘要。"""
    if built_in_path is not None:
        active_bytes = _read_template_bytes(built_in_path)
        resolution = TemplateResolution(
            template_key=template_key,
            active_source="built_in",
            active_path=str(built_in_path),
            runtime_path=None,
            default_path=None,
            active_sha256=_sha256_bytes(active_bytes),
            runtime_sha256=None,
            default_sha256=None,
            baseline_version=None,
            drift_status="built_in",
        )
        return ResolvedTemplateFiles(
            resolution=resolution,
            active_bytes=active_bytes,
            default_bytes=None,
        )

    runtime_bytes = (
        _read_template_bytes(runtime_path) if runtime_path is not None else None
    )
    default_bytes = (
        _read_template_bytes(default_path) if default_path is not None else None
    )
    if runtime_bytes is None and default_bytes is None:
        raise FileNotFoundError(f"Prompt 模板不存在: {template_key}")

    from core.prompt_v2.template_baseline import (
        TemplateBaselineError,
        TemplateBaselineStore,
    )

    baseline_report = TemplateBaselineStore.from_environment().audit(template_key)
    if baseline_report.drift_status == "invalid":
        raise TemplateBaselineError(
            baseline_report.invalid_reason
            or f"模板 {template_key} 的基线状态 invalid"
        )
    if (
        baseline_report.runtime_sha256
        != (_sha256_bytes(runtime_bytes) if runtime_bytes is not None else None)
        or baseline_report.default_sha256
        != (_sha256_bytes(default_bytes) if default_bytes is not None else None)
    ):
        raise TemplateBaselineError(
            f"模板 {template_key} 在来源解析期间发生并发变化"
        )

    if runtime_bytes is not None:
        active_source: TemplateSource = "runtime"
        active_path = runtime_path
        active_bytes = runtime_bytes
        drift_status = baseline_report.drift_status
    else:
        active_source = "default"
        active_path = default_path
        active_bytes = default_bytes
        drift_status = baseline_report.drift_status

    if active_path is None or active_bytes is None:
        raise RuntimeError(f"Prompt 模板来源解析不完整: {template_key}")
    resolution = TemplateResolution(
        template_key=template_key,
        active_source=active_source,
        active_path=str(active_path),
        runtime_path=str(runtime_path) if runtime_path is not None else None,
        default_path=str(default_path) if default_path is not None else None,
        active_sha256=_sha256_bytes(active_bytes),
        runtime_sha256=(
            _sha256_bytes(runtime_bytes) if runtime_bytes is not None else None
        ),
        default_sha256=(
            _sha256_bytes(default_bytes) if default_bytes is not None else None
        ),
        baseline_version=baseline_report.baseline_version,
        drift_status=drift_status,
    )
    return ResolvedTemplateFiles(
        resolution=resolution,
        active_bytes=active_bytes,
        default_bytes=default_bytes,
    )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _required_sha256(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not _SHA256_RE.fullmatch(text):
        raise ValueError(f"模板解析记录的 {field} 必须是完整 SHA-256")
    return text


def _optional_sha256(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    return _required_sha256(value, field=field)


def normalize_template_resolutions(
    resolutions: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """收敛为不含模板正文的稳定追踪结构。"""
    normalized: dict[str, dict[str, Any]] = {}
    for raw_node_id, raw_resolution in sorted(
        (resolutions or {}).items(),
        key=lambda item: str(item[0]),
    ):
        node_id = str(raw_node_id).strip()
        if not node_id:
            raise ValueError("模板解析记录的 flow node ID 不能为空")
        if isinstance(raw_resolution, TemplateResolution):
            source = raw_resolution.to_dict()
        elif isinstance(raw_resolution, Mapping):
            source = dict(raw_resolution)
        else:
            raise TypeError(f"模板解析记录 {node_id} 必须是对象")
        active_source = str(source.get("active_source") or "").strip()
        if active_source not in _ACTIVE_SOURCES:
            raise ValueError(f"模板解析记录 {node_id} 的 active_source 非法")
        template_key = str(source.get("template_key") or "").strip()
        if not template_key:
            raise ValueError(f"模板解析记录 {node_id} 的 template_key 不能为空")
        active_path = _optional_text(source.get("active_path"))
        runtime_path = _optional_text(source.get("runtime_path"))
        default_path = _optional_text(source.get("default_path"))
        active_sha256 = _required_sha256(
            source.get("active_sha256"),
            field=f"{node_id}.active_sha256",
        )
        runtime_sha256 = _optional_sha256(
            source.get("runtime_sha256"),
            field=f"{node_id}.runtime_sha256",
        )
        default_sha256 = _optional_sha256(
            source.get("default_sha256"),
            field=f"{node_id}.default_sha256",
        )
        drift_status = str(source.get("drift_status") or "").strip()
        if active_source == "built_in":
            if drift_status != "built_in":
                raise ValueError(f"模板解析记录 {node_id} 的 built_in drift 非法")
        elif drift_status not in _RUNTIME_DRIFT_STATUSES:
            raise ValueError(f"模板解析记录 {node_id} 的 drift_status 非法")
        if not active_path:
            raise ValueError(f"模板解析记录 {node_id} 的 active_path 不能为空")
        if active_source == "runtime" and (
            not runtime_path
            or runtime_sha256 is None
            or active_path != runtime_path
            or active_sha256 != runtime_sha256
        ):
            raise ValueError(f"模板解析记录 {node_id} 的 runtime 来源字段不一致")
        if active_source == "default" and (
            not default_path
            or default_sha256 is None
            or active_path != default_path
            or active_sha256 != default_sha256
        ):
            raise ValueError(f"模板解析记录 {node_id} 的 default 来源字段不一致")
        if active_source == "built_in" and any(
            value is not None
            for value in (runtime_path, default_path, runtime_sha256, default_sha256)
        ):
            raise ValueError(f"模板解析记录 {node_id} 的 built_in 字段不一致")
        item = {
            "template_key": template_key,
            "active_source": active_source,
            "active_path": active_path,
            "runtime_path": runtime_path,
            "default_path": default_path,
            "active_sha256": active_sha256,
            "runtime_sha256": runtime_sha256,
            "default_sha256": default_sha256,
            "baseline_version": _optional_text(source.get("baseline_version")),
            "drift_status": drift_status,
        }
        normalized[node_id] = {key: item[key] for key in _RESOLUTION_FIELDS}
    return normalized


def serialize_template_resolutions_json(
    resolutions: Mapping[str, Any] | None,
) -> str:
    return json.dumps(
        normalize_template_resolutions(resolutions),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def build_template_trace_fields(
    resolutions: Mapping[str, Any] | None,
) -> dict[str, str]:
    normalized = normalize_template_resolutions(resolutions)
    active_sources = {
        item["active_source"]
        for item in normalized.values()
    }
    if not active_sources:
        prompt_source = "built_in"
    elif len(active_sources) == 1:
        prompt_source = next(iter(active_sources))
    else:
        prompt_source = "mixed"
    base = normalized.get("base_contract", {})
    return {
        "prompt_source": prompt_source,
        "prompt_runtime_path": str(base.get("runtime_path") or ""),
        "prompt_default_path": str(base.get("default_path") or ""),
    }
