from datetime import datetime, timedelta
import asyncio
from tests.async_helpers import run_async
import json


def test_clean_message_filters_commands_and_noise():
    from creatures.nanobot.prompts.skills.group_analysis.preprocess import clean_message

    assert clean_message("/group_analysis") is None
    assert clean_message("<@123456> /help") is None
    assert clean_message("@bot") is None
    assert clean_message("  ++==  ") is None
    assert clean_message("https://example.com") is None
    assert clean_message("正常聊天内容") == "正常聊天内容"
    assert clean_message("[A]: 签到") is None
    assert clean_message("[A]: 正常聊天内容") == "正常聊天内容"


def test_parse_instruction_window_hours():
    from creatures.nanobot.prompts.skills.group_analysis.preprocess import parse_instruction_window_hours

    assert parse_instruction_window_hours("最近2小时") == 2
    assert parse_instruction_window_hours("只看最近6小时") == 6
    assert parse_instruction_window_hours("最近1天") == 24
    assert parse_instruction_window_hours("看全部历史") == 0
    assert parse_instruction_window_hours("随便看看") is None


def test_resolve_analysis_window_hours_defaults_and_overrides():
    from creatures.nanobot.prompts.skills.group_analysis.preprocess import resolve_analysis_window_hours

    assert resolve_analysis_window_hours(None, "") == 24
    assert resolve_analysis_window_hours("6", "") == 6
    assert resolve_analysis_window_hours(0, "") is None
    assert resolve_analysis_window_hours("6", "最近2小时") == 2
    assert resolve_analysis_window_hours(None, "全部历史") is None


def test_group_analysis_resolves_group_name_from_chatlog_session_name(db_session):
    from core.database import ChatLog
    from creatures.nanobot.prompts.skills.group_analysis.repository import GroupAnalysisRepository

    db_session.add(ChatLog(
        user_id="group_2468",
        session_id="group_2468",
        role="ambient",
        content="[A]: hello",
        sender_name="A",
        session_name="项目讨论群",
    ))
    db_session.commit()

    repo = GroupAnalysisRepository(db_session)
    group = repo.resolve_group("项目讨论")

    assert group is not None
    assert group.group_id == "group_2468"
    assert group.legacy_group_id == "2468"
    assert group.name == "项目讨论群"
    assert repo.get_group_candidates("项目讨论")[0]["id"] == "2468"


def test_group_analysis_resolves_noisy_group_name_by_ordered_match(db_session):
    from core.database import ChatLog
    from creatures.nanobot.prompts.skills.group_analysis.repository import GroupAnalysisRepository

    db_session.add(ChatLog(
        user_id="group_984760873",
        session_id="group_984760873",
        role="ambient",
        content="[A]: hello",
        sender_name="A",
        session_name="凡赛尔学院•图书馆",
    ))
    db_session.add(ChatLog(
        user_id="group_971976533",
        session_id="group_971976533",
        role="ambient",
        content="[B]: hello",
        sender_name="B",
        session_name="雪花谷私立高中•图书馆",
    ))
    db_session.commit()

    repo = GroupAnalysisRepository(db_session)

    assert repo.resolve_group("凡赛尔图书馆").group_id == "group_984760873"
    assert repo.resolve_group("雪花谷图书馆").group_id == "group_971976533"
    assert repo.resolve_group("图书馆") is None
    assert {c["id"] for c in repo.get_group_candidates("图书馆")} == {"984760873", "971976533"}


def test_group_analysis_prefers_exact_group_name_when_fuzzy_has_multiple(db_session):
    from core.database import User
    from creatures.nanobot.prompts.skills.group_analysis.repository import GroupAnalysisRepository

    db_session.add(User(id="group_1001", name="AI"))
    db_session.add(User(id="group_1002", name="AI 讨论群"))
    db_session.commit()

    group = GroupAnalysisRepository(db_session).resolve_group("AI")

    assert group is not None
    assert group.group_id == "group_1001"
    assert group.name == "AI"


def test_group_analysis_resolves_stream_id_from_chat_stream_config(db_session):
    from core.database import ChatStreamConfig
    from creatures.nanobot.prompts.skills.group_analysis.repository import GroupAnalysisRepository

    db_session.add(ChatStreamConfig(chat_stream_id="qq:24680:group"))
    db_session.commit()

    group = GroupAnalysisRepository(db_session).resolve_group("qq:24680:group")

    assert group is not None
    assert group.group_id == "group_24680"
    assert group.name == "24680"


