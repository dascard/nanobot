"""聊天图片预缓存调度 helper。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from api import chat_content_helpers


logger = logging.getLogger("nanobot.chat_media_precache")


def schedule_image_precache(
    background_tasks: Any,
    files: Any,
    *,
    source_type: str,
    source_name_prefix: str,
    normalize_files: Callable[[Any], list[str]] = chat_content_helpers.normalize_files,
    precache_image_sources: Callable[..., Any] | None = None,
) -> None:
    normalized_files = normalize_files(files)
    if not normalized_files or background_tasks is None:
        return

    if precache_image_sources is None:
        from core.media_preprocess_runtime import (
            ImagePrecacheRuntimeUnavailableError,
            get_image_precache_port,
        )

        try:
            precache_image_sources_func = get_image_precache_port().precache
        except ImagePrecacheRuntimeUnavailableError:
            logger.warning(
                "图片预缓存运行时未启动，跳过旁路任务: source_type=%s source_name_prefix=%s",
                source_type,
                source_name_prefix,
            )
            return
        normalized_files = tuple(normalized_files)
    else:
        precache_image_sources_func = precache_image_sources

    background_tasks.add_task(
        precache_image_sources_func,
        normalized_files,
        source_type=source_type,
        source_name_prefix=source_name_prefix,
    )
