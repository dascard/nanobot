"""Agent Manifest 的确定性 JSON 与内容摘要。"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
import hashlib
import json
from typing import Any


def _sort_key(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def canonical_value(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: canonical_value(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, frozenset):
        return [
            canonical_value(item)
            for item in sorted(value, key=_sort_key)
        ]
    if isinstance(value, tuple):
        return [canonical_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"Manifest 包含不可序列化类型：{type(value).__name__}")


def manifest_payload(manifest: object) -> dict[str, object]:
    if not is_dataclass(manifest) or isinstance(manifest, type):
        raise TypeError("manifest 必须是 dataclass 实例")
    payload = {
        item.name: canonical_value(getattr(manifest, item.name))
        for item in fields(manifest)
        if item.name != "content_sha256"
    }
    return payload

def canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def content_sha256(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def manifest_dict(manifest: object) -> dict[str, Any]:
    payload = manifest_payload(manifest)
    payload["content_sha256"] = str(
        getattr(manifest, "content_sha256", "")
    )
    return payload
