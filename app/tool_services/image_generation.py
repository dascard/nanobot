"""图片生成工具的框架无关输入、持久化与结果编排。"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
from collections.abc import Callable
from typing import Any

from core.generated_images import save_generated_image
from core.tool_contracts.result import ToolServiceResult


logger = logging.getLogger("nanobot.app.tool.image_generation")
ALLOWED_IMAGE_SIZES = frozenset(
    {"1024x1024", "1024x1536", "1536x1024", "auto"}
)
ALLOWED_IMAGE_QUALITIES = frozenset(
    {"low", "medium", "high", "auto"}
)
ALLOWED_IMAGE_BACKGROUNDS = frozenset(
    {"auto", "transparent", "opaque"}
)
GenerateCallable = Callable[..., dict[str, Any]]


def normalize_image_option(
    value: Any,
    allowed: frozenset[str],
    default: str,
) -> str:
    item = str(value or default).strip()
    return item if item in allowed else default


async def execute_image_generation(
    args: dict[str, Any],
    *,
    generate: GenerateCallable,
    model: str,
    prompt_max_chars: int,
) -> ToolServiceResult:
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return ToolServiceResult(error="prompt is required")
    if len(prompt) > prompt_max_chars:
        return ToolServiceResult(
            error=(
                f"prompt too long: {len(prompt)} > "
                f"{prompt_max_chars}"
            )
        )

    size = normalize_image_option(
        args.get("size"),
        ALLOWED_IMAGE_SIZES,
        "1024x1024",
    )
    quality = normalize_image_option(
        args.get("quality"),
        ALLOWED_IMAGE_QUALITIES,
        "high",
    )
    background = normalize_image_option(
        args.get("background"),
        ALLOWED_IMAGE_BACKGROUNDS,
        "auto",
    )
    try:
        result = await asyncio.to_thread(
            generate,
            prompt=prompt,
            size=size,
            quality=quality,
            background=background,
        )
        saved = save_generated_image(
            result["image_b64"],
            prompt=prompt,
            metadata={
                "model": model,
                "size": size,
                "quality": quality,
                "background": background,
            },
        )
        payload = {
            "mime": "image/png",
            "model": model,
            "size": size,
            "quality": quality,
            "background": background,
            "reply_token": saved["reply_token"],
            "saved_path": saved["path"],
            "image_bytes": saved["bytes"],
            "revised_prompt": result.get("revised_prompt", ""),
            "text_output": result.get("text_output", ""),
            "usage_hint": (
                "把 reply_token 原样放进 reply(content) 即可发送图片，不要改写 token。"
            ),
        }
        return ToolServiceResult(
            output=json.dumps(payload, ensure_ascii=False),
            exit_code=0,
        )
    except urllib.error.URLError as exc:
        logger.error(
            "[image_generation] request failed: %s",
            exc,
            exc_info=True,
        )
        return ToolServiceResult(
            error=f"Image generation failed: {exc}"
        )
    except Exception as exc:
        logger.error(
            "[image_generation] failed: %s",
            exc,
            exc_info=True,
        )
        return ToolServiceResult(
            error=f"Image generation failed: {exc}"
        )


__all__ = [
    "ALLOWED_IMAGE_BACKGROUNDS",
    "ALLOWED_IMAGE_QUALITIES",
    "ALLOWED_IMAGE_SIZES",
    "GenerateCallable",
    "execute_image_generation",
    "normalize_image_option",
]
