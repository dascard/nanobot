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
    delta_events = [item for item in events if item.get("status") == "delta"]
    assert delta_events == [{"status": "delta", "text": "你好"}]
    done_event = next(item for item in events if item.get("status") == "done")
    assert done_event["answer"] == "你好"
    assert done_event["reply"] == "你好"
    assert done_event["messages"] == [{"type": "text", "text": "你好"}]


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


def test_stream_chat_flushes_delta_before_progress(client):
    from unittest.mock import patch

    async def fake_handle_message(*args, **kwargs):
        queue = kwargs.get("stream_queue")
        assert queue is not None
        await queue.put({"status": "delta", "text": "你"})
        await queue.put({"status": "delta", "text": "好"})
        await queue.put({"status": "progress", "text": "正在调用工具"})
        await queue.put({"status": "delta", "text": "！"})
        return "你好！"

    with patch("api.routes.get_bridge") as mock_get_bridge:
        mock_get_bridge.return_value.handle_message.side_effect = fake_handle_message
        with client.stream(
            "POST",
            "/api/v1/chat",
            json={
                "user_id": "stream_progress_break_user",
                "session_id": "group_1000",
                "query": "test",
                "stream": True,
            },
        ) as response:
            body = "".join(response.iter_text())

    events = [
        json.loads(chunk[6:])
        for chunk in body.split("\n\n")
        if chunk.startswith("data: ")
    ]

    assert response.status_code == 200
    assert [
        (event.get("status"), event.get("text") or event.get("answer"))
        for event in events
        if event.get("status") in {"delta", "progress", "done"}
    ] == [
        ("delta", "你好"),
        ("progress", "正在调用工具"),
        ("delta", "！"),
        ("done", "你好！"),
    ]


def test_stream_chat_flushes_pending_delta_before_done(client):
    from unittest.mock import patch

    async def fake_handle_message(*args, **kwargs):
        queue = kwargs.get("stream_queue")
        assert queue is not None
        await queue.put({"status": "delta", "text": "最"})
        await queue.put({"status": "delta", "text": "后"})
        return "最后答案"

    with patch("api.routes.get_bridge") as mock_get_bridge:
        mock_get_bridge.return_value.handle_message.side_effect = fake_handle_message
        with client.stream(
            "POST",
            "/api/v1/chat",
            json={
                "user_id": "stream_flush_before_done_user",
                "session_id": "group_1000",
                "query": "test",
                "stream": True,
            },
        ) as response:
            body = "".join(response.iter_text())

    events = [
        json.loads(chunk[6:])
        for chunk in body.split("\n\n")
        if chunk.startswith("data: ")
    ]
    statuses = [event.get("status") for event in events]

    assert response.status_code == 200
    assert events[statuses.index("delta")] == {"status": "delta", "text": "最后"}
    assert statuses.index("delta") < statuses.index("done")
    assert events[statuses.index("done")]["answer"] == "最后答案"


def test_stream_chat_normalizes_final_replace_before_done(client):
    from unittest.mock import patch

    async def fake_handle_message(*args, **kwargs):
        queue = kwargs.get("stream_queue")
        assert queue is not None
        await queue.put({"status": "delta", "text": "草稿"})
        await queue.put({"status": "final", "text": "最终"})
        return "最终"

    with patch("api.routes.get_bridge") as mock_get_bridge:
        mock_get_bridge.return_value.handle_message.side_effect = fake_handle_message
        with client.stream(
            "POST",
            "/api/v1/chat",
            json={
                "user_id": "stream_final_user",
                "session_id": "group_1000",
                "query": "test",
                "stream": True,
            },
        ) as response:
            body = "".join(response.iter_text())

    events = [
        json.loads(chunk[6:])
        for chunk in body.split("\n\n")
        if chunk.startswith("data: ")
    ]
    final_index = next(i for i, item in enumerate(events) if item.get("status") == "final")
    done_index = next(i for i, item in enumerate(events) if item.get("status") == "done")

    assert response.status_code == 200
    assert events[final_index] == {
        "status": "final",
        "text": "最终",
        "replace": True,
        "source": "bridge",
    }
    assert final_index < done_index
    assert events[done_index]["answer"] == "最终"
