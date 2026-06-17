from __future__ import annotations

import hashlib
import json
from typing import Any

from core.token_utils import estimate_tokens


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def hash_section(section_hashes: dict[str, str], name: str, content: Any) -> None:
    section_hashes[name] = sha256_text(stable_json(content))


def system_message(content: str) -> dict[str, Any]:
    return {"role": "system", "content": str(content or "").strip()}
