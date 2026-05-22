#!/usr/bin/env python3
"""从 legacy system fragments 生成 managed prompt 默认模板候选。

默认只打印到 stdout，不覆盖文件。使用 `--write` 才会更新
`prompts.default/group_chat.md` 和 `prompts.default/private_chat.md`。
"""

from __future__ import annotations

import argparse
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_DIR = PROJECT_ROOT / "creatures" / "nanobot" / "prompts" / "system"
DEFAULT_DIR = PROJECT_ROOT / "prompts.default"


COMMON = ("00_identity.md", "05_core.md", "10_chat_style.md", "30_tool_discipline.md")
GROUP = ("20_group_rules.md", "25_context_control.md")
PRIVATE = ("26_private_behavior.md",)
TOOL_POLICY = ("27_tool_routing.md", "40_memory_policy.md", "60_artifact_passthrough.md")


def _read(name: str) -> str:
    return (SYSTEM_DIR / name).read_text(encoding="utf-8").strip()


def _template(name: str, description: str, fragments: tuple[str, ...]) -> str:
    body = "\n\n".join(_read(name) for name in fragments)
    return (
        "---\n"
        f"name: {name}\n"
        "version: 2\n"
        f"description: {description}\n"
        "---\n"
        f"{body}\n"
    )


def build_group() -> str:
    return _template(
        "群聊回复",
        "由 legacy fragments 迁移生成的群聊 managed 模板候选。",
        COMMON + GROUP + TOOL_POLICY,
    )


def build_private() -> str:
    return _template(
        "私聊回复",
        "由 legacy fragments 迁移生成的私聊 managed 模板候选。",
        COMMON + PRIVATE + TOOL_POLICY,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="覆盖 prompts.default 中的 managed 模板")
    args = parser.parse_args()

    outputs = {
        "group_chat.md": build_group(),
        "private_chat.md": build_private(),
    }
    if args.write:
        DEFAULT_DIR.mkdir(parents=True, exist_ok=True)
        for name, content in outputs.items():
            (DEFAULT_DIR / name).write_text(content, encoding="utf-8")
            print(f"wrote {DEFAULT_DIR / name}")
        return

    for name, content in outputs.items():
        print(f"===== {name} =====")
        print(content)


if __name__ == "__main__":
    main()
