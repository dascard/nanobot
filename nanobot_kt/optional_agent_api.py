"""仅在显式选择 KT Runtime 时解析上游 Agent API。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def resolve_kt_agent_api(
    agent_factory: Any | None,
    config_loader: Callable[[str], Any] | None,
) -> tuple[Any, Callable[[str], Any]]:
    if agent_factory is not None and config_loader is not None:
        return agent_factory, config_loader
    try:
        from kohakuterrarium.core.agent import Agent as KtAgent
        from kohakuterrarium.core.config import (
            load_agent_config as load_kt_agent_config,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "KT Runtime 未安装；请安装可选依赖后再显式启用"
        ) from exc
    return agent_factory or KtAgent, config_loader or load_kt_agent_config


__all__ = ["resolve_kt_agent_api"]