def test_compute_group_statistics_returns_summary():
    from creatures.nanobot.prompts.skills.group_analysis.preprocess import compute_group_statistics

    messages = [
        {"user_id": "A", "content": "早上好😂", "hour": 9, "is_reply": False},
        {"user_id": "B", "content": "今天真热闹🔥", "hour": 9, "is_reply": True},
        {"user_id": "A", "content": "晚上继续聊", "hour": 21, "is_reply": False},
    ]

    stats = compute_group_statistics(messages)

    assert stats["message_count"] == 3
    assert stats["participant_count"] == 2
    assert stats["total_characters"] == len("早上好😂今天真热闹🔥晚上继续聊")
    assert stats["average_message_length"] > 0
    assert stats["most_active_period"] == "09:00-10:00"
    assert stats["emoji_count"] == 2
    assert stats["hourly_counts"][9] == 2


def test_format_group_report_includes_statistics_quality_and_mbti():
    from creatures.nanobot.prompts.skills.group_analysis.render import format_scrapbook_html

    report = format_scrapbook_html(
        "测试群",
        {
            "message_count": 12,
            "participant_count": 4,
            "total_characters": 96,
            "average_message_length": 8.0,
            "most_active_period": "21:00-22:00",
            "hourly_counts": {9: 2, 21: 10},
            "emoji_count": 5,
        },
        {"topics": [{"topic": "AI 模型", "contributors": ["A", "B"], "detail": "大家在聊价格和能力。"}]},
        {"users": [{"user_id": "A", "title": "深夜卷王", "mbti": "INTJ", "reason": "总在夜里高强度输出"}]},
        {"quotes": [{"user_id": "B", "content": "今天的 benchmark 又反转了"}]},
        {
            "title": "群聊质量稳定",
            "subtitle": "讨论密度高，跑题少",
            "dimensions": [
                {"name": "信息密度", "percentage": 82, "comment": "有效观点连续出现"},
                {"name": "互动氛围", "percentage": 76, "comment": "接话积极，节奏自然"},
            ],
            "summary": "整体属于高频互动、低冷场的讨论型群聊。",
        },
    )

    assert "<!DOCTYPE html>" in report
    assert "group-analysis-report" in report
    assert "消息总数" in report
    assert "表情统计" in report
    assert "24H 活跃轨迹" in report
    assert "最活跃时段" in report
    assert "群友画像" in report
    assert "<table" in report
    assert "聊天质量锐评" in report
    assert "INTJ" in report


def test_format_group_report_escapes_contributors_and_clamps_percentages():
    from creatures.nanobot.prompts.skills.group_analysis.render import format_scrapbook_html

    report = format_scrapbook_html(
        "测试群",
        {
            "message_count": 3,
            "participant_count": 2,
            "total_characters": 12,
            "most_active_period": "12:00-13:00",
            "hourly_counts": {12: 3},
            "emoji_count": 0,
        },
        {
            "topics": [
                {
                    "topic": "安全测试",
                    "contributors": ["<script>alert(1)</script>"],
                    "detail": "检查 HTML 注入。",
                }
            ]
        },
        {"users": []},
        {"quotes": []},
        {
            "dimensions": [
                {"name": "越界", "percentage": 150, "comment": "应被截断"},
                {"name": "非法", "percentage": "bad;width:999px", "comment": "应被兜底"},
            ]
        },
    )

    assert "<script>alert(1)</script>" not in report
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in report
    assert "150%" not in report
    assert "bad;width" not in report
    assert "width:100%" in report
    assert "width:50%" in report


def test_build_message_prompt_text_keeps_large_group_context():
    from creatures.nanobot.prompts.skills.group_analysis.preprocess import build_message_prompt_text

    messages = [
        {
            "time": f"{idx % 24:02d}:00",
            "user_id": f"用户{idx % 60}",
            "content": f"第{idx}条消息，讨论话题{idx % 7}，包含足够上下文。",
        }
        for idx in range(2200)
    ]

    text = build_message_prompt_text(messages, max_chars=12000)

    assert "原始可分析消息总数: 2200" in text
    assert "第2199条消息" in text
    assert len(text) <= 12000


