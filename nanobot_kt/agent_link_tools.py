"""把 Agent Link 动态前端能力投影为 KT 原生工具。"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from kohakuterrarium.llm.message import ImagePart, TextPart
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolResult,
)

from core.agent_link.runtime import (
    AgentLinkSessionKey,
    AgentLinkToolCaller,
    AgentLinkToolDefinition,
    AgentLinkToolFailure,
)


if TYPE_CHECKING:
    from kohakuterrarium.llm.message import ContentPart


def _json_text(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def _multimodal_result(value: object) -> str | list["ContentPart"]:
    """识别 MeaPet 截图结果，避免把 base64 再作为普通文本发给模型。"""

    if not isinstance(value, Mapping):
        return _json_text(value)
    image = value.get("image")
    if not isinstance(image, Mapping):
        return _json_text(value)
    mime_type = str(
        image.get("mime_type") or image.get("media_type") or ""
    ).strip().lower()
    data = image.get("data")
    if (
        mime_type not in {"image/jpeg", "image/png", "image/webp"}
        or not isinstance(data, str)
        or not data
    ):
        return _json_text(value)

    summary = copy.deepcopy(dict(value))
    summary["image"] = {
        "mime_type": mime_type,
        "inline": True,
    }
    return [
        TextPart(text=_json_text(summary)),
        ImagePart(
            url=f"data:{mime_type};base64,{data}",
            detail="high",
            source_type="agent_link_tool",
            source_name="meapet.capture_screen",
        ),
    ]


class AgentLinkProxyTool(BaseTool):
    """通过当前 Agent Link 长连接调用 MeaPet 前端能力。"""

    def __init__(
        self,
        key: AgentLinkSessionKey,
        definition: AgentLinkToolDefinition,
        runtime: AgentLinkToolCaller,
    ) -> None:
        super().__init__()
        self._key = key
        self._definition = definition
        self._runtime = runtime

    @property
    def tool_name(self) -> str:
        return self._definition.name

    @property
    def description(self) -> str:
        return self._definition.description

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(
        self,
        args: dict[str, Any],
        **_kwargs: Any,
    ) -> ToolResult:
        try:
            result = await self._runtime.call_tool(
                self._key,
                self.tool_name,
                args,
            )
        except AgentLinkToolFailure as exc:
            payload = exc.to_payload()
            return ToolResult(
                output=_json_text(payload),
                error=_json_text(payload),
                metadata={
                    "agent_link": True,
                    "code": exc.code,
                    "retryable": exc.retryable,
                },
            )
        return ToolResult(
            output=_multimodal_result(result),
            exit_code=0,
            metadata={"agent_link": True},
        )


def build_agent_link_tools(
    key: AgentLinkSessionKey,
    definitions: Sequence[AgentLinkToolDefinition],
    *,
    runtime: AgentLinkToolCaller,
) -> tuple[AgentLinkProxyTool, ...]:
    """用当前完整快照构造请求级动态工具实例。"""

    return tuple(
        AgentLinkProxyTool(key, definition, runtime)
        for definition in definitions
    )


__all__ = [
    "AgentLinkProxyTool",
    "build_agent_link_tools",
]
