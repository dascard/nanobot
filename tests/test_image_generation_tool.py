import asyncio
import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch


class _MockSSEResponse:
    status = 200

    def __init__(self, lines: list[str]):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return iter(line.encode("utf-8") for line in self._lines)

    def getcode(self):
        return self.status


def _sse_data(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False)


def test_tool_metadata_and_schema():
    from creatures.nanobot.prompts.skills.image_generation.tool import ImageGenerationTool

    tool = ImageGenerationTool()

    assert tool.tool_name == "image_generation"
    assert "生成图片" in tool.description

    schema = tool.get_parameters_schema()
    assert schema["required"] == ["prompt"]
    assert "prompt" in schema["properties"]
    assert schema["properties"]["size"]["default"] == "1024x1024"
    assert "1024x1024" in schema["properties"]["size"]["enum"]


def test_execute_requires_prompt():
    from creatures.nanobot.prompts.skills.image_generation.tool import ImageGenerationTool

    result = asyncio.run(ImageGenerationTool().execute({"prompt": "  "}))

    assert not result.success
    assert "prompt" in result.error.lower()


def test_execute_calls_new_api_responses_and_returns_generated_image_token(monkeypatch, tmp_path):
    from core import generated_images
    from creatures.nanobot.prompts.skills.image_generation import tool as image_tool
    from creatures.nanobot.prompts.skills.image_generation.tool import ImageGenerationTool

    image_b64 = base64.b64encode(b"fake-png").decode("ascii")
    response = _MockSSEResponse(
        [
            "event: response.output_text.delta",
            _sse_data({"type": "response.output_text.delta", "delta": "正在生成"}),
            "event: response.output_item.done",
            _sse_data(
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "image_generation_call",
                        "result": image_b64,
                    },
                }
            ),
            "data: [DONE]",
        ]
    )
    mock_opener = MagicMock()
    mock_opener.open.return_value = response

    monkeypatch.setattr(image_tool, "NEW_API_KEY", "test-key")
    monkeypatch.setattr(image_tool, "NEW_API_BASE_URL", "http://new-api.test/v1")
    monkeypatch.setattr(image_tool, "IMAGE_GENERATION_MODEL", "gpt-image")
    monkeypatch.setattr(image_tool, "IMAGE_GENERATION_TIMEOUT", 600.0)
    monkeypatch.setattr(generated_images, "GENERATED_IMAGE_DIR", str(tmp_path))

    with patch("urllib.request.build_opener", return_value=mock_opener):
        result = asyncio.run(
            ImageGenerationTool().execute(
                {
                    "prompt": "Generate a cute red panda drinking boba tea, sticker style.",
                    "size": "1024x1024",
                    "quality": "high",
                }
            )
        )

    assert result.success
    payload = json.loads(result.output)
    assert payload["reply_token"].startswith("[generated_image:")
    assert "send_code" not in payload
    assert payload["mime"] == "image/png"
    assert payload["model"] == "gpt-image"
    assert payload["text_output"] == "正在生成"
    saved_path = Path(payload["saved_path"])
    assert saved_path.parent == tmp_path
    assert saved_path.read_bytes() == b"fake-png"

    mock_opener.open.assert_called_once()
    req = mock_opener.open.call_args.args[0]
    assert req.full_url == "http://new-api.test/v1/responses"
    assert req.headers["Authorization"] == "Bearer test-key"
    assert req.headers["Content-type"] == "application/json"
    assert req.headers["Accept"] == "text/event-stream"
    assert mock_opener.open.call_args.kwargs["timeout"] == 600.0

    request_payload = json.loads(req.data.decode("utf-8"))
    assert request_payload["model"] == "gpt-image"
    assert request_payload["stream"] is True
    assert request_payload["store"] is False
    assert request_payload["tool_choice"] == "auto"
    assert request_payload["input"][0]["content"][0]["text"].startswith("Generate a cute red panda")
    assert request_payload["tools"] == [
        {
            "type": "image_generation",
            "output_format": "png",
            "size": "1024x1024",
            "quality": "high",
            "background": "auto",
            "action": "generate",
        }
    ]


