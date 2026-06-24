from tests.async_helpers import run_async
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


# ── 基础元数据测试 ──

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
    # quality 默认 high
    assert schema["properties"]["quality"]["default"] == "high"
    # prompt 应有 maxLength
    assert "maxLength" in schema["properties"]["prompt"]


def test_execute_requires_prompt():
    from creatures.nanobot.prompts.skills.image_generation.tool import ImageGenerationTool

    result = run_async(ImageGenerationTool().execute({"prompt": "  "}))

    assert not result.success
    assert "prompt" in result.error.lower()


def test_execute_rejects_too_long_prompt(monkeypatch):
    from creatures.nanobot.prompts.skills.image_generation import tool as image_tool
    from creatures.nanobot.prompts.skills.image_generation.tool import ImageGenerationTool

    monkeypatch.setattr(image_tool, "IMAGE_GENERATION_PROMPT_MAX_CHARS", 50)

    result = run_async(ImageGenerationTool().execute({"prompt": "x" * 60}))

    assert not result.success
    assert "too long" in result.error.lower()


def test_execute_uses_to_thread_for_new_api(monkeypatch, tmp_path):
    from core import generated_images
    from creatures.nanobot.prompts.skills.image_generation import tool as image_tool
    from creatures.nanobot.prompts.skills.image_generation.tool import ImageGenerationTool

    png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    image_b64 = base64.b64encode(png_data).decode("ascii")
    calls = []

    def fake_call_new_api(*, prompt, size, quality, background):
        return {
            "image_b64": image_b64,
            "revised_prompt": prompt,
            "text_output": "ok",
        }

    async def fake_to_thread(func, *args, **kwargs):
        calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(generated_images, "GENERATED_IMAGE_DIR", str(tmp_path))
    monkeypatch.setattr(image_tool.asyncio, "to_thread", fake_to_thread)

    tool = ImageGenerationTool()
    monkeypatch.setattr(tool, "_call_new_api", fake_call_new_api)

    result = run_async(
        tool.execute(
            {
                "prompt": "画一只猫",
                "size": "1024x1024",
                "quality": "high",
                "background": "auto",
            }
        )
    )

    assert result.success
    assert calls
    func, args, kwargs = calls[0]
    assert func is fake_call_new_api
    assert args == ()
    assert kwargs == {
        "prompt": "画一只猫",
        "size": "1024x1024",
        "quality": "high",
        "background": "auto",
    }


# ── 标准 output_item.done 成功 ──

