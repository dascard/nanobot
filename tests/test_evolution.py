import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from core.database import ChatLog
from core.evolution import evolution_task, model_scout_task


@pytest.fixture(autouse=True)
def _enable_persona_auto_update_for_legacy_tests(monkeypatch):
    from core.settings_service import settings

    original = settings.get_bool
    monkeypatch.setattr(
        settings,
        "get_bool",
        lambda key, default=False: True
        if key == "persona.auto_update_enabled"
        else original(key, default),
    )

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


def test_evolution_task_is_paused_when_auto_update_is_disabled(monkeypatch):
    from core.settings_service import settings

    monkeypatch.setattr(settings, "get_bool", lambda key, default=False: False)
    monkeypatch.setattr(
        "core.evolution.SQLiteMemory",
        lambda: (_ for _ in ()).throw(AssertionError("停用时不应访问画像日志")),
    )

    evolution_task("paused-user")

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

    monkeypatch.setattr(
        "core.prompt_v2.task_templates.render_task_prompt",
        lambda key, values, fallback_text="": (
            "V2 Persona System"
            if key == "tasks/persona_candidate_system"
            else f"V2 记忆模板: {values['conversation']} / {values['existing_memory']}"
        ),
    )

    engine = NanobotKTController.__new__(NanobotKTController)
    engine.memory = FakeMemory()
    engine.provider = FakeProvider()

    await engine.evolve("u1")

    assert "V2 记忆模板:" in captured["query"]
    assert "我长期使用 Python" in captured["query"]
    assert captured["processed"] == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_output",
    ["", "garbage", "{}", "[]", '{"candidates":{}}'],
)
async def test_memory_extract_contract_failure_keeps_logs_unprocessed(
    monkeypatch,
    model_output,
):
    from core.legacy_adapter import NanobotKTController
    from core.prompt_v2.task_contracts import TaskOutputContractError

    captured = {"processed": []}

    class FakeMemory:
        def get_unprocessed_logs(self, user_id):
            return [{"id": 1, "role": "user", "content": "长期使用 Python"}]

        def get_user_persona(self, user_id):
            return "{}"

        def mark_logs_processed(self, ids):
            captured["processed"].extend(ids)

    class FakeProvider:
        calls = 0

        async def invoke_raw(self, **_kwargs):
            self.calls += 1
            return model_output

    engine = NanobotKTController.__new__(NanobotKTController)
    engine.memory = FakeMemory()
    engine.provider = FakeProvider()

    with pytest.raises(TaskOutputContractError):
        await engine.evolve("u1")

    assert engine.provider.calls == 2
    assert captured["processed"] == []


@pytest.mark.asyncio
async def test_memory_extract_state_machine_failure_keeps_logs_unprocessed(monkeypatch):
    from core.legacy_adapter import NanobotKTController

    captured = {"processed": []}

    class FakeMemory:
        def get_unprocessed_logs(self, user_id):
            return [{"id": 1, "role": "user", "content": "长期使用 Python"}]

        def get_user_persona(self, user_id):
            return "{}"

        def mark_logs_processed(self, ids):
            captured["processed"].extend(ids)

    class FakeProvider:
        async def invoke_raw(self, **_kwargs):
            return '{"candidates":[{"text":"长期使用 Python"}]}'

    class FailingStateMachine:
        def __init__(self, db, user_id):
            pass

        def process_candidates(self, candidates):
            raise RuntimeError("state machine rollback")

    class FakeDb:
        def close(self):
            pass

    monkeypatch.setattr("core.legacy_adapter.PersonaStateMachine", FailingStateMachine)
    monkeypatch.setattr("core.legacy_adapter.SessionLocal", lambda: FakeDb())

    engine = NanobotKTController.__new__(NanobotKTController)
    engine.memory = FakeMemory()
    engine.provider = FakeProvider()

    with pytest.raises(RuntimeError, match="state machine rollback"):
        await engine.evolve("u1")

    assert captured["processed"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "processing_stats,update_result,error_match",
    [
        ({"processing_errors": 1}, True, "candidate processing errors"),
        ({"processing_errors": 0}, False, "persona persistence rejected"),
    ],
)
async def test_memory_extract_post_parse_failure_keeps_logs_unprocessed(
    monkeypatch,
    processing_stats,
    update_result,
    error_match,
):
    from core.legacy_adapter import NanobotKTController

    captured = {"processed": []}

    class FakeMemory:
        def get_unprocessed_logs(self, user_id):
            return [{"id": 1, "role": "user", "content": "长期使用 Python"}]

        def get_user_persona(self, user_id):
            return "{}"

        def update_persona(self, user_id, persona_summary):
            return update_result

        def mark_logs_processed(self, ids):
            captured["processed"].extend(ids)

    class FakeProvider:
        async def invoke_raw(self, **_kwargs):
            return '{"candidates":[{"text":"长期使用 Python"}]}'

    class FakeStateMachine:
        def __init__(self, db, user_id):
            pass

        def process_candidates(self, candidates):
            return dict(processing_stats)

        def build_summary(self):
            return "{}"

    class FakeDb:
        def close(self):
            pass

    monkeypatch.setattr("core.legacy_adapter.PersonaStateMachine", FakeStateMachine)
    monkeypatch.setattr("core.legacy_adapter.SessionLocal", lambda: FakeDb())

    engine = NanobotKTController.__new__(NanobotKTController)
    engine.memory = FakeMemory()
    engine.provider = FakeProvider()

    with pytest.raises(RuntimeError, match=error_match):
        await engine.evolve("u1")

    assert captured["processed"] == []


