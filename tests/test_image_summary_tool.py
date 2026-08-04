from tests.async_helpers import run_async
import json
from unittest.mock import MagicMock, patch


def _mock_qwen_response(content: str) -> MagicMock:
    mock = MagicMock()
    mock.read.return_value = json.dumps(
        {
            "choices": [{"message": {"content": content}}],
            "usage": {},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def test_tool_metadata():
    from nanobot_kt.tools.image_summary import ImageSummaryTool

    tool = ImageSummaryTool()
    assert tool.tool_name == "image_summary"
    assert "摘要" in tool.description

    schema = tool.get_parameters_schema()
    assert "files" in schema["required"]
    assert "focus" in schema["properties"]


def test_execute_requires_images():
    from nanobot_kt.tools.image_summary import ImageSummaryTool

    tool = ImageSummaryTool()
    result = run_async(tool.execute({"files": []}))
    assert not result.success
    assert "files" in result.error.lower()


def test_execute_uses_to_thread_for_qwen_call(monkeypatch):
    import nanobot_kt.tools.image_summary as image_tool
    from nanobot_kt.tools.image_summary import ImageSummaryTool

    calls = []

    def fake_call_qwen(files, focus):
        return json.dumps(
            {
                "overall_summary": "线程摘要",
                "per_image": [],
                "keywords": [],
                "risk_flags": [],
                "confidence": "high",
            },
            ensure_ascii=False,
        )

    async def fake_to_thread(func, *args, **kwargs):
        calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    tool = ImageSummaryTool()
    monkeypatch.setattr(tool, "_call_qwen", fake_call_qwen)
    monkeypatch.setattr(image_tool.asyncio, "to_thread", fake_to_thread)

    result = run_async(tool.execute({"files": ["https://example.com/cat.png"], "focus": "主体"}))

    assert result.success
    assert calls == [(fake_call_qwen, (["https://example.com/cat.png"], "主体"), {})]


def test_execute_calls_local_qwen_with_multimodal_payload():
    from nanobot_kt.tools.image_summary import ImageSummaryTool
    from kohakuterrarium.llm.message import ImagePart

    mock_response = _mock_qwen_response(
        json.dumps(
            {
                "image_count": 1,
                "overall_summary": "一只猫坐在桌子上",
                "per_image": [
                    {
                        "index": 1,
                        "summary": "画面主体是一只猫",
                        "text": [],
                        "objects": ["猫", "桌子"],
                        "scene": "室内",
                        "uncertainties": [],
                    }
                ],
                "keywords": ["猫", "桌子"],
                "risk_flags": [],
                "confidence": "high",
            },
            ensure_ascii=False,
        )
    )

    tool = ImageSummaryTool()

    with patch(
        "nanobot_kt.tools.image_summary.prepare_image_parts",
        return_value=[
            ImagePart(
                url="data:image/jpeg;base64,ZmFrZQ==",
                detail="low",
                source_type="image_summary",
                source_name="image_summary_1",
            )
        ],
    ) as mock_prepare:
        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_opener = MagicMock()
            mock_opener.open.return_value = mock_response
            mock_build_opener.return_value = mock_opener

            result = run_async(
                tool.execute(
                    {
                        "files": ["https://example.com/cat.png"],
                        "focus": "识别主体",
                    }
                )
            )

    assert result.success
    data = json.loads(result.output)
    assert data["overall_summary"] == "一只猫坐在桌子上"

    mock_prepare.assert_called_once_with(
        ["https://example.com/cat.png"],
        source_type="image_summary",
        source_name_prefix="image_summary",
        detail="low",
    )
    mock_opener.open.assert_called_once()
    req = mock_opener.open.call_args.args[0]
    assert req.full_url.endswith("/chat/completions")
    payload = json.loads(req.data.decode("utf-8"))
    assert payload["max_tokens"] >= 256
    assert payload["temperature"] <= 0.2
    assert payload["top_p"] <= 1.0
    user_content = payload["messages"][1]["content"]
    assert isinstance(user_content, list)
    assert user_content[0]["type"] == "text"
    assert user_content[1]["type"] == "image_url"
    assert user_content[1]["image_url"]["url"] == "data:image/jpeg;base64,ZmFrZQ=="


def test_execute_records_direct_llm_request():
    from core.llm_trace_context import llm_trace_scope
    from nanobot_kt.tools.image_summary import ImageSummaryTool
    from kohakuterrarium.llm.message import ImagePart

    recorded = []
    mock_response = _mock_qwen_response(
        '{"overall_summary":"测试","per_image":[],"keywords":[],"risk_flags":[],"confidence":"high"}'
    )

    with patch(
        "core.tracing.LLMRequestTracer.record_request",
        side_effect=lambda **kwargs: recorded.append(kwargs),
    ):
        with patch(
            "nanobot_kt.tools.image_summary._get_image_summary_route",
            return_value={
                "base_url": "http://vision.test/v1",
                "api_key": "vision-key",
                "model": "vision-model",
                "max_tokens": 512,
                "temperature": 0.1,
                "timeout": 5,
                "provider_id": "local_vision",
                "enabled": True,
            },
        ):
            with patch(
                "nanobot_kt.tools.image_summary.prepare_image_parts",
                return_value=[
                    ImagePart(
                        url="data:image/jpeg;base64,ZmFrZQ==",
                        detail="low",
                        source_type="image_summary",
                        source_name="image_summary_1",
                    )
                ],
            ):
                with patch("urllib.request.build_opener") as mock_build_opener:
                    mock_opener = MagicMock()
                    mock_opener.open.return_value = mock_response
                    mock_build_opener.return_value = mock_opener

                    with llm_trace_scope(trace_id="trace-img", run_id="run-img", source="replyer"):
                        result = run_async(
                            ImageSummaryTool().execute({"files": ["https://example.com/a.png"]})
                        )

    assert result.success
    assert recorded
    row = recorded[0]
    assert row["trace_id"] == "trace-img"
    assert row["run_id"] == "run-img"
    assert row["source"] == "image_summary.tool"
    assert row["provider"] == "local_vision"
    assert row["model"] == "vision-model"
    assert row["url"] == "http://vision.test/v1/chat/completions"
    assert row["request"]["messages"][1]["content"][1]["type"] == "image_url"


def test_execute_accepts_json_codeblock():
    from nanobot_kt.tools.image_summary import ImageSummaryTool
    from kohakuterrarium.llm.message import ImagePart

    mock_response = _mock_qwen_response(
        """```json
        {"overall_summary":"测试","per_image":[],"keywords":[],"risk_flags":[],"confidence":"medium"}
        ```"""
    )
    tool = ImageSummaryTool()

    with patch(
        "nanobot_kt.tools.image_summary.prepare_image_parts",
        return_value=[
            ImagePart(
                url="data:image/jpeg;base64,ZmFrZQ==",
                detail="low",
                source_type="image_summary",
                source_name="image_summary_1",
            )
        ],
    ):
        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_opener = MagicMock()
            mock_opener.open.return_value = mock_response
            mock_build_opener.return_value = mock_opener
            result = run_async(tool.execute({"files": ["https://example.com/cat.png"]}))

    assert result.success
    data = json.loads(result.output)
    assert data["overall_summary"] == "测试"
    assert data["image_count"] == 1
