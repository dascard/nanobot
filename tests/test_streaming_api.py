import json
import time


def test_stream_chat_forwards_delta_events(client):
    from unittest.mock import patch

    async def fake_handle_message(*args, **kwargs):
        queue = kwargs.get("stream_queue")
        assert queue is not None
        await queue.put({"status": "progress", "text": "正在处理..."})
        await queue.put({"status": "delta", "text": "你"})
        await queue.put({"status": "delta", "text": "好"})
        return "你好"

    with patch("api.routes.get_bridge") as mock_get_bridge:
        mock_get_bridge.return_value.handle_message.side_effect = fake_handle_message
        with client.stream(
            "POST",
            "/api/v1/chat",
            json={
                "user_id": "stream_delta_user",
                "session_id": "group_1000",
                "query": "test",
                "stream": True,
            },
        ) as response:
            body = "".join(response.iter_text())

    events = []
    for chunk in body.split("\n\n"):
        if not chunk.startswith("data: "):
            continue
        events.append(json.loads(chunk[6:]))

    assert response.status_code == 200
    assert {"status": "progress", "text": "正在处理..."} in events
    assert {"status": "delta", "text": "你"} in events
    assert {"status": "delta", "text": "好"} in events
    assert {"status": "done", "answer": "你好"} in events


def test_stream_chat_done_does_not_wait_for_heartbeat(client):
    from unittest.mock import patch

    async def fake_handle_message(*args, **kwargs):
        return "立即完成"

    with patch("api.routes.get_bridge") as mock_get_bridge:
        mock_get_bridge.return_value.handle_message.side_effect = fake_handle_message
        started = time.perf_counter()
        with client.stream(
            "POST",
            "/api/v1/chat",
            json={
                "user_id": "stream_fast_user",
                "session_id": "group_1000",
                "query": "test",
                "stream": True,
            },
        ) as response:
            body = "".join(response.iter_text())
        elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert '"status": "done"' in body
    assert elapsed < 2.0


def test_stream_chat_error_event_hides_internal_details(client):
    from unittest.mock import patch

    async def fake_handle_message(*args, **kwargs):
        raise RuntimeError("sqlite path /srv/nanobot.db leaked-secret")

    with patch("api.routes.get_bridge") as mock_get_bridge:
        mock_get_bridge.return_value.handle_message.side_effect = fake_handle_message
        with client.stream(
            "POST",
            "/api/v1/chat",
            json={
                "user_id": "stream_error_user",
                "session_id": "group_1000",
                "query": "test",
                "stream": True,
            },
        ) as response:
            body = "".join(response.iter_text())

    events = []
    for chunk in body.split("\n\n"):
        if not chunk.startswith("data: "):
            continue
        events.append(json.loads(chunk[6:]))

    assert response.status_code == 200
    assert {"status": "error", "message": "系统暂时不可用，请稍后再试"} in events
    assert "nanobot.db" not in body
    assert "leaked-secret" not in body
