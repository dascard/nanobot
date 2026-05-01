from datetime import datetime, timedelta
import asyncio


def test_clean_message_filters_commands_and_noise():
    from creatures.nanobot.prompts.skills.group_analysis.tool import _clean_message

    assert _clean_message("/group_analysis") is None
    assert _clean_message("<@123456> /help") is None
    assert _clean_message("@bot") is None
    assert _clean_message("  ++==  ") is None
    assert _clean_message("https://example.com") is None
    assert _clean_message("正常聊天内容") == "正常聊天内容"


def test_parse_instruction_window_hours():
    from creatures.nanobot.prompts.skills.group_analysis.tool import _parse_instruction_window_hours

    assert _parse_instruction_window_hours("最近2小时") == 2
    assert _parse_instruction_window_hours("只看最近6小时") == 6
    assert _parse_instruction_window_hours("最近1天") == 24
    assert _parse_instruction_window_hours("随便看看") is None


def test_compute_group_statistics_returns_summary():
    from creatures.nanobot.prompts.skills.group_analysis.tool import _compute_group_statistics

    messages = [
        {"user_id": "A", "content": "早上好😂", "hour": 9, "is_reply": False},
        {"user_id": "B", "content": "今天真热闹🔥", "hour": 9, "is_reply": True},
        {"user_id": "A", "content": "晚上继续聊", "hour": 21, "is_reply": False},
    ]

    stats = _compute_group_statistics(messages)

    assert stats["message_count"] == 3
    assert stats["participant_count"] == 2
    assert stats["total_characters"] == len("早上好😂今天真热闹🔥晚上继续聊")
    assert stats["average_message_length"] > 0
    assert stats["most_active_period"] == "09:00-10:00"
    assert stats["emoji_count"] == 2
    assert stats["hourly_counts"][9] == 2


def test_format_group_report_includes_statistics_quality_and_mbti():
    from creatures.nanobot.prompts.skills.group_analysis.tool import _format_scrapbook_html

    report = _format_scrapbook_html(
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
    from creatures.nanobot.prompts.skills.group_analysis.tool import _format_scrapbook_html

    report = _format_scrapbook_html(
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
    from creatures.nanobot.prompts.skills.group_analysis.tool import _build_message_prompt_text

    messages = [
        {
            "time": f"{idx % 24:02d}:00",
            "user_id": f"用户{idx % 60}",
            "content": f"第{idx}条消息，讨论话题{idx % 7}，包含足够上下文。",
        }
        for idx in range(2200)
    ]

    text = _build_message_prompt_text(messages, max_chars=12000)

    assert "原始可分析消息总数: 2200" in text
    assert "第2199条消息" in text
    assert len(text) <= 12000


def test_filter_messages_by_hours_keeps_recent_messages():
    from creatures.nanobot.prompts.skills.group_analysis.tool import _filter_messages_by_hours

    now = datetime.now()
    logs = [
        {"created_at": now - timedelta(hours=1), "content": "recent"},
        {"created_at": now - timedelta(hours=8), "content": "old"},
    ]

    filtered = _filter_messages_by_hours(logs, 2, now=now)

    assert len(filtered) == 1
    assert filtered[0]["content"] == "recent"


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
    monkeypatch.setattr("creatures.nanobot.prompts.skills.group_analysis.tool._call_llm_with_retry", fake_call)

    tool = GroupAnalysisTool()
    result = asyncio.run(tool.execute({"group_id": "123", "instructions": "最近2小时"}))

    assert result.success
    assert "<!DOCTYPE html>" in result.output
    assert "group-analysis-report" in result.output
    assert "消息总数" in result.output
    assert "24H 活跃轨迹" in result.output
    assert "话题总结" in result.output
    assert "聊天质量锐评" in result.output
    assert "INTP" in result.output
