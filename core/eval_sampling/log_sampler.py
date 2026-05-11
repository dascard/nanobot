"""日志采样——从日志文件增量扫描错误模式，生成 EvalCandidate。"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

# (regex, suite, description_prefix)
ERROR_PATTERNS: list[tuple[str, str, str]] = [
    (r"ActionFailed.*retcode=\d+.*message='([^']+)'", "error", "ActionFailed"),
    (r"tool_error.*\[([^\]]+)\].*ERROR:\s*(.+)", "error", "tool_error"),
    (r"HTTP\s+(400|401|403|404|500|502|503)\b", "error", "HTTP error"),
    (r"ECONNREFUSED\s+([^\s]+)", "error", "ECONNREFUSED"),
    (r"image_summary\s+(HTTP\s+\d+|failed|timeout)", "sticker", "image_summary error"),
    (r"sticker image\s+(404|500|failed|not found)", "sticker", "sticker image error"),
    (r"model.*route\s+failed|AllModelsFailed|switch", "model_routing", "model route failed"),
    (r"timed?\s*out|Timeout", "timing_gate", "timeout"),
    (r"send.*failed.*type=\w+\s+id=\d+", "reply_contract", "send failed"),
    (r"EMPTY RESPONSE", "reply_contract", "empty response"),
    (r"parse_error|invalid JSON|JSONDecodeError", "timing_gate", "parse error"),
    (r"preview.*failed|describe.*failed", "sticker", "sticker preview failure"),
    (r"blocked|moderation", "moderation", "moderation event"),
]


def sample_log_file(
    log_path: str,
    *,
    start_offset: int = 0,
    start_line: int = 0,
    limit: int = 100,
) -> tuple[list[dict], dict]:
    """增量扫描日志文件，返回 (candidates, new_cursor)。

    new_cursor = {"byte_offset": ..., "line_no": ...}
    """
    if not os.path.isfile(log_path):
        return [], {"byte_offset": 0, "line_no": 0}

    compiled = [(re.compile(pat, re.IGNORECASE), suite, desc) for pat, suite, desc in ERROR_PATTERNS]
    candidates: list[dict] = []
    seen: set[str] = set()

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(start_offset)
        lines = f.readlines()

    new_offset = start_offset + sum(len(line.encode("utf-8")) for line in lines)
    new_line = start_line + len(lines)

    for i, line in enumerate(lines):
        if len(candidates) >= limit:
            break
        stripped = line.strip()
        if not stripped:
            continue

        for pat, suite, desc_prefix in compiled:
            m = pat.search(stripped)
            if not m:
                continue

            key = f"{suite}:{m.group(0)[:80]}"
            if key in seen:
                continue
            seen.add(key)

            error_msg = (m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0))[:200]
            global_line = start_line + i + 1

            case_id = f"cand_log_{suite}_{hashlib.md5(key.encode()).hexdigest()[:8]}"
            input_data = {
                "log_line": stripped[:500],
                "error_type": desc_prefix,
                "error_message": error_msg,
            }
            fingerprint_raw = f"{suite}|{desc_prefix}|{error_msg[:200]}"
            fingerprint = hashlib.sha256(fingerprint_raw.encode("utf-8")).hexdigest()[:16]

            candidates.append({
                "case_id": case_id,
                "suite": suite,
                "source": "log",
                "source_ref": f"log:{log_path}:{global_line}",
                "description": f"{desc_prefix}: {error_msg}",
                "input": input_data,
                "expected": {"needs_label": True},
                "tags": ["sampled", "log_error", suite],
                "fingerprint": fingerprint,
            })
            break

    return candidates, {"byte_offset": new_offset, "line_no": new_line}
