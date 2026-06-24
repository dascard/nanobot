"""从日志文件抽取错误/异常事件，生成候选 regression case。

用法:
    python -m evals.sample_from_logs --log data/nanobot.log --out evals/cases/candidates/errors --limit 50
"""
from __future__ import annotations

import argparse
import json
import os
import re

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


def _scan_log(log_path: str, limit: int) -> list[dict]:
    if not os.path.isfile(log_path):
        print(f"Log file not found: {log_path}")
        return []

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    compiled = [(re.compile(pat, re.IGNORECASE), suite, desc) for pat, suite, desc in ERROR_PATTERNS]
    cases: list[dict] = []
    seen: set[str] = set()

    for i, line in enumerate(lines):
        if len(cases) >= limit:
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

            ctx_start = max(0, i - 3)
            ctx_end = min(len(lines), i + 4)
            context = [line.strip()[:300] for line in lines[ctx_start:ctx_end]]
            error_msg = (m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0))[:200]

            cases.append({
                "id": f"candidate_log_error_{len(cases) + 1:04d}",
                "suite": suite,
                "description": f"{desc_prefix}: {error_msg}",
                "input": {
                    "log_line": stripped[:500],
                    "context": context,
                    "error_type": desc_prefix,
                    "error_message": error_msg,
                },
                "expected": {"needs_label": True},
                "tags": ["sampled", "log_error", suite],
                "source": "log",
                "source_ref": f"log:{log_path}:{i + 1}",
            })
            break

    return cases


def main():
    parser = argparse.ArgumentParser(description="从日志抽取错误候选 eval case")
    parser.add_argument("--log", default="data/nanobot.log")
    parser.add_argument("--out", default="evals/cases/candidates/errors")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    cases = _scan_log(args.log, args.limit)

    for case in cases:
        fpath = os.path.join(args.out, f"{case['id']}.json")
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(case, f, indent=2, ensure_ascii=False)

    print(f"[log] {len(cases)} error candidates → {args.out}")


if __name__ == "__main__":
    main()
