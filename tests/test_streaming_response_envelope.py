import json
from unittest.mock import patch

from tests.test_api import _fast_private_reply


def test_stream_chat_done_includes_standard_envelope_and_reply_meta(client, monkeypatch):
    _fast_private_reply(monkeypatch)

    class FakeBridge:
        async def handle_message(self, *args, **kwargs):
            return "流式最终答案"

        def pop_last_reply_meta(self, session_id):
            assert session_id == "private_stream_envelope_user"
            return {"send_mode": "normal", "_agent_result": "ok"}

    with patch("api.routes.get_bridge", return_value=FakeBridge()):
        with client.stream(
            "POST",
            "/api/v1/chat",
            json={
                "user_id": "stream_envelope_user",
                "session_id": "private_stream_envelope_user",
                "query": "流式信封",
                "stream": True,
                "client_meta": {"platform": "web"},
            },
        ) as response:
            body = "".join(response.iter_text())

    events = []
    for chunk in body.split("\n\n"):
        if chunk.startswith("data: "):
            events.append(json.loads(chunk[6:]))
    done_event = next(item for item in events if item.get("status") == "done")

    assert response.status_code == 200
    assert done_event["answer"] == "流式最终答案"
    assert done_event["reply"] == "流式最终答案"
    assert done_event["messages"] == [{"type": "text", "text": "流式最终答案"}]
    assert done_event["reply_meta"] == {"send_mode": "normal"}
    assert done_event["meta"]["user_id"] == "stream_envelope_user"
    assert done_event["meta"]["session_id"] == "private_stream_envelope_user"
    assert done_event["meta"]["platform"] == "web"
    assert done_event["meta"]["chat_type"] == "private"


def test_stream_chat_done_answer_remains_authoritative_when_delta_differs(client, monkeypatch):
    _fast_private_reply(monkeypatch)

    class FakeBridge:
        async def handle_message(self, *args, **kwargs):
            queue = kwargs.get("stream_queue")
            assert queue is not None
            await queue.put({"status": "delta", "text": "草稿"})
            return "最终回复"

        def pop_last_reply_meta(self, session_id):
            assert session_id == "private_stream_done_authority_user"
            return {"send_mode": "normal"}

    with patch("api.routes.get_bridge", return_value=FakeBridge()):
        with client.stream(
            "POST",
            "/api/v1/chat",
            json={
                "user_id": "stream_done_authority_user",
                "session_id": "private_stream_done_authority_user",
                "query": "流式权威",
                "stream": True,
                "client_meta": {"platform": "web"},
            },
        ) as response:
            body = "".join(response.iter_text())

    events = [
        json.loads(chunk[6:])
        for chunk in body.split("\n\n")
        if chunk.startswith("data: ")
    ]
    delta_event = next(item for item in events if item.get("status") == "delta")
    done_event = next(item for item in events if item.get("status") == "done")

    assert response.status_code == 200
    assert delta_event["text"] == "草稿"
    assert done_event["answer"] == "最终回复"
    assert done_event["reply"] == "最终回复"
    assert done_event["messages"] == [{"type": "text", "text": "最终回复"}]
