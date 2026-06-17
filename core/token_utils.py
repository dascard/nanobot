"""Shared rough token estimation utilities."""

from __future__ import annotations


def is_cjk_char(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    value = str(text)
    cjk = sum(1 for ch in value if is_cjk_char(ch))
    ascii_chars = sum(1 for ch in value if ord(ch) < 128)
    other = max(0, len(value) - cjk - ascii_chars)
    return int(cjk * 1.0 + ascii_chars * 0.35 + other * 0.8)