def test_reply_tool_expands_generated_image_token(monkeypatch, tmp_path):
    from core import generated_images
    from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER, ReplyTool

    monkeypatch.setattr(generated_images, "GENERATED_IMAGE_DIR", str(tmp_path))
    saved = generated_images.save_generated_image(
        base64.b64encode(b"fake-png").decode("ascii"),
        prompt="画一只猫",
    )

    result = asyncio.run(ReplyTool()._execute({"content": saved["reply_token"]}))

    assert not result.error
    payload = json.loads(result.output)
    assert payload[REPLY_MARKER]["content"] == (
        "[CQ:image,file=base64://ZmFrZS1wbmc=]"
    )


def test_generated_image_metadata_list_and_search(monkeypatch, tmp_path):
    from core import generated_images

    monkeypatch.setattr(generated_images, "GENERATED_IMAGE_DIR", str(tmp_path))
    first = generated_images.save_generated_image(
        base64.b64encode(b"first-png").decode("ascii"),
        prompt="画一只猫在键盘旁边睡觉",
        metadata={
            "model": "gpt-image",
            "size": "1024x1024",
            "quality": "high",
            "background": "auto",
        },
    )
    second = generated_images.save_generated_image(
        base64.b64encode(b"second-png").decode("ascii"),
        prompt="赛博城市夜景",
        metadata={"model": "gpt-image", "size": "1536x1024"},
    )

    meta_path = tmp_path / f"{first['id']}.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["prompt"] == "画一只猫在键盘旁边睡觉"
    assert meta["model"] == "gpt-image"
    assert meta["size"] == "1024x1024"
    assert meta["quality"] == "high"
    assert meta["reply_token"] == first["reply_token"]

    listed = generated_images.list_generated_images(page=1, limit=20)
    assert listed["total"] == 2
    assert [item["id"] for item in listed["items"]] == [second["id"], first["id"]]

    searched = generated_images.list_generated_images(page=1, limit=20, search="键盘")
    assert searched["total"] == 1
    assert searched["items"][0]["id"] == first["id"]
    assert generated_images.get_generated_image_path(first["id"]) == str(tmp_path / f"{first['id']}.png")


def test_execute_reports_missing_image_result(monkeypatch):
    from creatures.nanobot.prompts.skills.image_generation import tool as image_tool
    from creatures.nanobot.prompts.skills.image_generation.tool import ImageGenerationTool

    response = _MockSSEResponse(
        [
            _sse_data({"type": "response.output_text.delta", "delta": "没有图片"}),
            "data: [DONE]",
        ]
    )
    mock_opener = MagicMock()
    mock_opener.open.return_value = response

    monkeypatch.setattr(image_tool, "NEW_API_KEY", "test-key")
    monkeypatch.setattr(image_tool, "NEW_API_BASE_URL", "http://new-api.test/v1")

    with patch("urllib.request.build_opener", return_value=mock_opener):
        result = asyncio.run(ImageGenerationTool().execute({"prompt": "画一只猫"}))

    assert not result.success
    assert "image_generation_call" in result.error


def test_image_generation_tool_is_registered():
    from core.runtime_tool_service import resolve_effective_tools, resolve_lightweight_default
    from core.tool_registry import TOOL_METADATA
    from core.tool_schema_preview import build_tool_schema

    config_text = Path("creatures/nanobot/config.yaml").read_text(encoding="utf-8")
    assert "name: image_generation" in config_text
    assert "module: nanobot_kt.tools.image_generation" in config_text
    assert "class: ImageGenerationTool" in config_text

    assert "image_generation" in TOOL_METADATA
    assert TOOL_METADATA["image_generation"].private_default is True
    assert TOOL_METADATA["image_generation"].group_default is True

    schema = build_tool_schema("image_generation")
    assert schema["function"]["name"] == "image_generation"
    assert "prompt" in schema["function"]["parameters"]["properties"]

    enabled, _disabled = resolve_effective_tools(
        chat_type="private",
        runtime_preset="lightweight",
    )
    assert enabled["image_generation"] is True
    assert resolve_lightweight_default("image_generation") is True
