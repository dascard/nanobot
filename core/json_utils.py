"""轻量 JSON 容错解析工具。"""

from __future__ import annotations

import json
import re
from typing import Any


def json_repair(raw: Any) -> dict[str, Any]:
    """兼容旧 LLM 工作流的 best-effort JSON 对象解析。"""
    if raw is None:
        return {"parse_error": True, "raw": ""}
    raw = str(raw).strip()
    if not raw:
        return {"parse_error": True, "raw": ""}

    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass

    try:
        match = re.search(r"```json\s*([\s\S]*?)```", raw)
        if match:
            return json.loads(match.group(1).strip())
    except (json.JSONDecodeError, ValueError):
        pass

    try:
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            return json.loads(match.group())
    except (json.JSONDecodeError, ValueError):
        pass

    try:
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            fixed = match.group()
            fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
            fixed = fixed.replace("'", '"')
            return json.loads(fixed)
    except (json.JSONDecodeError, ValueError):
        pass

    return {"parse_error": True, "raw": str(raw)[:1000]}
