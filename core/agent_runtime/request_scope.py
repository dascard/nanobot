"""Agent 单次请求的受信运行时上下文。"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from types import MappingProxyType
from typing import Any


_CURRENT_RUNTIME_CONTEXT: ContextVar[Mapping[str, Any] | None] = ContextVar(
    "nanobot_agent_runtime_context",
    default=None,
)
_CURRENT_RUNTIME_DRY_RUN: ContextVar[bool] = ContextVar(
    "nanobot_agent_runtime_dry_run",
    default=False,
)


def get_current_runtime_context() -> Mapping[str, Any] | None:
    """读取当前异步请求的不可变身份上下文。"""

    return _CURRENT_RUNTIME_CONTEXT.get()


def is_runtime_request_dry_run() -> bool:
    """返回当前异步 Runtime 请求是否禁止写入副作用。"""

    return _CURRENT_RUNTIME_DRY_RUN.get()


def set_runtime_request_dry_run(dry_run: bool) -> Token[bool]:
    return _CURRENT_RUNTIME_DRY_RUN.set(bool(dry_run))


def reset_runtime_request_dry_run(token: Token[bool]) -> None:
    _CURRENT_RUNTIME_DRY_RUN.reset(token)


def set_current_runtime_context(
    context: Mapping[str, Any],
) -> Token[Mapping[str, Any] | None]:
    """绑定请求上下文；子协程和 ``asyncio.to_thread`` 会继承该值。"""

    return _CURRENT_RUNTIME_CONTEXT.set(
        MappingProxyType({str(key): value for key, value in context.items()})
    )


def reset_current_runtime_context(
    token: Token[Mapping[str, Any] | None],
) -> None:
    _CURRENT_RUNTIME_CONTEXT.reset(token)


@contextmanager
def runtime_context_scope(
    context: Mapping[str, Any],
) -> Iterator[Mapping[str, Any]]:
    token = set_current_runtime_context(context)
    try:
        current = get_current_runtime_context()
        if current is None:  # pragma: no cover - ContextVar 不变量
            raise RuntimeError("Agent 请求上下文绑定失败")
        yield current
    finally:
        reset_current_runtime_context(token)


def require_current_runtime_context() -> dict[str, Any]:
    """缺少请求级绑定时失败关闭，不回退读取 KT 全局 Session。"""

    context = get_current_runtime_context()
    if context is None:
        raise RuntimeError("缺少受信 Agent 请求上下文")
    return dict(context)


__all__ = [
    "get_current_runtime_context",
    "is_runtime_request_dry_run",
    "require_current_runtime_context",
    "reset_current_runtime_context",
    "reset_runtime_request_dry_run",
    "runtime_context_scope",
    "set_current_runtime_context",
    "set_runtime_request_dry_run",
]
