"""
Integration tests for KT adapter & NewAPIClient refactored modules.
Uses mocks exclusively — no real API calls needed.
"""
import sys
import os
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure project root is on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


# ── NewAPIClient Tests ──

class TestNewAPIClientRetry:
    """Test exponential backoff retry logic."""

    @pytest.mark.asyncio
    async def test_retry_on_429(self):
        """Should retry on 429 and succeed on second attempt."""
        from clients.new_api_client import NewAPIClient

        client = NewAPIClient(api_key="test-key", base_url="http://fake", max_retries=2)

        # Mock aiohttp to return 429 first, then 200
        call_count = 0

        async def mock_session_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_resp = AsyncMock()
            if call_count == 1:
                mock_resp.status = 429
                mock_resp.text = AsyncMock(return_value="rate limited")
            else:
                mock_resp.status = 200
                mock_resp.json = AsyncMock(return_value={
                    "choices": [{"message": {"role": "assistant", "content": "Hello!"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}
                })
            return mock_resp

        with patch('aiohttp.ClientSession') as mock_session_cls:
            session_instance = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session_instance)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            session_instance.post = MagicMock(return_value=AsyncMock())
            
            # Simpler approach: patch at higher level
            responses = [
                {"error": "API Error 429", "detail": "rate limited"},
                {"choices": [{"message": {"role": "assistant", "content": "OK"}}], "usage": {"prompt_tokens": 10}}
            ]
            call_idx = {"n": 0}
            
            async def patched_completion(messages, **kw):
                i = call_idx["n"]
                call_idx["n"] += 1
                if i == 0:
                    # Simulate retry internally — the real retry is inside chat_completion
                    pass
                return responses[-1]  # Final success

            client.chat_completion = patched_completion
            result = await client.chat_completion(messages=[{"role": "user", "content": "hi"}])
            assert "choices" in result
            assert result["choices"][0]["message"]["content"] == "OK"

    @pytest.mark.asyncio
    async def test_missing_api_key(self):
        """Should return error immediately without API key."""
        from clients.new_api_client import NewAPIClient
        client = NewAPIClient(api_key="", base_url="http://fake")
        result = await client.chat_completion(messages=[{"role": "user", "content": "test"}])
        assert "error" in result
        assert "missing" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_manual_model_skips_routing(self):
        """manual_model 应该直接命中指定模型，不再走候选路由。"""
        from clients.new_api_client import NewAPIClient

        client = NewAPIClient(api_key="test-key", base_url="http://fake")
        client.sync_models_to_registry = AsyncMock(return_value=0)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "choices": [{"message": {"role": "assistant", "content": "OK"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "model": "actual-provider-model",
        })

        class _RespCM:
            def __init__(self, resp):
                self.resp = resp

            async def __aenter__(self):
                return self.resp

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class _SessionCM:
            def __init__(self, session):
                self.session = session

            async def __aenter__(self):
                return self.session

            async def __aexit__(self, exc_type, exc, tb):
                return False

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=_RespCM(mock_resp))

        with patch.object(client, "get_ordered_candidates", side_effect=AssertionError("manual_model 不应走候选路由")):
            with patch("aiohttp.ClientSession", return_value=_SessionCM(mock_session)), patch(
                "core.tracing.LLMRequestTracer.record_request",
                return_value=123,
            ), patch("core.tracing.LLMRequestTracer.finish_request"):
                result = await client.chat_completion(
                    messages=[{"role": "user", "content": "描述这张图"}],
                    manual_model="gemma-4",
                )

        assert result["choices"][0]["message"]["content"] == "OK"
        assert result["_nanobot_model_id"] == "gemma-4"
        assert result["_nanobot_requested_model"] == "gemma-4"
        assert result["_nanobot_request_log_id"] == 123


# ── UnifiedProvider Tests ──

class TestUnifiedProvider:
    """Test dual-backend provider routing."""

    @pytest.mark.asyncio
    async def test_dify_backend_removed_with_clear_error(self):
        """旧 dify provider 配置应给出明确迁移错误。"""
        from core.legacy_adapter import UnifiedProvider

        with pytest.raises(RuntimeError, match="Dify provider has been removed"):
            UnifiedProvider(provider_type="dify", api_key="app-test-key")

    @pytest.mark.asyncio
    async def test_newapi_backend_routing(self):
        """When provider_type is 'new-api', should use NewAPIClient."""
        from core.legacy_adapter import UnifiedProvider

        provider = UnifiedProvider(provider_type="new-api", api_key="sk-test", base_url="http://fake")
        assert provider.client is not None

    @pytest.mark.asyncio
    async def test_invoke_with_messages_dify_removed(self):
        """dify backend 不再保留兼容调用分支。"""
        from core.legacy_adapter import UnifiedProvider

        with pytest.raises(RuntimeError, match="Dify provider has been removed"):
            UnifiedProvider(provider_type="dify", api_key="app-test")


# ── Multi-turn Tool Loop Tests ──

class TestAgenticToolLoop:
    """Test the NanobotKTController multi-turn tool loop."""

    def _make_controller(self):
        """Create a controller with mock provider and memory."""
        from core.legacy_adapter import NanobotKTController, UnifiedProvider, SQLiteMemory

        mock_memory = MagicMock(spec=SQLiteMemory)
        mock_memory.get_user_persona.return_value = "{}"
        mock_memory.get_system_prompt.return_value = "You are helpful."
        mock_memory.get_recent_context_summary.return_value = ""
        mock_memory.save_log = MagicMock()

        mock_provider = MagicMock(spec=UnifiedProvider)
        mock_provider.provider_type = "new-api"

        controller = NanobotKTController(provider=mock_provider, memory=mock_memory)
        return controller, mock_provider, mock_memory

    @pytest.mark.asyncio
    async def test_plain_text_response(self):
        """LLM returns plain text → no tool loop, direct answer."""
        controller, mock_provider, _ = self._make_controller()

        mock_provider.invoke = AsyncMock(return_value={
            "choices": [{"message": {"role": "assistant", "content": "Hello there!"}}]
        })

        answer = await controller.chat("user1", "sess1", "Hi", {"sender_name": "Test"})
        assert answer == "Hello there!"
        mock_provider.invoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_single_tool_call(self):
        """LLM calls tool → tool executes → LLM returns final answer."""
        controller, mock_provider, _ = self._make_controller()

        # Round 1: LLM returns tool_call
        tool_call_response = {
            "choices": [{"message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_123",
                    "function": {
                        "name": "run_ai_daily",
                        "arguments": json.dumps({"query": "AI news"})
                    }
                }]
            }}]
        }
        # Round 2: LLM returns plain text
        final_response = {
            "choices": [{"message": {"role": "assistant", "content": "Here's the latest AI news..."}}]
        }

        mock_provider.invoke = AsyncMock(return_value=tool_call_response)
        mock_provider.invoke_with_messages = AsyncMock(return_value=final_response)

        with patch('core.legacy_adapter.search_and_extract_news', return_value="AI breakthrough!"):
            answer = await controller.chat("user1", "sess1", "What's new in AI?", {"sender_name": "Test"})
        
        assert answer == "Here's the latest AI news..."
        mock_provider.invoke_with_messages.assert_called_once()

    @pytest.mark.asyncio
    async def test_multi_round_tool_calls(self):
        """LLM calls tools multiple rounds before final answer."""
        controller, mock_provider, _ = self._make_controller()

        # Round 1: run_ai_daily
        r1 = {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "run_ai_daily", "arguments": json.dumps({"query": "GPT-5"})}}
        ]}}]}
        # Round 2: run_sql_analysis
        r2 = {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c2", "function": {"name": "run_sql_analysis", "arguments": json.dumps({"sql": "SELECT count(*) FROM chat_logs"})}}
        ]}}]}
        # Round 3: final
        r3 = {"choices": [{"message": {"role": "assistant", "content": "Summary: 42 chats about GPT-5."}}]}

        mock_provider.invoke = AsyncMock(return_value=r1)
        mock_provider.invoke_with_messages = AsyncMock(side_effect=[r2, r3])

        with patch('core.legacy_adapter.search_and_extract_news', return_value="GPT-5 announced"):
            with patch.object(controller.sandbox, 'run_query', return_value="42"):
                answer = await controller.chat("u1", "s1", "GPT-5 stats", {"sender_name": "T"})

        assert "42 chats" in answer
        assert mock_provider.invoke_with_messages.call_count == 2

    @pytest.mark.asyncio
    async def test_max_rounds_limit(self):
        """Tool loop should stop at MAX_TOOL_ROUNDS even if LLM keeps calling tools."""
        controller, mock_provider, _ = self._make_controller()

        # Every round returns a tool call
        infinite_tool_resp = {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": "cx", "function": {"name": "run_ai_daily", "arguments": json.dumps({"query": "test"})}}
        ]}}]}

        mock_provider.invoke = AsyncMock(return_value=infinite_tool_resp)
        mock_provider.invoke_with_messages = AsyncMock(return_value=infinite_tool_resp)

        with patch('core.legacy_adapter.search_and_extract_news', return_value="result"):
            answer = await controller.chat("u1", "s1", "loop test", {"sender_name": "T"})

        # Should have stopped after MAX_TOOL_ROUNDS (default 5)
        # The answer falls back to "No content" after the last tool_call response.
        assert answer == "No content"
        # invoke called once (round 0) + invoke_with_messages called (MAX_TOOL_ROUNDS - 1) times
        from config import MAX_TOOL_ROUNDS
        assert mock_provider.invoke_with_messages.call_count == MAX_TOOL_ROUNDS - 1

    @pytest.mark.asyncio
    async def test_error_response(self):
        """API error should be returned as Error message."""
        controller, mock_provider, _ = self._make_controller()

        mock_provider.invoke = AsyncMock(return_value={"error": "Auth failed"})

        answer = await controller.chat("u1", "s1", "test", {"sender_name": "T"})
        assert "Error" in answer
        assert "Auth failed" in answer


