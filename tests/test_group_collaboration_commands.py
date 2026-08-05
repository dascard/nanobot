from __future__ import annotations

import pytest

from app.group_ingress.collaboration_commands import (
    GroupCollaborationCommandKind,
    parse_group_collaboration_command,
)


def _request(
    *,
    message: str,
    message_id: str,
    sender_id: str = "group-user",
):
    from api.group_message_routes import GroupMessageRequest

    return GroupMessageRequest(
        group_id="collaboration-group",
        sender_id=sender_id,
        sender_name="协作测试用户",
        message=message,
        message_id=message_id,
        client_meta={"platform": "qq", "chat_type": "group"},
    )


@pytest.mark.parametrize(
    ("raw", "kind", "fields"),
    [
        (
            "@agent:meapet board-1 research",
            GroupCollaborationCommandKind.INVITE,
            {"client_id": "meapet", "board_id": "board-1", "task_id": "research"},
        ),
        (
            "@agent 状态 board-1",
            GroupCollaborationCommandKind.STATUS,
            {"board_id": "board-1"},
        ),
        (
            f"@agent 审批 board-1 delivery-1 {'a' * 64}",
            GroupCollaborationCommandKind.APPROVE,
            {"delivery_id": "delivery-1", "delivery_sha256": "a" * 64},
        ),
        (
            f"@agent 拒绝 board-1 delivery-1 {'b' * 64} evidence_missing",
            GroupCollaborationCommandKind.REJECT,
            {
                "delivery_id": "delivery-1",
                "delivery_sha256": "b" * 64,
                "reason_code": "evidence_missing",
            },
        ),
    ],
)
def test_group_collaboration_parser_accepts_only_frozen_grammar(
    raw,
    kind,
    fields,
):
    command = parse_group_collaboration_command(raw)

    assert command is not None
    assert command.kind is kind
    for name, expected in fields.items():
        assert getattr(command, name) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "@agent",
        "请 @agent:meapet board-1 research",
        "@agent:MeaPet board-1 research",
        "@agent 状态",
        "@agent 状态 board-1 额外文本",
        "@agent 审批 board-1 delivery-1 not-a-sha",
        f"@agent 拒绝 board-1 delivery-1 {'a' * 64} 原因含空格",
    ],
)
def test_group_collaboration_parser_rejects_ambiguous_chat(raw):
    assert parse_group_collaboration_command(raw) is None


@pytest.mark.asyncio
async def test_disabled_collaboration_command_keeps_existing_timing_path(
    db_session,
    monkeypatch,
):
    from app.group_ingress.service import GroupIngressService

    calls: list[str] = []

    class Runtime:
        async def process_message(self, *_args, **_kwargs):
            calls.append("timing")
            return {
                "action": "no_reply",
                "reason": "collaboration_disabled_fallback",
                "generation": 7,
            }

        def note_bot_replied(self, *_args, **_kwargs):
            raise AssertionError("no_reply 不应记录 bot 回复")

    monkeypatch.setattr(
        "core.agent_collaboration.is_agent_collaboration_requested",
        lambda: False,
    )
    monkeypatch.setattr("core.timing_runtime.get_group_runtime", Runtime)

    result = await GroupIngressService(db=db_session).handle(
        _request(
            message="@agent 状态 board-disabled",
            message_id="group-collaboration-disabled",
        )
    )

    assert calls == ["timing"]
    assert result["action"] == "no_reply"
    assert result["reason"] == "collaboration_disabled_fallback"
    assert result["generation"] == 7


@pytest.mark.asyncio
async def test_enabled_exact_command_bypasses_model_and_persists_reply(
    db_session,
    monkeypatch,
):
    from app.group_ingress.service import GroupIngressService
    from core.database import ChatLog, ConversationTurn, InboundMessageClaim

    calls: list[str] = []

    class Runtime:
        async def process_message(self, *_args, **_kwargs):
            raise AssertionError("严格协作命令不应进入 Timing 或模型")

        def note_bot_replied(self, group_id):
            calls.append(str(group_id))

    monkeypatch.setattr(
        "core.agent_collaboration.is_agent_collaboration_requested",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.group_ingress.service.is_super_user_id",
        lambda _sender_id: False,
    )
    monkeypatch.setattr("core.timing_runtime.get_group_runtime", Runtime)

    result = await GroupIngressService(db=db_session).handle(
        _request(
            message="@agent 状态 board-private",
            message_id="group-collaboration-enabled",
        )
    )

    assert result["action"] == "continue"
    assert "仅配置的超级用户" in result["reply"]
    assert result["reason"] == "agent_collaboration_command"
    assert calls == ["collaboration-group"]
    assert db_session.query(ChatLog).filter_by(role="assistant").one().content == (
        result["reply"]
    )
    assert db_session.query(ConversationTurn).count() == 2
    claim = db_session.query(InboundMessageClaim).one()
    assert claim.status == "completed"
