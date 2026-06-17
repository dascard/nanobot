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


def test_chat_step_returns_tool_call(client, monkeypatch):
    calls = []

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
    async def fake_chat_completion(self, **kwargs):
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
                                    "arguments": '{"price": 0.8}',
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

    with client.stream(
        "POST",
        "/api/v1/chat-step",
        json=_step_request(stream=True),
        headers={"Accept": "text/event-stream"},
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert 'data: {"status": "progress"' in body
    assert '"status": "tool_call"' in body
    assert '"name": "synergy.energy.load_types"' in body
