"""Persona 事实正文与证据元数据的确定性辅助函数。"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def normalize_fact_content(value: str) -> str:
    """规范化事实正文，仅用于稳定哈希，不改写持久化内容。"""

    return " ".join(str(value or "").strip().lower().split()).rstrip("。.!！?？")


def content_hash(value: str) -> str:
    """生成同文事实的稳定短哈希。"""

    normalized = normalize_fact_content(value)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def safe_json_list(value: str | None) -> list[Any]:
    """解析 JSON 数组；历史脏值按空数组处理。"""

    try:
        parsed = json.loads(value or "[]")
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def safe_json_dict(value: str | None) -> dict[str, Any]:
    """解析 JSON 对象；历史脏值按空对象处理。"""

    try:
        parsed = json.loads(value or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}
