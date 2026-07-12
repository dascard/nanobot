"""Prompt 运行时目录初始化。"""

from __future__ import annotations

import logging


def init_prompt_runtimes(logger: logging.Logger) -> None:
    """初始化当前 canonical prompt runtime 目录。"""
    try:
        from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

        runtime_result = init_prompt_v2_runtime_dir()
        if runtime_result["copied"]:
            logger.info(
                "[PromptRuntime] initialized %d templates from %s -> %s",
                len(runtime_result["copied"]),
                runtime_result["source_dir"],
                runtime_result["runtime_dir"],
            )
        else:
            logger.info("[PromptRuntime] runtime templates ready: %s", runtime_result["runtime_dir"])
        if runtime_result.get("migrated"):
            logger.info(
                "[PromptRuntime] migrated %d legacy identity placeholders",
                len(runtime_result["migrated"]),
            )
    except Exception as exc:
        logger.warning("[PromptRuntime] init_runtime_dir failed: %s", exc)

    try:
        from core.settings_service import settings

        effective_engine = str(settings.get("prompt_runtime.engine", "prompt") or "prompt").strip().lower()
        if effective_engine == "v1":
            logger.warning(
                "Prompt Runtime 当前有效 engine=v1；旧版运行时已下线，将使用 canonical prompt runtime"
            )
    except Exception as exc:
        logger.warning("[PromptRuntime] failed to inspect effective engine: %s", exc)