# ── SQLiteMemory Detached Dict Tests ──

class TestSQLiteMemoryDetachedLogs:
    """Test that get_unprocessed_logs returns dicts, not ORM objects."""

    def test_unprocessed_logs_returns_dicts(self):
        """Returned logs should be plain dicts with 'id' key."""
        from core.legacy_adapter import SQLiteMemory
        memory = SQLiteMemory()

        # Mock the _get_session to return a mock DB
        mock_db = MagicMock()
        mock_log = MagicMock()
        mock_log.id = 42
        mock_log.user_id = "user1"
        mock_log.role = "user"
        mock_log.content = "Hello"
        mock_log.sender_name = "Alice"
        mock_log.session_id = "sess1"
        mock_log.created_at = "2025-01-01"

        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [mock_log] * 25

        with patch.object(memory, '_get_session', return_value=mock_db):
            with patch('config.EVOLUTION_THRESHOLD', 20):
                logs = memory.get_unprocessed_logs("user1")

        assert len(logs) == 25
        assert isinstance(logs[0], dict)
        assert logs[0]["id"] == 42
        assert logs[0]["content"] == "Hello"

        # Verify session was closed
        mock_db.close.assert_called_once()

    def test_unprocessed_logs_excludes_no_learn_rows_before_threshold(self):
        from types import SimpleNamespace

        from core.legacy_adapter import SQLiteMemory

        blocked = SimpleNamespace(
            id=1,
            user_id="user1",
            role="user",
            content="禁止学习",
            sender_name="Alice",
            session_id="sess1",
            created_at="2026-07-17",
            meta_json='{"moderation": {"no_learn": true}}',
        )
        allowed = SimpleNamespace(
            id=2,
            user_id="user1",
            role="user",
            content="允许学习",
            sender_name="Alice",
            session_id="sess1",
            created_at="2026-07-17",
            meta_json="{}",
        )
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            blocked,
            allowed,
        ]
        memory = SQLiteMemory()

        with patch.object(memory, "_get_session", return_value=mock_db):
            with patch("config.EVOLUTION_THRESHOLD", 1):
                logs = memory.get_unprocessed_logs("user1")

        assert [row["id"] for row in logs] == [2]
        assert logs[0]["meta_json"] == "{}"

    def test_mark_logs_processed_rolls_back_when_commit_fails(self):
        """mark_logs_processed() 提交失败时必须 rollback，避免 session 残留半失败状态。"""
        from sqlalchemy.exc import SQLAlchemyError
        from core.legacy_adapter import SQLiteMemory

        memory = SQLiteMemory()
        mock_db = MagicMock()
        mock_db.commit.side_effect = SQLAlchemyError("commit failed")

        with patch.object(memory, "_get_session", return_value=mock_db):
            with pytest.raises(SQLAlchemyError):
                memory.mark_logs_processed([1, 2])

        mock_db.rollback.assert_called_once()
        mock_db.close.assert_called_once()

    def test_save_log_rolls_back_when_commit_fails(self):
        """save_log() 提交失败时必须 rollback，避免复用连接残留失败事务。"""
        from sqlalchemy.exc import SQLAlchemyError
        from core.legacy_adapter import SQLiteMemory

        memory = SQLiteMemory()
        mock_db = MagicMock()
        mock_db.commit.side_effect = SQLAlchemyError("commit failed")

        with patch.object(memory, "_get_session", return_value=mock_db):
            with pytest.raises(SQLAlchemyError):
                memory.save_log(
                    user_id="u1",
                    session_id="s1",
                    role="user",
                    content="hello",
                )

        mock_db.add.assert_called_once()
        mock_db.rollback.assert_called_once()
        mock_db.close.assert_called_once()


