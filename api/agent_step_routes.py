"""普通 API Agent Step 与遗留渲染路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response
from fastapi.responses import StreamingResponse

from api.common_auth import verify_token
from core.agent_step import (
    AgentStepRequest,
    agent_step_event_payload,
    run_agent_step,
    run_agent_step_stream,
    sse_data as agent_step_sse_data,
)
from core.lifecycle import (
    COMPATIBILITY_REGISTRY,
    record_compatibility_usage,
)

router = APIRouter(tags=["agent-step"])


@router.get("/render")
async def render_markdown(text: str, response: Response):
    """遗留端点，已弃用。目前直接内嵌 base64 返回"""
    descriptor = COMPATIBILITY_REGISTRY.require("endpoint.render")
    record_compatibility_usage(descriptor.compatibility_id)
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = (
        f'<{descriptor.canonical_replacement}>; '
        'rel="successor-version"'
    )
    return {"status": "deprecated"}


@router.post("/chat-step", dependencies=[Depends(verify_token)])
async def chat_step(req: AgentStepRequest, accept: str = Header(default="")):
    """SynergyOpt 等外部编排方使用的 HTTP 半 ReAct step/resume 端点。"""
    wants_stream = req.stream or "text/event-stream" in str(accept or "").lower()

    if wants_stream:
        async def _event_stream():
            yield agent_step_sse_data({
                "status": "progress",
                "text": "正在判断需要的业务工具...",
            })
            async for event in run_agent_step_stream(req):
                yield agent_step_sse_data(event)

        return StreamingResponse(_event_stream(), media_type="text/event-stream")

    response = await run_agent_step(req)
    return agent_step_event_payload(response)
