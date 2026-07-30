from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from api import routes


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_chat_helper_modules_do_not_import_parent_routes_or_sync_awaitable():
    for path in ("api/chat_content_helpers.py", "api/chat_response_contract.py"):
        source = _source(path)

        assert "from api.routes" not in source
        assert "import api.routes" not in source
        assert "asyncio.run" not in source
        assert "run_awaitable_sync" not in source


def test_legacy_parent_chat_helper_wrappers_keep_api_routes_module():
    assert routes._normalize_files.__module__ == "api.routes"
    assert routes._schedule_image_precache.__module__ == "api.routes"
    assert routes._build_guardrail_input.__module__ == "api.routes"
    assert routes._build_multimodal_user_input_text.__module__ == "api.routes"
    assert routes._build_chatlog_user_content.__module__ == "api.routes"
    assert routes._build_conversation_user_content.__module__ == "api.routes"
    assert routes._normalize_chat_stream_event.__module__ == "api.routes"
    assert routes._split_chat_answer_chunks.__module__ == "api.routes"
    assert routes._chat_response_payload.__module__ == "api.routes"


def test_chat_content_helpers_match_parent_facade():
    from api import chat_content_helpers

    files = ["", "  ", 42, "http://img.example/a.png", "token://b"]

    assert chat_content_helpers.normalize_files(files) == [
        "http://img.example/a.png",
        "token://b",
    ]
    assert routes._normalize_files(files) == chat_content_helpers.normalize_files(files)
    assert routes._build_guardrail_input("", files) == "[图片消息，共 2 张]"
    assert routes._build_guardrail_input("看看", files) == "看看\n[附带图片 2 张]"


def test_multimodal_user_input_text_keeps_existing_contract():
    assert routes._build_multimodal_user_input_text("你好", None) == "你好"
    assert (
        routes._build_multimodal_user_input_text("", ["img://a", "img://b"])
        == "[用户附带了 2 张图片，请结合图片内容理解并回答]"
    )
    assert (
        routes._build_multimodal_user_input_text("请看", ["img://a"])
        == "请看\n[用户附带了 1 张图片，请结合图片内容理解并回答]"
    )


def test_chatlog_and_conversation_content_keep_different_file_archive_contracts():
    files = ["http://img.example/a.png", "token://b"]

    chatlog_content = routes._build_chatlog_user_content("看看", files)
    conversation_content = routes._build_conversation_user_content("看看", files)

    assert chatlog_content == (
        "看看\n"
        "[图片附件 2 张]\n"
        "[图片1] http://img.example/a.png\n"
        "[图片2] token://b"
    )
    assert conversation_content == (
        "看看\n[用户附带了 2 张图片，请结合图片内容理解并回答]"
    )
    assert "http://img.example/a.png" not in conversation_content
    assert "token://b" not in conversation_content


def test_chat_stream_event_contract_is_available_through_parent_facade():
    from api import chat_response_contract

    assert routes._normalize_chat_stream_event({"status": "delta", "text": "你"}) == {
        "status": "delta",
        "text": "你",
    }
    assert routes._normalize_chat_stream_event({"status": "delta", "text": ""}) is None
    assert routes._normalize_chat_stream_event({"status": "final", "text": "完成"}) == {
        "status": "final",
        "text": "完成",
        "replace": True,
        "source": "bridge",
    }
    assert routes._normalize_chat_stream_event({"status": "progress", "step": "thinking"}) == {
        "status": "progress",
        "step": "thinking",
    }
    assert routes._normalize_chat_stream_event({"text": "missing status"}) is None
    assert routes._normalize_chat_stream_event({"status": "final", "text": "完成"}) == (
        chat_response_contract.normalize_chat_stream_event({"status": "final", "text": "完成"})
    )


def test_chat_sse_data_and_safe_error_event_contract():
    from api import chat_response_contract

    assert routes._chat_sse_data({"status": "delta", "text": "你好"}) == (
        'data: {"status": "delta", "text": "你好"}\n\n'
    )
    assert routes._stream_error_event() == {
        "status": "error",
        "message": routes.SAFE_STREAM_ERROR_MESSAGE,
    }
    assert routes._chat_sse_data(routes._stream_error_event()) == chat_response_contract.chat_sse_data(
        chat_response_contract.stream_error_event(routes.SAFE_STREAM_ERROR_MESSAGE)
    )


def test_chat_response_payload_contract_stays_compatible():
    req = SimpleNamespace(
        user_id="u1",
        session_id="private_u1",
        client_meta={"platform": "qq", "trace": {"request_id": "req-1"}},
    )

    payload = routes._chat_response_payload(
        req,
        status="ok",
        answer="第一段\n\n第二段",
        reply_meta={"send_mode": "reply", "_agent_result": "internal"},
        include_answer_chunks=True,
        guardrail_status="safe",
    )

    assert payload["status"] == "ok"
    assert payload["reply"] == "第一段\n\n第二段"
    assert payload["answer"] == payload["reply"]
    assert payload["messages"] == [{"type": "text", "text": "第一段\n\n第二段"}]
    assert payload["reply_meta"] == {"send_mode": "reply"}
    assert payload["answer_chunks"] == ["第一段", "第二段"]
    assert payload["meta"]["user_id"] == "u1"
    assert payload["meta"]["session_id"] == "private_u1"
    assert payload["meta"]["platform"] == "qq"
    assert payload["meta"]["chat_type"] == "private"
    assert payload["meta"]["request_id"] == "req-1"
    assert payload["meta"]["guardrail_status"] == "safe"
