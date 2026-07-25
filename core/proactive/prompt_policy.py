"""主动外呼任务 Prompt 的 canonical 调用策略。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.model_provider.route_registry import list_model_route_descriptors
from core.prompt_v2.task_templates import render_task_messages


OUTREACH_TASK_KEYS = frozenset(
    descriptor.route_key
    for descriptor in list_model_route_descriptors()
    if descriptor.domain == "proactive_outreach"
    and descriptor.runtime_task_key is not None
)


def invoke_outreach_task(
    caller: Callable[..., Any],
    *,
    task_key: str,
    payload: str,
) -> Any:
    """按 Task Registry 渲染 system/user 消息并调用模型路由。"""

    if task_key not in OUTREACH_TASK_KEYS:
        raise ValueError(f"未登记的主动外呼任务: {task_key}")
    messages = render_task_messages(
        task_key,
        {"message": payload},
        fallback_messages=[],
    )
    if (
        len(messages) != 2
        or messages[0].get("role") != "system"
        or messages[1].get("role") != "user"
    ):
        raise RuntimeError(f"主动外呼任务 {task_key} 未生成有效消息合同")
    system_prompt = str(messages[0].get("content") or "")
    user_message = str(messages[1].get("content") or "")
    if not system_prompt or not user_message:
        raise RuntimeError(f"主动外呼任务 {task_key} 渲染结果为空")
    return caller(
        route_key=task_key,
        messages=messages,
        system_prompt=system_prompt,
        user_message=user_message,
    )


__all__ = ["OUTREACH_TASK_KEYS", "invoke_outreach_task"]
