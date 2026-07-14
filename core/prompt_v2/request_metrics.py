"""基于最终 Prompt envelope 的统一指纹与 Token 估算。"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from typing import Any

from core.prompt_v2.section_renderer import estimate_tokens, sha256_text, stable_json


@dataclass(frozen=True)
class PromptRequestMetrics:
    message_token_estimate: int
    tool_schema_token_estimate: int
    token_estimate: int
    prompt_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _snapshot_list(value: Any) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        return []
    return copy.deepcopy(list(value))


def calculate_request_metrics(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> PromptRequestMetrics:
    """只基于最终 ``messages + tools`` 计算稳定指标。

    provider、model、temperature、stream 等传输参数不属于 Prompt 指纹。
    缺失或非法的列表按空列表处理，使无工具计划与省略 tools 的出站请求
    使用相同口径。
    """

    message_snapshot = _snapshot_list(messages)
    tool_snapshot = _snapshot_list(tools)
    request_envelope = {
        "messages": message_snapshot,
        "tools": tool_snapshot,
    }
    message_tokens = (
        estimate_tokens(stable_json(message_snapshot))
        if message_snapshot
        else 0
    )
    tool_tokens = (
        estimate_tokens(stable_json(tool_snapshot))
        if tool_snapshot
        else 0
    )
    return PromptRequestMetrics(
        message_token_estimate=message_tokens,
        tool_schema_token_estimate=tool_tokens,
        token_estimate=message_tokens + tool_tokens,
        prompt_sha256=sha256_text(stable_json(request_envelope)),
    )