def test_group_analysis_filters_internal_and_artifact_logs():
    from core.database import ChatLog
    from creatures.nanobot.prompts.skills.group_analysis.preprocess import filter_analyzable_logs

    logs = [
        ChatLog(role="ambient", content="[A]: 今天聊 AI", sender_name="A"),
        ChatLog(role="assistant", content="可以，这个思路能跑", sender_name="nanobot"),
        ChatLog(role="system", content="[NO_SEND] agent_result=no_tool_call",
                meta_json='{"no_send": true}'),
        ChatLog(role="assistant", content='<!DOCTYPE html><html><body class="group-analysis-report">旧日报</body></html>'),
        ChatLog(role="assistant", content='<article class="news-brief">AI 日报</article>'),
        ChatLog(role="ambient", content="[B]: 被屏蔽内容", sender_name="B",
                meta_json='{"moderation": {"no_context": true}}'),
        ChatLog(role="tool", content="工具输出"),
    ]

    filtered = filter_analyzable_logs(logs)

    assert [log.content for log in filtered] == [
        "[A]: 今天聊 AI",
        "可以，这个思路能跑",
    ]



def test_group_analysis_dedupes_all_ambient_covered_by_user_source_ids():
    """TimingGate 合并转发：user 的 source_message_ids 覆盖全部 ambient → 全去重。"""
    from core.database import ChatLog
    from creatures.nanobot.prompts.skills.group_analysis.preprocess import dedupe_group_logs

    logs = [
        ChatLog(role="ambient", message_id="m1", content="[A]: hello", sender_name="A"),
        ChatLog(role="ambient", message_id="m2", content="[A]: world", sender_name="A"),
        ChatLog(role="user", message_id="m1", source_message_ids_json='["m1", "m2"]',
                content="合并: hello\nworld", sender_name="A"),
        ChatLog(role="assistant", content="bot reply", sender_name="nanobot"),
    ]

    deduped = dedupe_group_logs(logs)

    assert [log.role for log in deduped] == ["user", "assistant"]


def test_group_analysis_keeps_ambient_not_in_user_source_ids():
    """ambient 的 message_id 不在任何 user 的 source_message_ids 中 → 保留。"""
    from core.database import ChatLog
    from creatures.nanobot.prompts.skills.group_analysis.preprocess import dedupe_group_logs

    logs = [
        ChatLog(role="ambient", message_id="m1", content="[A]: hello", sender_name="A"),
        ChatLog(role="ambient", message_id="m3", content="[C]: unrelated", sender_name="C"),
        ChatLog(role="user", message_id="m1", source_message_ids_json='["m1", "m2"]',
                content="merged hello", sender_name="A"),
    ]

    deduped = dedupe_group_logs(logs)

    # m1 被 user 覆盖 → 去重；m3 不在 user 的 source 中 → 保留
    assert [(log.role, log.message_id) for log in deduped] == [
        ("ambient", "m3"),
        ("user", "m1"),
    ]


def test_group_analysis_keeps_source_ambient_when_user_content_does_not_cover_it():
    """防御非 Plan8 客户端：source_ids 批量传入但 user 内容未合并对应原文时，不能误删 ambient。"""
    from core.database import ChatLog
    from creatures.nanobot.prompts.skills.group_analysis.preprocess import dedupe_group_logs

    logs = [
        ChatLog(role="ambient", message_id="m1", content="[A]: hello", sender_name="A"),
        ChatLog(role="ambient", message_id="m2", content="[B]: world", sender_name="B"),
        ChatLog(role="user", message_id="m1", source_message_ids_json='["m1", "m2"]',
                content="hello", sender_name="A"),
    ]

    deduped = dedupe_group_logs(logs)

    assert [(log.role, log.message_id) for log in deduped] == [
        ("ambient", "m2"),
        ("user", "m1"),
    ]


def test_group_analysis_dedupes_duplicate_ambient_same_message_id():
    """同 message_id 的 ambient 只保留一条。"""
    from core.database import ChatLog
    from creatures.nanobot.prompts.skills.group_analysis.preprocess import dedupe_group_logs

    logs = [
        ChatLog(role="ambient", message_id="m1", content="[A]: hello", sender_name="A"),
        ChatLog(role="ambient", message_id="m1", content="[A]: hello", sender_name="A"),
        ChatLog(role="user", message_id="m2", content="world", sender_name="B"),
    ]
    deduped = dedupe_group_logs(logs)
    assert [(log.role, log.message_id) for log in deduped] == [
        ("ambient", "m1"),
        ("user", "m2"),
    ]


