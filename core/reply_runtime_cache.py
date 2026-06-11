"""Reply 工具运行时缓存兜底。

当 KT conversation 因 interrupt 时序或竞态条件未写入 tool result 时，
Bridge 可通过此缓存回退提取 reply 输出，避免误判 no_tool_call。
"""

from __future__ import annotations

from contextvars import ContextVar

_LAST_REPLY_TEXT: ContextVar[str] = ContextVar("last_reply_text", default="")
_LAST_REPLY_META: ContextVar[dict] = ContextVar("last_reply_meta", default={})


def set_last_reply(text: str, meta: dict | None = None) -> None:
    """在 reply 工具执行完成后写入缓存。"""
    _LAST_REPLY_TEXT.set(str(text or ""))
    _LAST_REPLY_META.set(dict(meta or {}))


def get_last_reply() -> tuple[str, dict]:
    """读取最近一次 reply 缓存。"""
    return _LAST_REPLY_TEXT.get(""), dict(_LAST_REPLY_META.get({}) or {})


def clear_last_reply() -> None:
    """清空缓存——每轮 handle_message 开始时调用。"""
    _LAST_REPLY_TEXT.set("")
    _LAST_REPLY_META.set({})
