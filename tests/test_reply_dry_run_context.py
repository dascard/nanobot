"""ReplyTool 请求级 dry-run 上下文回归测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.async_helpers import run_async


@pytest.mark.asyncio
async def test_public_reply_execute_records_sticker_outside_dry_run(monkeypatch):
    """普通请求仍应通过公开 ReplyTool.execute 记录贴纸使用。"""
    from creatures.nanobot.prompts.skills.reply.tool import ReplyTool

    recorded = []
    monkeypatch.setattr(
        "core.sticker_memory.expand_sticker_refs_in_content",
        lambda content: content,
    )
    monkeypatch.setattr(
        "core.sticker_memory.record_sticker_uses_in_content",
        lambda content: recorded.append(content),
    )

    result = await ReplyTool().execute(
        {"content": "普通回复 [sticker:test]"},
    )

    assert result.success
    assert recorded == ["普通回复 [sticker:test]"]


@pytest.mark.asyncio
async def test_bridge_request_dry_run_context_is_isolated_between_tasks():
    """并发 Bridge 请求必须各自读取自己的 dry-run 值。"""
    from nanobot_kt.request_scope import BridgeRequestScope, is_request_dry_run

    entered = asyncio.Queue()
    release = asyncio.Event()

    async def observe(dry_run):
        async with BridgeRequestScope(
            asyncio.Lock(),
            MagicMock(),
            dry_run=dry_run,
        ):
            await entered.put(None)
            await release.wait()
            before_yield = is_request_dry_run()
            await asyncio.sleep(0)
            return before_yield, is_request_dry_run()

    dry_task = asyncio.create_task(observe(True))
    live_task = asyncio.create_task(observe(False))
    await entered.get()
    await entered.get()

    assert is_request_dry_run() is False
    release.set()

    assert await dry_task == (True, True)
    assert await live_task == (False, False)
    assert is_request_dry_run() is False


@pytest.mark.asyncio
async def test_bridge_request_dry_run_context_resets_after_exception():
    """请求异常退出时也必须复位 dry-run ContextVar 并释放 session lock。"""
    from nanobot_kt.request_scope import BridgeRequestScope, is_request_dry_run

    lock = asyncio.Lock()

    with pytest.raises(RuntimeError, match="模拟请求失败"):
        async with BridgeRequestScope(lock, MagicMock(), dry_run=True):
            assert is_request_dry_run() is True
            raise RuntimeError("模拟请求失败")

    assert is_request_dry_run() is False
    assert lock.locked() is False


@patch("nanobot_kt.bridge.registry")
@patch("nanobot_kt.bridge.NewAPIClient")
@patch("nanobot_kt.bridge.load_agent_config")
@patch("nanobot_kt.bridge.Agent")
def test_bridge_dry_run_reaches_public_reply_execute(
    MockAgent,
    mock_load,
    MockClient,
    mock_registry,
    monkeypatch,
):
    """Bridge 的 dry-run 必须抑制公开 ReplyTool.execute 的贴纸计数副作用。"""
    from creatures.nanobot.prompts.skills.reply.tool import ReplyTool
    from nanobot_kt.bridge import NanobotBridge

    recorded = []
    monkeypatch.setattr(
        "core.sticker_memory.expand_sticker_refs_in_content",
        lambda content: content,
    )
    monkeypatch.setattr(
        "core.sticker_memory.record_sticker_uses_in_content",
        lambda content: recorded.append(content),
    )

    mock_config = MagicMock()
    mock_config.name = "test"
    mock_load.return_value = mock_config

    mock_agent = MagicMock()
    mock_agent.start = AsyncMock()
    mock_agent.registry.list_tools.return_value = []
    mock_conversation = MagicMock()
    mock_conversation.get_messages.return_value = []
    mock_conversation.find_last_user_index.return_value = -1
    mock_controller = MagicMock()
    mock_controller.conversation = mock_conversation
    mock_controller.llm = MagicMock(config=MagicMock(model="test-model"))
    mock_controller.max_attempts = 1
    mock_agent.controller = mock_controller

    candidate = {
        "id": "test-model",
        "intelligence": 12,
        "context_window": 128_000,
    }
    route_client = MagicMock()
    route_client.sync_models_to_registry = AsyncMock()
    route_client.estimate_complexity.return_value = 1
    route_client.get_ordered_candidates.return_value = [candidate]
    MockClient.return_value = route_client
    MockClient.get_failure_tracker.return_value = MagicMock(
        sync_is_disabled=MagicMock(return_value=False),
        record_success=AsyncMock(),
        record_failure=AsyncMock(),
    )
    mock_registry.get_models_by_provider.return_value = [candidate]
    mock_registry.get_model_info.return_value = None

    bridge = NanobotBridge()

    async def fake_process(_event):
        result = await ReplyTool().execute(
            {"content": "研究草稿 [sticker:test]"},
        )
        assert result.success

    mock_agent._process_event = AsyncMock(side_effect=fake_process)
    MockAgent.return_value = mock_agent

    async def _run():
        await bridge.start()
        return await bridge.handle_message(
            "请研究 Python 3.14 JIT",
            user_id="dry-run-user",
            session_id="private_dry-run-user",
            metadata={"dry_run": True},
        )

    result = run_async(_run())

    assert result == "研究草稿 [sticker:test]"
    assert recorded == []
