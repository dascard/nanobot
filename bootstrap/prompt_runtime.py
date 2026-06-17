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

    try:
        from core.settings_service import settings

        effective_engine = str(settings.get("prompt_runtime.engine", "v2") or "v2").strip().lower()
        if effective_engine == "v1":
            logger.warning(
                "Prompt Runtime 当前有效 engine=v1；这是显式回滚状态，请检查 DB setting 或 NANOBOT_PROMPT_ENGINE"
            )
    except Exception as exc:
        logger.warning("[PromptRuntime] failed to inspect effective engine: %s", exc)
