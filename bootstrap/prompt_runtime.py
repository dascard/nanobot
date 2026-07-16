"""Prompt 运行时目录初始化。"""

from __future__ import annotations

import logging


def init_prompt_runtimes(logger: logging.Logger) -> None:
    """初始化当前 canonical prompt runtime 目录。"""
    try:
        from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir
        from core.prompt_v2.flow import load_flow, validate_runtime_contract

        runtime_result = init_prompt_v2_runtime_dir()
        active_flow = load_flow()
        validate_runtime_contract(active_flow.flow)
    except Exception:
        logger.exception("[PromptRuntime] init_runtime_dir failed")
        raise

    if runtime_result["copied"]:
        logger.info(
            "[PromptRuntime] initialized %d templates from %s -> %s",
            len(runtime_result["copied"]),
            runtime_result["source_dir"],
            runtime_result["runtime_dir"],
        )
    else:
        logger.info("[PromptRuntime] runtime templates ready: %s", runtime_result["runtime_dir"])
    if runtime_result.get("flow_migrated"):
        logger.info(
            "[PromptRuntime] runtime flow migrated: backup=%s",
            runtime_result.get("flow_backup_path", ""),
        )
    if runtime_result.get("migrated"):
        logger.info(
            "[PromptRuntime] migrated %d legacy identity placeholders",
            len(runtime_result["migrated"]),
        )
    recovery = dict(runtime_result.get("template_recovery") or {})
    if recovery.get("status") not in {None, "clean"}:
        logger.warning(
            "[PromptRuntime] template migration recovered: status=%s operation_id=%s",
            recovery.get("status", ""),
            recovery.get("operation_id", ""),
        )
    for audit in runtime_result.get("template_audit", []):
        drift_status = str(audit.get("drift_status") or "")
        if drift_status == "in_sync":
            continue
        logger.warning(
            "[PromptRuntime] template drift: key=%s status=%s",
            audit.get("template_key", ""),
            drift_status,
        )
    for status in runtime_result.get("task_contracts", []):
        invalid_sources = list(status.get("invalid_sources") or [])
        if not invalid_sources:
            continue
        logger.warning(
            "[PromptRuntime] task contract %s fallback source=%s invalid=%s",
            status.get("task_key", ""),
            status.get("source", ""),
            ",".join(invalid_sources),
        )
    logger.info(
        "[PromptRuntime] active flow validated: source=%s path=%s",
        active_flow.source,
        active_flow.path,
    )

    try:
        from core.settings_service import settings

        effective_engine = str(settings.get("prompt_runtime.engine", "prompt") or "prompt").strip().lower()
        if effective_engine == "v1":
            logger.warning(
                "Prompt Runtime 当前有效 engine=v1；旧版运行时已下线，将使用 canonical prompt runtime"
            )
    except Exception as exc:
        logger.warning("[PromptRuntime] failed to inspect effective engine: %s", exc)
