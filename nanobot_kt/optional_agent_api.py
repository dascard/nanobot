"""仅在显式选择 KT Runtime 时解析上游 Agent API。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


_MANAGED_AGENT_TYPES: dict[type[Any], type[Any]] = {}


def _managed_agent_type(base_type: type[Any]) -> type[Any]:
    """隔离 KT 自带 Skill 发现，避免绕过 Nanobot 版本锁与作用域。"""

    cached = _MANAGED_AGENT_TYPES.get(base_type)
    if cached is not None:
        return cached

    class NanobotManagedKtAgent(base_type):  # type: ignore[misc, valid-type]
        def _init_skills(self) -> None:
            # KT 2.0 默认扫描 cwd、HOME、Agent 目录和 package manifest。
            # Nanobot 的 Skill 只能从受信发布目录或受管数据库按请求锁定，
            # 因而在可选 Adapter 初始化点关闭上游的平行发现与 slash 注入。
            self.skills = None
            self.skill_path_scanner = None
            session = getattr(self, "session", None)
            extra = getattr(session, "extra", None)
            if isinstance(extra, dict):
                extra.pop("skills_registry", None)

    NanobotManagedKtAgent.__name__ = "NanobotManagedKtAgent"
    NanobotManagedKtAgent.__qualname__ = "NanobotManagedKtAgent"
    _MANAGED_AGENT_TYPES[base_type] = NanobotManagedKtAgent
    return NanobotManagedKtAgent


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
    resolved_agent = (
        agent_factory
        if agent_factory is not None
        else _managed_agent_type(KtAgent)
    )
    return resolved_agent, config_loader or load_kt_agent_config


__all__ = ["resolve_kt_agent_api"]