def test_execute_calls_new_api_responses_and_returns_generated_image_token(monkeypatch, tmp_path):
    from core import generated_images
    from creatures.nanobot.prompts.skills.image_generation import tool as image_tool
    from creatures.nanobot.prompts.skills.image_generation.tool import ImageGenerationTool

    # 使用有效 PNG 魔数
    png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    image_b64 = base64.b64encode(png_data).decode("ascii")
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
        result = run_async(
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
    assert saved_path.read_bytes() == png_data

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
    # 不再包含 action 参数
    assert request_payload["tools"] == [
        {
            "type": "image_generation",
            "output_format": "png",
            "size": "1024x1024",
            "quality": "high",
            "background": "auto",
        }
    ]


# ── SSE partial_image 成功 ──

def test_execute_partial_image_then_completed(monkeypatch, tmp_path):
    from core import generated_images
    from creatures.nanobot.prompts.skills.image_generation import tool as image_tool
    from creatures.nanobot.prompts.skills.image_generation.tool import ImageGenerationTool

    png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    image_b64 = base64.b64encode(png_data).decode("ascii")
    response = _MockSSEResponse(
        [
            _sse_data({
                "type": "response.image_generation_call.partial_image",
                "partial_image_b64": image_b64,
            }),
            _sse_data({
                "type": "response.completed",
                "response": {"status": "completed", "output": []},
            }),
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
        result = run_async(ImageGenerationTool().execute({"prompt": "画一只猫"}))

    assert result.success
    payload = json.loads(result.output)
    assert payload["reply_token"].startswith("[generated_image:")


# ── completed.output 聚合成功 ──

def test_execute_completed_output_aggregation(monkeypatch, tmp_path):
    from core import generated_images
    from creatures.nanobot.prompts.skills.image_generation import tool as image_tool
    from creatures.nanobot.prompts.skills.image_generation.tool import ImageGenerationTool

    png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    image_b64 = base64.b64encode(png_data).decode("ascii")
    response = _MockSSEResponse(
        [
            _sse_data({
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": [
                        {
                            "type": "image_generation_call",
                            "result": image_b64,
                        }
                    ],
                },
            }),
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
        result = run_async(ImageGenerationTool().execute({"prompt": "画一只猫"}))

    assert result.success
    payload = json.loads(result.output)
    assert payload["reply_token"].startswith("[generated_image:")


# ── 失败分型测试 ──


def test_execute_moderation_blocked(monkeypatch):
    """模拟 content_filter 违规——断言错误信息包含 blocked or incomplete。"""
    from creatures.nanobot.prompts.skills.image_generation import tool as image_tool
    from creatures.nanobot.prompts.skills.image_generation.tool import ImageGenerationTool

    response = _MockSSEResponse(
        [
            _sse_data({
                "type": "response.completed",
                "response": {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "content_filter"},
                },
            }),
        ]
    )
    mock_opener = MagicMock()
    mock_opener.open.return_value = response

    monkeypatch.setattr(image_tool, "NEW_API_KEY", "test-key")
    monkeypatch.setattr(image_tool, "NEW_API_BASE_URL", "http://new-api.test/v1")

    with patch("urllib.request.build_opener", return_value=mock_opener):
        result = run_async(ImageGenerationTool().execute({"prompt": "画违规内容"}))

    assert not result.success
    assert "blocked or incomplete" in result.error


def test_execute_upstream_error_event(monkeypatch):
    """模拟 response.failed 事件。"""
    from creatures.nanobot.prompts.skills.image_generation import tool as image_tool
    from creatures.nanobot.prompts.skills.image_generation.tool import ImageGenerationTool

    response = _MockSSEResponse(
        [
            _sse_data({
                "type": "response.failed",
                "error": {"code": "server_error", "message": "upstream timeout"},
            }),
        ]
    )
    mock_opener = MagicMock()
    mock_opener.open.return_value = response

    monkeypatch.setattr(image_tool, "NEW_API_KEY", "test-key")
    monkeypatch.setattr(image_tool, "NEW_API_BASE_URL", "http://new-api.test/v1")

    with patch("urllib.request.build_opener", return_value=mock_opener):
        result = run_async(ImageGenerationTool().execute({"prompt": "画一只猫"}))

    assert not result.success
    assert "upstream error" in result.error


def test_execute_tool_usage_but_no_output(monkeypatch):
    """模拟 new-api 聚合失败：tool_usage 有统计但 output 为空。"""
    from creatures.nanobot.prompts.skills.image_generation import tool as image_tool
    from creatures.nanobot.prompts.skills.image_generation.tool import ImageGenerationTool

    response = _MockSSEResponse(
        [
            _sse_data({
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": [],
                    "tool_usage": {
                        "image_gen": {"output_tokens": 1756}
                    },
                },
            }),
        ]
    )
    mock_opener = MagicMock()
    mock_opener.open.return_value = response

    monkeypatch.setattr(image_tool, "NEW_API_KEY", "test-key")
    monkeypatch.setattr(image_tool, "NEW_API_BASE_URL", "http://new-api.test/v1")

    with patch("urllib.request.build_opener", return_value=mock_opener):
        result = run_async(ImageGenerationTool().execute({"prompt": "画一只猫"}))

    assert not result.success
    assert "possible upstream aggregation" in result.error


def test_execute_reports_missing_image_result(monkeypatch):
    """完全没有生图事件。"""
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
        result = run_async(ImageGenerationTool().execute({"prompt": "画一只猫"}))

    assert not result.success
    assert "missing image_generation_call" in result.error


def test_execute_rejects_non_png_result(monkeypatch, tmp_path):
    """校验 PNG 魔数——非 PNG 数据应报错。"""
    from core import generated_images
    from creatures.nanobot.prompts.skills.image_generation import tool as image_tool
    from creatures.nanobot.prompts.skills.image_generation.tool import ImageGenerationTool

    # GIF89a 开头，不是 PNG
    non_png = base64.b64encode(b"GIF89a\x00\x00\x00").decode("ascii")
    response = _MockSSEResponse(
        [
            _sse_data({
                "type": "response.output_item.done",
                "item": {"type": "image_generation_call", "result": non_png},
            }),
        ]
    )
    mock_opener = MagicMock()
    mock_opener.open.return_value = response

    monkeypatch.setattr(image_tool, "NEW_API_KEY", "test-key")
    monkeypatch.setattr(image_tool, "NEW_API_BASE_URL", "http://new-api.test/v1")
    monkeypatch.setattr(generated_images, "GENERATED_IMAGE_DIR", str(tmp_path))

    with patch("urllib.request.build_opener", return_value=mock_opener):
        result = run_async(ImageGenerationTool().execute({"prompt": "画一只猫"}))

    assert not result.success
    assert "not a PNG" in result.error


# ── reply 工具不展开图片 token ──

def test_reply_tool_keeps_short_generated_image_token(monkeypatch, tmp_path):
    """reply 工具不再展开 [generated_image:...]——保留短 token 给传输层处理。"""
    from core import generated_images
    from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER, ReplyTool

    monkeypatch.setattr(generated_images, "GENERATED_IMAGE_DIR", str(tmp_path))
    png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    saved = generated_images.save_generated_image(
        base64.b64encode(png_data).decode("ascii"),
        prompt="画一只猫",
    )

    result = run_async(ReplyTool()._execute({"content": saved["reply_token"]}))

    assert not result.error
    payload = json.loads(result.output)
    # 应保留短 token，不展开成 CQ base64
    assert payload[REPLY_MARKER]["content"] == saved["reply_token"]


# ── 传输层展开测试 ──

def test_transport_layer_expands_to_cq_url_when_public_base_url_configured(monkeypatch, tmp_path):
    """有 NANOBOT_PUBLIC_BASE_URL 时应展开为短 CQ URL。"""
    from core import generated_images

    monkeypatch.setattr(generated_images, "GENERATED_IMAGE_DIR", str(tmp_path))
    monkeypatch.setenv("NANOBOT_PUBLIC_BASE_URL", "http://nanobot.test:8000")
    monkeypatch.setenv("NANOBOT_GENERATED_IMAGE_TOKEN", "test-token")

    png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    saved = generated_images.save_generated_image(
        base64.b64encode(png_data).decode("ascii"),
        prompt="画一只猫",
    )

    expanded = generated_images.expand_generated_image_refs_in_content(saved["reply_token"])
    assert expanded.startswith("[CQ:image,file=http://nanobot.test:8000")
    assert f"/api/v1/generated-images/{saved['id']}/image?token=test-token" in expanded
    # 不应包含 base64
    assert "base64://" not in expanded


def test_transport_layer_expands_to_base64_when_no_public_url(monkeypatch, tmp_path):
    """无 NANOBOT_PUBLIC_BASE_URL 时回退 base64。"""
    from core import generated_images

    monkeypatch.setattr(generated_images, "GENERATED_IMAGE_DIR", str(tmp_path))
    monkeypatch.delenv("NANOBOT_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("NANOBOT_PUBLIC_BASE_URL", "")

    png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    saved = generated_images.save_generated_image(
        base64.b64encode(png_data).decode("ascii"),
        prompt="画一只猫",
    )

    expanded = generated_images.expand_generated_image_refs_in_content(saved["reply_token"])
    assert expanded.startswith("[CQ:image,file=base64://")
    assert base64.b64encode(png_data).decode("ascii") in expanded


def test_transport_layer_allow_base64_false_keeps_short_token(monkeypatch, tmp_path):
    """allow_base64=False 且无 public URL 时保留短 token。"""
    from core import generated_images

    monkeypatch.setattr(generated_images, "GENERATED_IMAGE_DIR", str(tmp_path))
    monkeypatch.delenv("NANOBOT_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("NANOBOT_PUBLIC_BASE_URL", "")

    png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    saved = generated_images.save_generated_image(
        base64.b64encode(png_data).decode("ascii"),
        prompt="画一只猫",
    )

    expanded = generated_images.expand_generated_image_refs_in_content(
        saved["reply_token"], allow_base64=False
    )
    # 保留短 token，不展开
    assert expanded == saved["reply_token"]


# ── generated_images 元数据和清理 ──

def test_generated_image_metadata_list_and_search(monkeypatch, tmp_path):
    from core import generated_images

    monkeypatch.setattr(generated_images, "GENERATED_IMAGE_DIR", str(tmp_path))
    png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    first = generated_images.save_generated_image(
        base64.b64encode(png_data).decode("ascii"),
        prompt="画一只猫在键盘旁边睡觉",
        metadata={
            "model": "gpt-image",
            "size": "1024x1024",
            "quality": "high",
            "background": "auto",
        },
    )
    second = generated_images.save_generated_image(
        base64.b64encode(png_data).decode("ascii"),
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


def test_cleanup_generated_images_by_ttl(monkeypatch, tmp_path):
    """清理超过 TTL 的图片。"""
    from core import generated_images

    monkeypatch.setattr(generated_images, "GENERATED_IMAGE_DIR", str(tmp_path))
    png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    generated_images.save_generated_image(
        base64.b64encode(png_data).decode("ascii"),
        prompt="测试图片",
    )

    # 模拟所有文件已过期（TTL=0 天）
    result = generated_images.cleanup_generated_images(ttl_days=0, max_files=500)
    assert result["deleted"] >= 1
    assert result["remaining"] == 0


def test_cleanup_generated_images_by_max_files(monkeypatch, tmp_path):
    """超过 max_files 时删除最旧的。"""
    from core import generated_images

    monkeypatch.setattr(generated_images, "GENERATED_IMAGE_DIR", str(tmp_path))
    png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    for i in range(5):
        generated_images.save_generated_image(
            base64.b64encode(png_data).decode("ascii"),
            prompt=f"测试图片{i}",
        )

    result = generated_images.cleanup_generated_images(ttl_days=365, max_files=2)
    assert result["deleted"] >= 3
    assert result["remaining"] <= 2


# ── Bridge runtime cache 兜底测试 ──

def test_runtime_cache_set_and_get():
    """runtime cache set/get 基本行为。"""
    from core.reply_runtime_cache import set_last_reply, get_last_reply, clear_last_reply

    clear_last_reply()
    text, meta = get_last_reply()
    assert text == ""
    assert meta == {}

    set_last_reply("hello world", {"reply_to_message_id": "123"})
    text, meta = get_last_reply()
    assert text == "hello world"
    assert meta == {"reply_to_message_id": "123"}

    clear_last_reply()
    text, meta = get_last_reply()
    assert text == ""
    assert meta == {}


# ── 工具注册检查 ──

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