def test_group_analysis_assistant_not_participate_in_dedupe():
    """assistant 消息不参与 ambient/user 去重，也不被去重逻辑删除。"""
    from core.database import ChatLog
    from creatures.nanobot.prompts.skills.group_analysis.preprocess import dedupe_group_logs

    logs = [
        ChatLog(role="ambient", message_id="m1", content="[A]: hello", sender_name="A"),
        ChatLog(role="assistant", content="bot reply", sender_name="nanobot"),
        ChatLog(role="user", message_id="m1", source_message_ids_json='["m1"]',
                content="merged: hello", sender_name="A"),
        ChatLog(role="assistant", content="another bot reply", sender_name="nanobot"),
    ]
    deduped = dedupe_group_logs(logs)
    roles = [log.role for log in deduped]
    assert roles.count("assistant") == 2
    assert roles.count("ambient") == 0
    assert roles.count("user") == 1


def test_group_analysis_tool_execute_returns_rich_html(monkeypatch):
    import core.database as database
    import clients.new_api_client as new_api_client
    from core.database import ChatLog, User, Persona
    from creatures.nanobot.prompts.skills.group_analysis.tool import GroupAnalysisTool

    now = datetime.now()
    logs = [
        ChatLog(user_id="group_123", session_id="group_123", sender_name="A", role="ambient", content="今天聊 AI", created_at=now - timedelta(hours=1)),
        ChatLog(user_id="group_123", session_id="group_123", sender_name="B", role="ambient", content="价格确实降了", created_at=now - timedelta(hours=1)),
        ChatLog(user_id="group_123", session_id="group_123", sender_name="A", role="ambient", content="benchmark 也反转了", created_at=now - timedelta(minutes=30)),
    ]
    user = User(id="group_123", name="测试群")
    persona = Persona(user_id="group_123", persona_json='{"facts":[{"content":"喜欢锐评"}]}')

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

    class FakeQuery:
        def __init__(self, dataset):
            self.dataset = dataset

        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def limit(self, *args, **kwargs):
            return self

        def all(self):
            return list(self.dataset) if isinstance(self.dataset, list) else []

        def first(self):
            if isinstance(self.dataset, list):
                return self.dataset[0] if self.dataset else None
            return self.dataset

    class FakeSession:
        def query(self, model):
            model_name = getattr(model, "__name__", "")
            if model_name == "ChatLog":
                return FakeQuery(logs)
            if model_name == "User":
                return FakeQuery(user)
            if model_name == "Persona":
                return FakeQuery(persona)
            return FakeQuery(None)

        def close(self):
            return None

    async def fake_call(_client, _system_prompt, prompt, max_retries=2):
        if "核心讨论话题" in prompt:
            return '{"topics":[{"topic":"AI 模型","contributors":["A","B"],"detail":"大家在讨论价格和 benchmark。"}]}'
        if "用户发言统计" in prompt:
            return '{"users":[{"user_id":"A","title":"夜聊选手","mbti":"INTP","reason":"经常抛观点带节奏"}]}'
        if "聊天质量" in prompt:
            return '{"title":"群聊质量在线","subtitle":"密度较高","dimensions":[{"name":"信息密度","percentage":80,"comment":"有效信息持续输出"}],"summary":"讨论围绕同一主题推进。"}'
        return '{"quotes":[{"user_id":"B","content":"价格确实降了"}]}'

    monkeypatch.setattr(database, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(new_api_client, "NewAPIClient", DummyClient)
    monkeypatch.setattr("creatures.nanobot.prompts.skills.group_analysis.analyzer._call_llm_with_retry", fake_call)

    tool = GroupAnalysisTool()
    result = run_async(tool.execute({"group_id": "123", "instructions": "最近2小时"}))

    assert result.success
    from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
    payload = json.loads(result.output)
    html = payload[REPLY_MARKER]["content"]
    assert "<!DOCTYPE html>" in html
    assert "group-analysis-report" in html
    assert "消息总数" in html
    assert "24H 活跃轨迹" in html
    assert "话题总结" in html
    assert "聊天质量锐评" in html
    assert "INTP" in html


def test_group_analysis_tool_filters_artifacts_before_llm(monkeypatch):
    import core.database as database
    import clients.new_api_client as new_api_client
    from core.database import ChatLog, User
    from creatures.nanobot.prompts.skills.group_analysis.tool import GroupAnalysisTool

    now = datetime.now()
    logs = [
        ChatLog(user_id="group_123", session_id="group_123", sender_name="A", role="ambient",
                content="今天聊 AI", created_at=now - timedelta(minutes=30)),
        ChatLog(user_id="group_123", session_id="group_123", sender_name="B", role="ambient",
                content="价格确实降了", created_at=now - timedelta(minutes=20)),
        ChatLog(user_id="group_123", session_id="group_123", sender_name="C", role="ambient",
                content="benchmark 也反转了", created_at=now - timedelta(minutes=10)),
        ChatLog(user_id="group_123", session_id="group_123", sender_name="nanobot", role="assistant",
                content='<!DOCTYPE html><html><body class="group-analysis-report">旧日报</body></html>',
                created_at=now - timedelta(minutes=5)),
        ChatLog(user_id="group_123", session_id="group_123", role="system",
                content="[NO_SEND] agent_result=no_tool_call", meta_json='{"no_send": true}',
                created_at=now - timedelta(minutes=4)),
    ]
    user = User(id="group_123", name="测试群")
    prompts = []

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

    class FakeQuery:
        def __init__(self, dataset):
            self.dataset = dataset

        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def limit(self, *args, **kwargs):
            return self

        def all(self):
            return list(self.dataset) if isinstance(self.dataset, list) else []

        def first(self):
            if isinstance(self.dataset, list):
                return self.dataset[0] if self.dataset else None
            return self.dataset

    class FakeSession:
        def query(self, model):
            model_name = getattr(model, "__name__", "")
            if model_name == "ChatLog":
                return FakeQuery(logs)
            if model_name == "User":
                return FakeQuery(user)
            return FakeQuery(None)

        def close(self):
            return None

    async def fake_call(_client, _system_prompt, prompt, max_retries=2):
        prompts.append(prompt)
        assert "旧日报" not in prompt
        assert "[NO_SEND]" not in prompt
        assert "今天聊 AI" in prompt
        if "核心讨论话题" in prompt:
            return '{"topics":[{"topic":"AI 模型","contributors":["A","B"],"detail":"大家在讨论价格。"}]}'
        if "用户发言统计" in prompt:
            return '{"users":[{"user_id":"A","title":"观察员","mbti":"","reason":"持续参与讨论"}]}'
        if "聊天质量" in prompt:
            return '{"title":"质量在线","subtitle":"","dimensions":[],"summary":"讨论集中。"}'
        return '{"quotes":[{"user_id":"B","content":"价格确实降了"}]}'

    monkeypatch.setattr(database, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(new_api_client, "NewAPIClient", DummyClient)
    monkeypatch.setattr("creatures.nanobot.prompts.skills.group_analysis.analyzer._call_llm_with_retry", fake_call)

    tool = GroupAnalysisTool()
    result = run_async(tool.execute({"group_id": "123", "window_hours": 24}))

    assert result.success
    assert prompts


def test_group_analysis_uses_deterministic_fallback_when_llm_fails(monkeypatch):
    import clients.new_api_client as new_api_client
    from creatures.nanobot.prompts.skills.group_analysis.analyzer import analyze_group
    from creatures.nanobot.prompts.skills.group_analysis.preprocess import build_analysis_payload
    from creatures.nanobot.prompts.skills.group_analysis.schemas import RawChatLog

    now = datetime.now()
    logs = [
        RawChatLog(id=1, role="ambient", user_id="u1", sender_name="A",
                   content="今天 AI 模型价格又降了，benchmark 也反转了", created_at=now),
        RawChatLog(id=2, role="ambient", user_id="u2", sender_name="B",
                   content="这张图太好笑了 [图片:1张]", created_at=now),
        RawChatLog(id=3, role="ambient", user_id="u1", sender_name="A",
                   content="我觉得这个 API 方案可以继续压成本", created_at=now),
        RawChatLog(id=4, role="ambient", user_id="u3", sender_name="C",
                   content="笑死，今天群里信息量有点大", created_at=now),
    ]
    payload = build_analysis_payload(logs)

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

    async def fail_call(*args, **kwargs):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(new_api_client, "NewAPIClient", DummyClient)
    monkeypatch.setattr("creatures.nanobot.prompts.skills.group_analysis.analyzer._call_llm_with_retry", fail_call)

    result = run_async(analyze_group(payload, ""))

    assert result["topics"]["topics"]
    assert result["titles"]["users"]
    assert result["quotes"]["quotes"]
    assert result["quality"]["dimensions"]
    assert "降级" in result["quality"]["subtitle"]
