import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from core.database import ChatLog
from core.evolution import evolution_task, model_scout_task

def test_evolution_task_not_triggered(db_session):
    """测试日志没有达到阈值时不触发进化"""
    # 塞入很少的几条记录
    for i in range(5):
        log = ChatLog(user_id="evo_user", role="user", content=f"msg {i}", processed=0)
        db_session.add(log)
    db_session.commit()
    
    with patch("core.evolution.SQLiteMemory") as mock_memory_cls, \
         patch("core.evolution.NanobotKTController") as mock_controller_cls:
        mock_memory = MagicMock()
        mock_memory.get_unprocessed_logs.return_value = []
        mock_memory_cls.return_value = mock_memory
        mock_controller = MagicMock()
        mock_controller_cls.return_value = mock_controller
        evolution_task("evo_user")
    mock_controller.evolve.assert_not_called()

def test_evolution_task_triggered(db_session):
    """测试日志达到阈值后，走通 KT 回写逻辑"""
    import config
    limit = config.EVOLUTION_THRESHOLD
    for i in range(limit):
        log = ChatLog(user_id="evo_user_2", role="user", content=f"hit {i}", processed=0)
        db_session.add(log)
    db_session.commit()
    
    with patch("core.evolution.SQLiteMemory") as mock_memory_cls, \
         patch("core.evolution.NanobotKTController") as mock_controller_cls:
        mock_memory = MagicMock()
        mock_logs = db_session.query(ChatLog).filter_by(user_id="evo_user_2", processed=0).all()
        mock_memory.get_unprocessed_logs.return_value = mock_logs
        mock_memory.get_user_persona.return_value = "{}"
        mock_memory.get_system_prompt.return_value = "你是一个具备自进化能力的智能助手。"
        mock_memory_cls.return_value = mock_memory

        mock_controller = MagicMock()
        mock_controller.evolve = AsyncMock(return_value=None)
        mock_controller_cls.return_value = mock_controller

        evolution_task("evo_user_2")
        
    # 验证 evolve 被调用（实际的 DB 标记由 controller.evolve 内部完成，此处为 mock）
    mock_controller.evolve.assert_awaited_once_with("evo_user_2")


def test_model_scout_task_runs_with_async_bridge():
    with patch("core.evolution.SQLiteMemory") as mock_memory_cls, \
         patch("core.evolution.NanobotKTController") as mock_controller_cls, \
         patch("core.evolution._build_provider", return_value=object()):
        mock_memory = MagicMock()
        mock_memory_cls.return_value = mock_memory

        mock_controller = MagicMock()
        mock_controller.model_scout.run = AsyncMock(return_value=None)
        mock_controller_cls.return_value = mock_controller

        model_scout_task()

    mock_controller.model_scout.run.assert_awaited_once()
    args = mock_controller.model_scout.run.await_args.args
    assert args[0] == "搜集最新的 AI 大模型（LLM）版本发布动态"
    mock_memory.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_legacy_adapter_memory_extract_uses_v2_task_template(monkeypatch):
    from core.legacy_adapter import NanobotKTController

    captured = {}

    class FakeMemory:
        def get_unprocessed_logs(self, user_id):
            return [{"id": 1, "role": "user", "content": "我长期使用 Python"}]

        def get_user_persona(self, user_id):
            return "{}"

        def mark_logs_processed(self, ids):
            captured["processed"] = ids

    class FakeProvider:
        async def invoke_raw(self, *, query, system_prompt, user_id, model_tier):
            captured["query"] = query
            return '{"candidates":[]}'

    class FakeAnalyst:
        async def run(self, logs, provider):
            return {}

    monkeypatch.setattr(
        "core.prompt_v2.task_templates.render_task_prompt",
        lambda key, values, fallback_text="": (
            f"V2 记忆模板: {values['conversation']} / {values['existing_memory']}"
        ),
    )

    engine = NanobotKTController.__new__(NanobotKTController)
    engine.memory = FakeMemory()
    engine.provider = FakeProvider()
    engine.log_analyst = FakeAnalyst()

    await engine.evolve("u1")

    assert "V2 记忆模板:" in captured["query"]
    assert "我长期使用 Python" in captured["query"]
    assert captured["processed"] == [1]

