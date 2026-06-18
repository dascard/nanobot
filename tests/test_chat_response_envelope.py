from unittest.mock import patch

from tests.test_api import _fast_private_reply


def test_proxy_chat_returns_standard_envelope_and_filtered_reply_meta(client, monkeypatch):
    _fast_private_reply(monkeypatch)

    class FakeBridge:
        async def handle_message(self, *args, **kwargs):
            return "标准回复"

        def pop_last_reply_meta(self, session_id):
            assert session_id == "private_envelope_user"
            return {
                "send_mode": "quote",
                "reply_to_message_id": "m-source",
                "mentions": ["10001"],
                "_agent_result": "ok",
                "_no_reply": True,
            }

    with patch("api.routes.get_bridge", return_value=FakeBridge()):
        response = client.post(
            "/api/v1/chat",
            json={
                "user_id": "envelope_user",
                "session_id": "private_envelope_user",
                "query": "生成标准信封",
                "client_meta": {"platform": "web"},
            },
        )

    data = response.json()
    assert response.status_code == 200
    assert data["status"] == "ok"
    assert data["answer"] == "标准回复"
    assert data["reply"] == "标准回复"
    assert data["messages"] == [{"type": "text", "text": "标准回复"}]
    assert data["reply_meta"] == {
        "send_mode": "quote",
        "reply_to_message_id": "m-source",
        "mentions": ["10001"],
    }
    assert data["meta"]["user_id"] == "envelope_user"
    assert data["meta"]["session_id"] == "private_envelope_user"
    assert data["meta"]["platform"] == "web"
    assert data["meta"]["chat_type"] == "private"
    assert data["meta"]["unprocessed_logs"] >= 0


def test_proxy_chat_no_reply_returns_empty_standard_envelope(client, monkeypatch):
    from core.private_timing import PrivateDecision

    class NoReplyGate:
        async def classify(self, *args, **kwargs):
            return PrivateDecision(
                "no_reply",
                "unit_test_no_reply",
                1.0,
                "unit_test",
                complexity=0,
                effort="silent",
                runtime_preset="none",
            )

    monkeypatch.setattr("core.private_timing.get_private_gate", lambda: NoReplyGate())

    response = client.post(
        "/api/v1/chat",
        json={
            "user_id": "empty_envelope_user",
            "session_id": "private_empty_envelope_user",
            "query": "不用回复",
            "client_meta": {"platform": "qq"},
        },
    )

    data = response.json()
    assert response.status_code == 200
    assert data["status"] == "no_reply"
    assert data["user_id"] == "empty_envelope_user"
    assert data["reply"] == ""
    assert data["messages"] == []
    assert data["reply_meta"] == {}
    assert data["meta"]["platform"] == "qq"
    assert data["meta"]["chat_type"] == "private"
