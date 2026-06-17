from __future__ import annotations

import logging
from typing import Any

from core.prompt_v2.template_loader import load_template
from core.prompt_v2.variables import render_scoped_template

logger = logging.getLogger("nanobot.prompt_v2.task_templates")


def render_task_prompt(prompt_key: str, values: dict[str, Any], *, fallback_text: str = "") -> str:
    try:
        template = load_template(prompt_key)
        rendered = render_scoped_template(template.prompt_key, template.body, values or {}).strip()
        return rendered or str(fallback_text or "")
    except Exception as exc:
        logger.warning("[PromptV2Task] render failed key=%s fallback=legacy error=%s", prompt_key, exc)
        return str(fallback_text or "")


def render_task_messages(
    prompt_key: str,
    values: dict[str, Any],
    *,
    fallback_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rendered = render_task_prompt(prompt_key, values, fallback_text="")
    if not rendered:
        return list(fallback_messages or [])
    return [{"role": "system", "content": rendered}]