@pytest.mark.asyncio
async def test_memory_extract_invalid_templates_fail_closed_and_keep_logs_pending(
    tmp_path,
    monkeypatch,
):
    from core.legacy_adapter import NanobotKTController

    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    for base, body in (
        (default_dir, "BROKEN DEFAULT"),
        (runtime_dir, "BROKEN RUNTIME"),
    ):
        path = base / "tasks" / "memory_extract.md"
        path.parent.mkdir(parents=True)
        path.write_text(body, encoding="utf-8")
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    captured = {"processed": []}

    class FakeMemory:
        def get_unprocessed_logs(self, user_id):
            return [{"id": 1, "role": "user", "content": "长期使用 Rust"}]

        def get_user_persona(self, user_id):
            return '{"preference":"UNIQUE_EXISTING_MEMORY_123"}'

        def mark_logs_processed(self, ids):
            captured["processed"].extend(ids)

    class FakeProvider:
        async def invoke_raw(self, *, query, system_prompt, **_kwargs):
            captured["query"] = query
            captured["system_prompt"] = system_prompt
            return '{"candidates":[]}'

    engine = NanobotKTController.__new__(NanobotKTController)
    engine.memory = FakeMemory()
    engine.provider = FakeProvider()

    from core.prompt_v2.task_templates import TaskTemplateUnavailableError

    with pytest.raises(TaskTemplateUnavailableError, match="memory_extract"):
        await engine.evolve("u1")

    assert "query" not in captured
    assert "system_prompt" not in captured
    assert captured["processed"] == []


@pytest.mark.asyncio
async def test_evolution_hot_path_only_extracts_candidates_and_updates_persona(
    monkeypatch,
):
    from core.legacy_adapter import NanobotKTController

    captured = {"provider_calls": 0, "processed": [], "updates": []}

    class FakeMemory:
        def get_unprocessed_logs(self, user_id):
            return [{"id": 7, "role": "user", "content": "我长期使用 Rust"}]

        def get_user_persona(self, user_id):
            return "{}"

        def update_persona(self, user_id, persona_summary):
            captured["updates"].append((user_id, persona_summary))
            return True

        def mark_logs_processed(self, ids):
            captured["processed"].extend(ids)

    class FakeProvider:
        async def invoke_raw(self, **_kwargs):
            captured["provider_calls"] += 1
            return '{"candidates":[{"text":"长期使用 Rust"}]}'

    class FakeStateMachine:
        def __init__(self, db, user_id):
            pass

        def process_candidates(self, candidates):
            return {"processing_errors": 0, "created": 1}

        def build_summary(self):
            return '{"facts":[{"content":"长期使用 Rust"}],"count":1}'

    class FakeDb:
        def close(self):
            pass

    monkeypatch.setattr("core.legacy_adapter.PersonaStateMachine", FakeStateMachine)
    monkeypatch.setattr("core.legacy_adapter.SessionLocal", lambda: FakeDb())

    controller = NanobotKTController.__new__(NanobotKTController)
    controller.memory = FakeMemory()
    controller.provider = FakeProvider()

    await controller.evolve("u1")

    assert captured["provider_calls"] == 1
    assert captured["updates"] == [
        ("u1", '{"facts":[{"content":"长期使用 Rust"}],"count":1}')
    ]
    assert captured["processed"] == [7]


def test_update_persona_does_not_modify_legacy_system_prompt(db_session, monkeypatch):
    from core import database
    from core.database import Persona, SystemPrompt
    from core.legacy_adapter import SQLiteMemory

    monkeypatch.setattr("core.legacy_adapter.SessionLocal", database.SessionLocal)

    db_session.add(SystemPrompt(user_id="u1", prompt_text="历史提示词"))
    db_session.commit()

    assert SQLiteMemory().update_persona("u1", '{"facts":[],"count":0}') is True

    db_session.expire_all()
    persona = db_session.query(Persona).filter(Persona.user_id == "u1").one()
    prompt = db_session.query(SystemPrompt).filter(SystemPrompt.user_id == "u1").one()
    assert persona.persona_json == '{"facts": [], "count": 0}'
    assert prompt.prompt_text == "历史提示词"

