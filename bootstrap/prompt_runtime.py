"""Prompt 运行时目录初始化。"""

from __future__ import annotations

import logging


def init_prompt_runtimes(logger: logging.Logger) -> None:
    """初始化 managed prompt 和 legacy rollback prompt 的运行时目录。"""
    try:
        from core.prompts.manager import PromptManager

        init_result = PromptManager.init_runtime_dir()
        if init_result["copied"]:
            logger.info(
                "[PromptManager] initialized %d templates from %s -> %s",
                len(init_result["copied"]),
                init_result["source_dir"],
                init_result["runtime_dir"],
            )
        else:
            logger.info("[PromptManager] runtime dir ready: %s", init_result["runtime_dir"])
    except Exception as exc:
        logger.warning("[PromptManager] init_runtime_dir failed: %s", exc)

    try:
        from core.legacy_prompt_runtime import init_legacy_prompt_runtime_dir

        legacy_result = init_legacy_prompt_runtime_dir()
        if legacy_result["copied"]:
            logger.info(
                "[LegacyPrompt] initialized %d fragments from %s -> %s",
                len(legacy_result["copied"]),
                legacy_result["source_dir"],
                legacy_result["runtime_dir"],
            )
        else:
            logger.info("[LegacyPrompt] runtime fragments ready: %s", legacy_result["runtime_dir"])
    except Exception as exc:
        logger.warning("[LegacyPrompt] init_runtime_dir failed: %s", exc)
