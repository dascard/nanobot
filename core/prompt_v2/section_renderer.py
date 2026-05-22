from __future__ import annotations

import hashlib
import json
from typing import Any


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    other = max(0, len(text) - cjk - ascii_chars)
    return int(cjk * 1.0 + ascii_chars * 0.35 + other * 0.8)


def hash_section(section_hashes: dict[str, str], name: str, content: Any) -> None:
    section_hashes[name] = sha256_text(stable_json(content))


def system_message(content: str) -> dict[str, Any]:
    return {"role": "system", "content": str(content or "").strip()}
