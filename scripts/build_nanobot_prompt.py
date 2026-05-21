#!/usr/bin/env python3
"""Build creatures/nanobot/prompt.md from composable system prompt fragments.

Usage: python scripts/build_nanobot_prompt.py [--check] [--chat-type base|group|private]

Fragments in creatures/nanobot/prompts/system/ are concatenated in filename order.
Fragment prefixes determine which chat type they apply to:
  base  (00_ 05_ 10_ 30_)       common base prompt
  group (20_ 25_)                only for --chat-type group
  private (26_)                  only for --chat-type private

Default --chat-type base generates prompt.md.
--chat-type group generates prompt_group.md.
--chat-type private generates prompt_private.md.
With --check and --chat-type, checks the corresponding output file.
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAGMENTS_DIR = os.path.join(PROJECT_ROOT, "creatures", "nanobot", "prompts", "system")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "creatures", "nanobot")

BASE_PREFIXES = ("00_", "05_", "10_", "30_")
GROUP_PREFIXES = ("20_", "25_")
PRIVATE_PREFIXES = ("26_",)
LEGACY_TOOL_PREFIXES = ("27_", "40_", "60_")

OUTPUT_FILES = {
    "base": "prompt.md",
    "group": "prompt_group.md",
    "private": "prompt_private.md",
}


def _iter_fragments(chat_type: str = "all"):
    """Yield (name, content) tuples sorted by filename, filtered by chat_type."""
    if chat_type == "all":
        allowed = BASE_PREFIXES + GROUP_PREFIXES + PRIVATE_PREFIXES + LEGACY_TOOL_PREFIXES
    elif chat_type == "base":
        allowed = BASE_PREFIXES
    elif chat_type == "group":
        allowed = BASE_PREFIXES + GROUP_PREFIXES
    elif chat_type == "private":
        allowed = BASE_PREFIXES + PRIVATE_PREFIXES
    else:
        raise ValueError(f"Unknown chat_type: {chat_type}")

    if not os.path.isdir(FRAGMENTS_DIR):
        sys.exit(f"Fragments directory not found: {FRAGMENTS_DIR}")
    for fname in sorted(os.listdir(FRAGMENTS_DIR)):
        if not fname.endswith(".md"):
            continue
        if not any(fname.startswith(p) for p in allowed):
            continue
        fpath = os.path.join(FRAGMENTS_DIR, fname)
        with open(fpath, "r", encoding="utf-8") as fh:
            content = fh.read().strip()
        yield fname, content


def build_prompt(chat_type: str = "base") -> str:
    fragments = list(_iter_fragments(chat_type))
    if not fragments:
        sys.exit(f"No .md fragments found in {FRAGMENTS_DIR} for chat_type={chat_type}")
    parts = []
    for i, (fname, content) in enumerate(fragments):
        parts.append(content)
        if i < len(fragments) - 1:
            parts.append("")
    return "\n".join(parts) + "\n"


def _check_file(output_path: str, expected: str, label: str) -> bool:
    if not os.path.exists(output_path):
        print(f"ERROR: {label} missing: {output_path}", file=sys.stderr)
        return False
    with open(output_path, "r", encoding="utf-8") as fh:
        existing = fh.read()
    if expected != existing:
        print(f"ERROR: {label} is stale. Run: python scripts/build_nanobot_prompt.py", file=sys.stderr)
        return False
    print(f"OK: {label} matches generated output.")
    return True


def main():
    check_mode = "--check" in sys.argv
    chat_type = "base"
    for arg in sys.argv:
        if arg.startswith("--chat-type="):
            chat_type = arg.split("=", 1)[1]
            if chat_type not in ("base", "group", "private"):
                sys.exit(f"Unknown --chat-type: {chat_type}")

    output_file = os.path.join(OUTPUT_DIR, OUTPUT_FILES[chat_type])
    generated = build_prompt(chat_type)

    if check_mode:
        ok = _check_file(output_file, generated, f"prompt ({chat_type})")
        sys.exit(0 if ok else 1)

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as fh:
        fh.write(generated)
    fragment_count = len(list(_iter_fragments(chat_type)))
    print(f"Built {output_file} ({chat_type}) from {fragment_count} fragments.")


if __name__ == "__main__":
    main()
