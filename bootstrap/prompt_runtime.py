"""Prompt 运行时目录初始化。"""

from __future__ import annotations

import logging


def init_prompt_runtimes(logger: logging.Logger) -> None:
    """初始化当前 canonical prompt runtime 目录。"""
    try:
        from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

        v2_result = init_prompt_v2_runtime_dir()
        if v2_result["copied"]:
            logger.info(
                "[PromptV2] initialized %d templates from %s -> %s",
                len(v2_result["copied"]),
                v2_result["source_dir"],
                v2_result["runtime_dir"],
            )
        else:
            logger.info("[PromptV2] runtime templates ready: %s", v2_result["runtime_dir"])
    except Exception as exc:
        logger.warning("[PromptV2] init_runtime_dir failed: %s", exc)

    try:
        from core.settings_service import settings

        effective_engine = str(settings.get("prompt_runtime.engine", "v2") or "v2").strip().lower()
        if effective_engine == "v1":
            logger.warning(
                "Prompt Runtime 当前有效 engine=v1；旧版运行时已下线，将使用 canonical prompt runtime"
            )
    except Exception as exc:
        logger.warning("[PromptRuntime] failed to inspect effective engine: %s", exc)
