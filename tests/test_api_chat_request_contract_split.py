from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api import routes
from api.routes import ChatProxyRequest


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_chat_request_contract_module_does_not_import_parent_routes_or_sync_awaitable():
    source = _source("api/chat_request_contract.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_chat_proxy_request_defaults_stay_compatible():
    req = ChatProxyRequest()

    assert req.user_id == "default_user"
    assert req.session_id == "default_session"
    assert req.query == ""
    assert req.files is None
    assert req.sender_name is None
    assert req.session_name is None
    assert req.stream is False
    assert req.classification_request is False
    assert req.merged_messages is None
    assert req.message_id is None
    assert req.source_message_ids is None
    assert req.client_meta is None


def test_parent_request_contract_wrappers_keep_api_routes_module():
    assert routes._clone_chat_request.__module__ == "api.routes"
    assert routes._resolve_push_target_id.__module__ == "api.routes"
    assert routes._extract_group_id_from_chat_request.__module__ == "api.routes"
    assert routes._chat_request_platform.__module__ == "api.routes"
    assert routes._chat_request_type.__module__ == "api.routes"
    assert routes._normalize_request_client_meta.__module__ == "api.routes"
    assert routes._private_prompt_audit_failure_meta.__module__ == "api.routes"
    assert routes._private_timing_meta.__module__ == "api.routes"


def test_clone_chat_request_preserves_all_request_contract_fields():
    req = ChatProxyRequest(
        user_id="u1",
        session_id="private_u1",
        query="原文",
        files=["img://a"],
        sender_name="用户",
        session_name="私聊",
        stream=True,
        classification_request=True,
        merged_messages=["a", "b"],
        message_id="m1",
        source_message_ids=["m0"],
        client_meta={"platform": "qq", "chat_type": "private"},
    )

    cloned = routes._clone_chat_request(req, query="合并后", files=["img://b"])

    assert isinstance(cloned, ChatProxyRequest)
    assert cloned is not req
    assert cloned.user_id == "u1"
    assert cloned.session_id == "private_u1"
    assert cloned.query == "合并后"
    assert cloned.files == ["img://b"]
    assert cloned.sender_name == "用户"
    assert cloned.session_name == "私聊"
    assert cloned.stream is True
    assert cloned.classification_request is True
    assert cloned.merged_messages == ["a", "b"]
    assert cloned.message_id == "m1"
    assert cloned.source_message_ids == ["m0"]
    assert cloned.client_meta == {"platform": "qq", "chat_type": "private"}


@pytest.mark.parametrize(
    ("req", "is_group", "expected"),
    [
        (ChatProxyRequest(user_id="u-private", session_id="private_u-private"), False, "u-private"),
        (ChatProxyRequest(user_id="u1", session_id="group_987654"), True, "987654"),
        (ChatProxyRequest(user_id="u1", session_id="987654"), True, "987654"),
        (ChatProxyRequest(user_id="u-fallback", session_id=""), True, "u-fallback"),
    ],
)
def test_resolve_push_target_id_keeps_private_and_group_contract(req, is_group, expected):
    assert routes._resolve_push_target_id(req, is_group) == expected


@pytest.mark.parametrize(
    ("req", "expected"),
    [
        (ChatProxyRequest(user_id="u1", session_id="group_987654"), "987654"),
        (ChatProxyRequest(user_id="u1", session_id="987654"), "987654"),
        (ChatProxyRequest(user_id="u-fallback", session_id=""), "u-fallback"),
    ],
)
def test_extract_group_id_from_chat_request_keeps_fallback_contract(req, expected):
    assert routes._extract_group_id_from_chat_request(req) == expected


@pytest.mark.parametrize(
    ("req", "expected"),
    [
        (ChatProxyRequest(client_meta=None), "qq"),
        (ChatProxyRequest(client_meta={}), "qq"),
        (ChatProxyRequest(client_meta={"platform": " QQ "}), "qq"),
        (ChatProxyRequest(client_meta={"platform": "Web"}), "web"),
        (SimpleNamespace(client_meta="bad-meta"), "qq"),
        (ChatProxyRequest(client_meta={"platform": "   "}), "qq"),
    ],
)
def test_chat_request_platform_defaults_and_normalizes(req, expected):
    assert routes._chat_request_platform(req) == expected


@pytest.mark.parametrize(
    ("session_id", "expected"),
    [
        ("private_u1", "private"),
        ("group_123", "group"),
        ("123", "group"),
        ("", "group"),
    ],
)
def test_chat_request_type_uses_private_prefix_only(session_id, expected):
    assert routes._chat_request_type(ChatProxyRequest(session_id=session_id)) == expected


def test_normalize_request_client_meta_writes_normalized_meta():
    req = ChatProxyRequest(
        user_id="u1",
        session_id="private_u1",
        client_meta={"platform": " QQ ", "chat_type": "private"},
    )

    normalized = routes._normalize_request_client_meta(req, expected_chat_type="private")

    assert normalized["platform"] == "qq"
    assert normalized["chat_type"] == "private"
    assert req.client_meta is normalized


def test_normalize_request_client_meta_maps_validation_error_to_http_400():
    req = ChatProxyRequest(client_meta={"chat_type": "group"})

    with pytest.raises(HTTPException) as exc_info:
        routes._normalize_request_client_meta(req, expected_chat_type="private")

    assert exc_info.value.status_code == 400
    assert "invalid client_meta" in str(exc_info.value.detail)


def test_private_prompt_audit_failure_meta_stays_exact():
    assert routes._private_prompt_audit_failure_meta() == {
        "kind": "empty_reply",
        "no_context": True,
        "no_send": True,
        "agent_result": "prompt_v2_audit_failed",
    }


def test_private_timing_meta_returns_none_for_missing_or_invalid_scoring():
    assert routes._private_timing_meta(None) is None
    assert routes._private_timing_meta(SimpleNamespace(timing_scoring=None)) is None
    assert routes._private_timing_meta(SimpleNamespace(timing_scoring="bad")) is None


def test_private_timing_meta_extracts_expected_fields():
    decision = SimpleNamespace(
        action="reply_now",
        effort="normal",
        intent="general_question",
        response_mode="agent",
        confidence=0.91,
        parse_quality="schema_valid",
        error_type=None,
        reason_code="clear_request",
        contract_version="private_decision_v2",
        task_run_id="taskrun_test",
        policy_mode="observation",
        policy_source="session:observation",
        proposed_action="no_reply",
        proposed_response_mode="none",
        runtime_preset="fast",
        timing_scoring={"score": 0.8},
    )

    assert routes._private_timing_meta(decision) == {
        "mode": "private",
        "action": "reply_now",
        "effort": "normal",
        "intent": "general_question",
        "response_mode": "agent",
        "confidence": 0.91,
        "parse_quality": "schema_valid",
        "error_type": None,
        "reason_code": "clear_request",
        "contract_version": "private_decision_v2",
        "task_run_id": "taskrun_test",
        "policy_mode": "observation",
        "policy_source": "session:observation",
        "proposed_action": "no_reply",
        "proposed_response_mode": "none",
        "runtime_preset": "fast",
        "scoring": {"score": 0.8},
    }
