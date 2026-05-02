#!/usr/bin/env python3
"""Build creatures/nanobot/prompt.md from composable system prompt fragments.

Usage: python scripts/build_nanobot_prompt.py [--check]

Fragments in creatures/nanobot/prompts/system/ are concatenated in filename order.
Output goes to creatures/nanobot/prompt.md.
With --check, exits non-zero if the output differs from existing file (CI guard).
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAGMENTS_DIR = os.path.join(PROJECT_ROOT, "creatures", "nanobot", "prompts", "system")
OUTPUT_FILE = os.path.join(PROJECT_ROOT, "creatures", "nanobot", "prompt.md")


def _iter_fragments():
    """Yield (name, content) tuples sorted by filename."""
    if not os.path.isdir(FRAGMENTS_DIR):
        sys.exit(f"Fragments directory not found: {FRAGMENTS_DIR}")
    for fname in sorted(os.listdir(FRAGMENTS_DIR)):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(FRAGMENTS_DIR, fname)
        with open(fpath, "r", encoding="utf-8") as fh:
            content = fh.read().strip()
        yield fname, content


def build_prompt() -> str:
    fragments = list(_iter_fragments())
    if not fragments:
        sys.exit(f"No .md fragments found in {FRAGMENTS_DIR}")
    parts = []
    for i, (fname, content) in enumerate(fragments):
        parts.append(content)
        # Ensure exactly one blank line between fragments
        if i < len(fragments) - 1:
            parts.append("")
    return "\n".join(parts) + "\n"


def main():
    check_mode = "--check" in sys.argv
    generated = build_prompt()

    if check_mode:
        if not os.path.exists(OUTPUT_FILE):
            sys.exit(f"Output file missing: {OUTPUT_FILE}")
        with open(OUTPUT_FILE, "r", encoding="utf-8") as fh:
            existing = fh.read()
        if generated != existing:
            print("ERROR: prompt.md is stale. Run: python scripts/build_nanobot_prompt.py", file=sys.stderr)
            sys.exit(1)
        print("OK: prompt.md matches generated output.")
        return

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        fh.write(generated)
    print(f"Built {OUTPUT_FILE} from {len(list(_iter_fragments()))} fragments.")


if __name__ == "__main__":
    main()