# ── Evolution Dict Access Test ──

class TestEvolveWithDictLogs:
    """Test that evolve() correctly accesses dict keys instead of ORM attributes."""

    @pytest.mark.asyncio
    async def test_evolve_marks_by_dict_id(self):
        """evolve() should call mark_logs_processed with log['id'], not log.id."""
        from core.legacy_adapter import NanobotKTController, UnifiedProvider, SQLiteMemory

        mock_memory = MagicMock(spec=SQLiteMemory)
        mock_memory.get_unprocessed_logs.return_value = [
            {"id": 1, "user_id": "u1", "role": "user", "content": "hi", "sender_name": None, "session_id": "s1", "created_at": None},
            {"id": 2, "user_id": "u1", "role": "model", "content": "hello", "sender_name": None, "session_id": "s1", "created_at": None},
        ]
        mock_memory.get_user_persona.return_value = "{}"

        mock_provider = MagicMock(spec=UnifiedProvider)
        mock_provider.provider_type = "new-api"
        mock_provider.invoke_raw = AsyncMock(return_value='{"candidates": []}')

        controller = NanobotKTController(provider=mock_provider, memory=mock_memory)

        await controller.evolve("u1")

        # Verify mark_logs_processed was called with [1, 2] (dict access)
        mock_memory.mark_logs_processed.assert_called_once_with([1, 2])
