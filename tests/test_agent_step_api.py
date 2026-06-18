import json


def _step_request(stream: bool = False) -> dict:
    return {
        "protocol": "agent-step.v1",
        "run_id": "run_1",
        "input": {"user_message": "上周哪种负荷类型能耗最高？"},
        "tools": [
            {
                "name": "synergy.energy.load_types",
                "description": "按负荷类型汇总能耗、碳排和成本。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "ef": {"type": "number"},
                        "price": {"type": "number"},
                    },
                    "additionalProperties": False,
                },
            }
        ],
        "tool_results": [],
        "instructions": {
            "language": "zh-CN",
            "artifact_policy": "side_panel",
            "do_not_fabricate": True,
        },
        "client_meta": {
            "app": "synergy-opt",
            "conversation_id": "conv_1",
            "request_id": "req_1",
        },
        "stream": stream,
    }


def _sse_events(body: str) -> list[dict]:
    events = []
    for block in body.strip().split("\n\n"):
        if not block.startswith("data: "):
            continue
        events.append(json.loads(block.removeprefix("data: ")))
    return events


def test_chat_step_returns_tool_call(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "core.agent_step.NANOBOT_AGENT_STEP_MODEL",
        "fixed-agent-step-model",
    )

    async def fake_chat_completion(self, **kwargs):
        calls.append(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "synergy.energy.load_types",
                                    "arguments": '{"ef": 0.57, "price": 0.8}',
                                },
                            }
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr(
        "core.agent_step.NewAPIClient.chat_completion",
        fake_chat_completion,
    )

    response = client.post("/api/v1/chat-step", json=_step_request())

    assert response.status_code == 200
    assert response.json() == {
        "protocol": "agent-step.v1",
        "run_id": "run_1",
        "status": "tool_call",
        "tool_calls": [
            {
                "id": "call_1",
                "name": "synergy.energy.load_types",
                "arguments": {"ef": 0.57, "price": 0.8},
            }
        ],
    }
    assert calls
    assert calls[0]["tools"][0]["function"]["name"] == "synergy.energy.load_types"
    assert calls[0]["tools"][0]["function"]["parameters"]["properties"]["ef"]["type"] == "number"
    assert calls[0]["manual_model"] == "fixed-agent-step-model"


def test_chat_step_returns_final_answer(client, monkeypatch):
    async def fake_chat_completion(self, **kwargs):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "status": "final",
                                "answer": "Medium_Load 类型能耗最高。",
                                "suggested_questions": ["查看 Medium_Load 小时峰值？"],
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(
        "core.agent_step.NewAPIClient.chat_completion",
        fake_chat_completion,
    )

    response = client.post("/api/v1/chat-step", json=_step_request())

    assert response.status_code == 200
    assert response.json() == {
        "protocol": "agent-step.v1",
        "run_id": "run_1",
        "status": "final",
        "answer": "Medium_Load 类型能耗最高。",
        "suggested_questions": ["查看 Medium_Load 小时峰值？"],
    }


def test_chat_step_stream_reuses_sse_framing(client, monkeypatch):
    calls = []

    async def fake_chat_completion_stream(self, **kwargs):
        calls.append(kwargs)
        yield {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "synergy.energy.load_types",
                                    "arguments": "",
                                },
                            }
                        ]
                    }
                }
            ]
        }
        yield {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {
                                    "arguments": '{"price": 0.8}',
                                },
                            }
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr(
        "core.agent_step.NewAPIClient.chat_completion_stream",
        fake_chat_completion_stream,
    )

    with client.stream(
        "POST",
        "/api/v1/chat-step",
        json=_step_request(stream=True),
        headers={"Accept": "text/event-stream"},
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(body)
    assert [event["status"] for event in events] == ["progress", "tool_call"]
    assert events[1]["tool_calls"] == [
        {
            "id": "call_1",
            "name": "synergy.energy.load_types",
            "arguments": {"price": 0.8},
        }
    ]
    assert calls[0]["max_tokens"] == 1200


def test_chat_step_stream_accumulates_split_tool_name(client, monkeypatch):
    async def fake_chat_completion_stream(self, **kwargs):
        yield {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "synergy.energy.",
                                },
                            }
                        ]
                    }
                }
            ]
        }
        yield {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {
                                    "name": "load_types",
                                    "arguments": '{"ef": 0.57}',
                                },
                            }
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr(
        "core.agent_step.NewAPIClient.chat_completion_stream",
        fake_chat_completion_stream,
    )

    with client.stream(
        "POST",
        "/api/v1/chat-step",
        json=_step_request(stream=True),
        headers={"Accept": "text/event-stream"},
    ) as response:
        events = _sse_events("".join(response.iter_text()))

    assert response.status_code == 200
    assert events[-1]["status"] == "tool_call"
    assert events[-1]["tool_calls"][0]["name"] == "synergy.energy.load_types"
    assert events[-1]["tool_calls"][0]["arguments"] == {"ef": 0.57}


def test_chat_step_stream_emits_final_answer_deltas(client, monkeypatch):
    req = _step_request(stream=True)
    req["tool_results"] = [
        {
            "id": "call_1",
            "name": "synergy.energy.load_types",
            "status": "success",
            "summary": "Maximum_Load 占比最高。",
            "data": {"types": [{"load_type": "Maximum_Load", "percent": 44.91}]},
        }
    ]

    async def fake_chat_completion_stream(self, **kwargs):
        yield {"choices": [{"delta": {"content": "Maximum_Load 占比"}}]}
        yield {"choices": [{"delta": {"content": "最高，约 44.9%。"}}]}

    monkeypatch.setattr(
        "core.agent_step.NewAPIClient.chat_completion_stream",
        fake_chat_completion_stream,
    )

    with client.stream(
        "POST",
        "/api/v1/chat-step",
        json=req,
        headers={"Accept": "text/event-stream"},
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    events = _sse_events(body)
    assert [event["status"] for event in events] == ["progress", "delta", "delta", "final"]
    assert events[1]["content"] == "Maximum_Load 占比"
    assert "text" not in events[1]
    assert events[2]["content"] == "最高，约 44.9%。"
    assert "text" not in events[2]
    assert events[-1]["answer"] == "Maximum_Load 占比最高，约 44.9%。"


def test_chat_step_stream_without_tools_prompts_for_natural_text(client, monkeypatch):
    req = _step_request(stream=True)
    req["tools"] = []
    calls = []

    async def fake_chat_completion_stream(self, **kwargs):
        calls.append(kwargs)
        yield {"choices": [{"delta": {"content": "可以直接回答。"}}]}

    monkeypatch.setattr(
        "core.agent_step.NewAPIClient.chat_completion_stream",
        fake_chat_completion_stream,
    )

    with client.stream(
        "POST",
        "/api/v1/chat-step",
        json=req,
        headers={"Accept": "text/event-stream"},
    ) as response:
        events = _sse_events("".join(response.iter_text()))

    assert response.status_code == 200
    system_prompt = calls[0]["messages"][0]["content"]
    assert "如果无需工具或已有 tool_results 足够回答" in system_prompt
    assert [event["status"] for event in events] == ["progress", "delta", "final"]
    assert events[-1]["answer"] == "可以直接回答。"
