from __future__ import annotations

from pathlib import Path

from api import routes
from api.chat_request_contract import ChatProxyRequest


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_chat_push_envelope_module_does_not_import_parent_routes_or_sync_awaitable():
    source = _source("api/chat_push_envelope.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
    assert "push_envelope_to_qq(" not in source
    assert "_persist_chat_turn(" not in source
    assert "_finalize_private_buffer(" not in source
    assert "get_bridge(" not in source


def test_build_chat_push_envelope_private_target_and_meta_contract():
    from api.chat_push_envelope import build_chat_push_envelope

    req = ChatProxyRequest(
        user_id="u-private",
        session_id="private_u-private",
        query="hello",
        client_meta={"platform": "web", "trace": {"request_id": "req-1"}},
    )

    built = build_chat_push_envelope(
        req,
        answer="推送正文",
        platform="web",
        chat_type="private",
        is_group=False,
        reply_meta={"send_mode": "quote", "_agent_result": "hidden"},
    )

    assert built.target_type == "private"
    assert built.target_id == "u-private"
    assert built.envelope["status"] == "ok"
    assert built.envelope["reply"] == "推送正文"
    assert built.envelope["messages"] == [{"type": "text", "text": "推送正文"}]
    assert built.envelope["reply_meta"] == {"send_mode": "quote"}
    assert built.envelope["meta"]["user_id"] == "u-private"
    assert built.envelope["meta"]["session_id"] == "private_u-private"
    assert built.envelope["meta"]["platform"] == "web"
    assert built.envelope["meta"]["chat_type"] == "private"
    assert built.envelope["meta"]["target_type"] == "private"
    assert built.envelope["meta"]["target_id"] == "u-private"


def test_build_chat_push_envelope_group_target_uses_request_contract():
    from api.chat_push_envelope import build_chat_push_envelope

    prefixed = ChatProxyRequest(
        user_id="u1",
        session_id="group_987654",
        query="group",
    )
    bare = ChatProxyRequest(
        user_id="u1",
        session_id="987654",
        query="group",
    )

    built_prefixed = build_chat_push_envelope(
        prefixed,
        answer="群回复",
        platform="qq",
        chat_type="group",
        is_group=True,
    )
    built_bare = build_chat_push_envelope(
        bare,
        answer="群回复",
        platform="qq",
        chat_type="group",
        is_group=True,
    )

    assert built_prefixed.target_type == "group"
    assert built_prefixed.target_id == "987654"
    assert built_prefixed.envelope["meta"]["target_id"] == "987654"
    assert built_bare.target_type == "group"
    assert built_bare.target_id == "987654"
    assert built_bare.envelope["meta"]["target_id"] == "987654"


def test_expand_chat_transport_answer_disables_base64(monkeypatch):
    from api import chat_push_envelope

    calls: list[tuple[str, bool]] = []

    def fake_expand(content: str, *, allow_base64: bool = True) -> str:
        calls.append((content, allow_base64))
        return "展开后的 CQ 图片"

    monkeypatch.setattr(
        "core.generated_images.expand_generated_image_refs_in_content",
        fake_expand,
    )

    assert (
        chat_push_envelope.expand_chat_transport_answer("原始 [generated_image:1]")
        == "展开后的 CQ 图片"
    )
    assert calls == [("原始 [generated_image:1]", False)]


def test_parent_chat_push_envelope_wrappers_remain_in_routes():
    assert routes._expand_chat_transport_answer.__module__ == "api.routes"
    assert routes._build_chat_push_envelope.__module__ == "api.routes"
    assert routes._chat_response_payload.__module__ == "api.routes"
