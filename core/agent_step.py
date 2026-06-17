"""Agent Step HTTP 半 ReAct 协议实现。"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from clients.new_api_client import NewAPIClient
from config import NANOBOT_AGENT_STEP_MODEL, NEW_API_BASE_URL, NEW_API_KEY


AGENT_STEP_PROTOCOL = "agent-step.v1"


class AgentStepInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_message: str = ""


class AgentStepInstructions(BaseModel):
    model_config = ConfigDict(extra="ignore")

    language: str = "zh-CN"
    artifact_policy: str = "side_panel"
    do_not_fabricate: bool = True


class AgentStepClientMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    app: str = ""
    conversation_id: str = ""
    request_id: str = ""


class AgentStepTool(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    display_name: str = ""


class AgentStepReference(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dataset_id: str | None = None
    field: str | None = None
    source: str = ""


class AgentStepArtifact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str = ""
    artifact_id: str = ""
    title: str = ""
    render_target: str = "side_panel"
    metadata: dict[str, Any] | None = None


class AgentStepError(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    message: str
    root_cause: str = ""
    retry: str = ""
    stop: bool = False


class AgentStepToolResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    status: str = "success"
    summary: str = ""
    data: Any = None
    error: AgentStepError | None = None
    references: list[AgentStepReference] = Field(default_factory=list)
    artifacts: list[AgentStepArtifact] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class AgentStepToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentStepRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    protocol: str = AGENT_STEP_PROTOCOL
    run_id: str
    input: AgentStepInput | None = None
    tools: list[AgentStepTool] = Field(default_factory=list)
    tool_results: list[AgentStepToolResult] = Field(default_factory=list)
    instructions: AgentStepInstructions = Field(default_factory=AgentStepInstructions)
    client_meta: AgentStepClientMeta = Field(default_factory=AgentStepClientMeta)
    stream: bool = False


class AgentStepResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    protocol: str = AGENT_STEP_PROTOCOL
    run_id: str
    status: str
    tool_calls: list[AgentStepToolCall] = Field(default_factory=list)
    answer: str | None = None
    suggested_questions: list[str] = Field(default_factory=list)
    error: AgentStepError | None = None


def _error_response(
    req: AgentStepRequest,
    *,
    code: str,
    message: str,
    root_cause: str,
    retry: str,
    stop: bool = False,
) -> AgentStepResponse:
    return AgentStepResponse(
        run_id=req.run_id,
        status="error",
        error=AgentStepError(
            code=code,
            message=message,
            root_cause=root_cause,
            retry=retry,
            stop=stop,
        ),
    )


def _openai_tools(req: AgentStepRequest) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for tool in req.tools:
        parameters = tool.input_schema or {"type": "object", "properties": {}}
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": parameters,
                },
            }
        )
    return tools


def _messages(req: AgentStepRequest) -> list[dict[str, str]]:
    instructions = req.instructions
    system = (
        "你是 agent-step.v1 的推理节点。你不能执行工具，也不能访问调用方 API 或数据库。\n"
        "如果需要业务数据，必须使用本轮 tools schema 中的原生 function call 选择工具和参数。\n"
        "如果已有 tool_results 足够回答，必须只输出 JSON："
        '{"status":"final","answer":"...","suggested_questions":["..."]}。\n'
        f"回复语言：{instructions.language}。artifact_policy={instructions.artifact_policy}。"
        f"do_not_fabricate={instructions.do_not_fabricate}。"
    )
    user_message = req.input.user_message if req.input else ""
    payload = {
        "user_message": user_message,
        "tool_results": [item.model_dump(exclude_none=True) for item in req.tool_results],
        "client_meta": req.client_meta.model_dump(exclude_none=True),
    }
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False),
        },
    ]


def _first_message(result: dict[str, Any]) -> dict[str, Any]:
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices:
        return {}
    first = choices[0]
    if not isinstance(first, dict):
        return {}
    message = first.get("message")
    return message if isinstance(message, dict) else {}


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_tool_calls(req: AgentStepRequest, message: dict[str, Any]) -> AgentStepResponse | None:
    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list) or not raw_calls:
        return None

    allowed = {tool.name for tool in req.tools}
    calls: list[AgentStepToolCall] = []
    for index, raw_call in enumerate(raw_calls, start=1):
        if not isinstance(raw_call, dict):
            continue
        function = raw_call.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        if name not in allowed:
            return _error_response(
                req,
                code="unknown_tool",
                message=f"模型请求了未授权工具：{name}",
                root_cause="tool_not_in_request_schema",
                retry="只从本轮 tools schema 中选择工具。",
                stop=True,
            )
        calls.append(
            AgentStepToolCall(
                id=str(raw_call.get("id") or f"call_{index}"),
                name=name,
                arguments=_parse_arguments(function.get("arguments")),
            )
        )

    if not calls:
        return None
    return AgentStepResponse(run_id=req.run_id, status="tool_call", tool_calls=calls)


def _parse_final_content(req: AgentStepRequest, content: Any) -> AgentStepResponse:
    text = str(content or "").strip()
    if not text:
        return _error_response(
            req,
            code="empty_model_response",
            message="模型未返回 tool_call 或 final answer。",
            root_cause="empty_message_content",
            retry="重新发起 step 请求。",
            stop=False,
        )

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return AgentStepResponse(run_id=req.run_id, status="final", answer=text)

    if not isinstance(payload, dict):
        return AgentStepResponse(run_id=req.run_id, status="final", answer=text)

    status = str(payload.get("status") or "final")
    if status == "tool_call" and isinstance(payload.get("tool_calls"), list):
        calls = []
        allowed = {tool.name for tool in req.tools}
        for index, item in enumerate(payload["tool_calls"], start=1):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            if name not in allowed:
                return _error_response(
                    req,
                    code="unknown_tool",
                    message=f"模型请求了未授权工具：{name}",
                    root_cause="tool_not_in_request_schema",
                    retry="只从本轮 tools schema 中选择工具。",
                    stop=True,
                )
            calls.append(
                AgentStepToolCall(
                    id=str(item.get("id") or f"call_{index}"),
                    name=name,
                    arguments=_parse_arguments(item.get("arguments")),
                )
            )
        if calls:
            return AgentStepResponse(run_id=req.run_id, status="tool_call", tool_calls=calls)

    answer = str(payload.get("answer") or payload.get("content") or "").strip()
    suggested_questions = payload.get("suggested_questions")
    if not isinstance(suggested_questions, list):
        suggested_questions = []
    return AgentStepResponse(
        run_id=req.run_id,
        status="final",
        answer=answer or text,
        suggested_questions=[str(item).strip() for item in suggested_questions if str(item).strip()],
    )


def normalize_agent_step_response(req: AgentStepRequest, result: dict[str, Any]) -> AgentStepResponse:
    if result.get("error"):
        return _error_response(
            req,
            code="llm_error",
            message=str(result.get("error")),
            root_cause="new_api_chat_completion_failed",
            retry="稍后重试或检查模型网关配置。",
            stop=False,
        )

    message = _first_message(result)
    tool_call_response = _parse_tool_calls(req, message)
    if tool_call_response is not None:
        return tool_call_response
    return _parse_final_content(req, message.get("content"))


async def run_agent_step(req: AgentStepRequest) -> AgentStepResponse:
    if req.protocol != AGENT_STEP_PROTOCOL:
        return _error_response(
            req,
            code="invalid_protocol",
            message=f"不支持的协议：{req.protocol}",
            root_cause="protocol_mismatch",
            retry=f"使用 {AGENT_STEP_PROTOCOL} 重新请求。",
            stop=True,
        )

    client = NewAPIClient(api_key=NEW_API_KEY, base_url=NEW_API_BASE_URL)
    result = await client.chat_completion(
        messages=_messages(req),
        tools=_openai_tools(req),
        temperature=0.2,
        model_tier="smart",
        manual_model=NANOBOT_AGENT_STEP_MODEL,
        max_tokens=1200,
        llm_source="agent_step",
    )
    return normalize_agent_step_response(req, result)


def agent_step_event_payload(response: AgentStepResponse) -> dict[str, Any]:
    payload = response.model_dump(exclude_none=True)
    if not payload.get("tool_calls"):
        payload.pop("tool_calls", None)
    if not payload.get("suggested_questions"):
        payload.pop("suggested_questions", None)
    return payload


def sse_data(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
