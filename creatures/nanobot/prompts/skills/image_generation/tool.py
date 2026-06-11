"""Image generation tool — 通过 new-api Responses 接口生成可发送图片。"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import urllib.error
import urllib.request
from typing import Any, Iterator

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

from config import (
    IMAGE_GENERATION_MODEL,
    IMAGE_GENERATION_TIMEOUT,
    NEW_API_BASE_URL,
    NEW_API_KEY,
)
from core.generated_images import save_generated_image


logger = logging.getLogger("nanobot.tool.image_generation")

_ALLOWED_SIZES = {"1024x1024", "1024x1536", "1536x1024", "auto"}
_ALLOWED_QUALITIES = {"low", "medium", "high", "auto"}
_ALLOWED_BACKGROUNDS = {"auto", "transparent", "opaque"}


def _normalize_option(value: Any, allowed: set[str], default: str) -> str:
    item = str(value or default).strip()
    return item if item in allowed else default


def _compact_b64(value: str) -> str:
    return "".join(str(value or "").split())


def _extract_image_b64(item: dict[str, Any]) -> str:
    candidates: list[Any] = [
        item.get("result"),
        item.get("b64_json"),
        item.get("image_base64"),
    ]
    result = item.get("result")
    if isinstance(result, list):
        candidates.extend(result)
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return _compact_b64(candidate)
        if isinstance(candidate, dict):
            for key in ("result", "b64_json", "image_base64"):
                nested = candidate.get(key)
                if isinstance(nested, str) and nested.strip():
                    return _compact_b64(nested)
    return ""


def _iter_sse_objects(response: Any) -> Iterator[dict[str, Any]]:
    for raw_line in response:
        if not raw_line:
            continue
        if isinstance(raw_line, bytes):
            line = raw_line.decode("utf-8", errors="replace").strip()
        else:
            line = str(raw_line).strip()
        if not line or not line.startswith("data: "):
            continue
        payload_str = line[len("data: ") :].strip()
        if not payload_str or payload_str == "[DONE]":
            continue
        try:
            payload = json.loads(payload_str)
        except json.JSONDecodeError:
            logger.warning("[image_generation] invalid SSE JSON: %.200s", payload_str)
            continue
        if isinstance(payload, dict):
            yield payload


class ImageGenerationTool(BaseTool):
    """使用 new-api gpt-image 生成图片，并返回 reply 可展开的短 token。"""

    @property
    def tool_name(self) -> str:
        return "image_generation"

    @property
    def description(self) -> str:
        return (
            "生成图片并返回可放进 reply(content) 的短 token。"
            "仅当用户明确要求画图、生成图片、做贴纸/头像/插画等新图片时使用。"
            "识别或解释已有图片时使用 image_summary，不要用本工具。"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "图片生成提示词。保留用户要求的主体、风格、构图、文字和约束。",
                    "minLength": 1,
                },
                "size": {
                    "type": "string",
                    "enum": sorted(_ALLOWED_SIZES),
                    "default": "1024x1024",
                    "description": "输出尺寸，默认 1024x1024。",
                },
                "quality": {
                    "type": "string",
                    "enum": sorted(_ALLOWED_QUALITIES),
                    "default": "high",
                    "description": "图片质量，默认 high。",
                },
                "background": {
                    "type": "string",
                    "enum": sorted(_ALLOWED_BACKGROUNDS),
                    "default": "auto",
                    "description": "背景策略，默认 auto。",
                },
            },
            "required": ["prompt"],
        }

    def _build_payload(
        self,
        *,
        prompt: str,
        size: str,
        quality: str,
        background: str,
    ) -> dict[str, Any]:
        return {
            "model": IMAGE_GENERATION_MODEL,
            "instructions": (
                "You are an image generation assistant. "
                "When the user asks for an image, call the image_generation tool."
            ),
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": prompt,
                        }
                    ],
                }
            ],
            "tools": [
                {
                    "type": "image_generation",
                    "output_format": "png",
                    "size": size,
                    "quality": quality,
                    "background": background,
                    "action": "generate",
                }
            ],
            "tool_choice": "auto",
            "store": False,
            "stream": True,
        }

    def _record_trace_start(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> int:
        try:
            from core.llm_trace_context import get_llm_trace_vars
            from core.tracing import LLMRequestTracer

            trace_id, run_id, trace_source = get_llm_trace_vars()
            source = (
                trace_source
                if str(trace_source or "").startswith("image_generation.")
                else "image_generation.tool"
            )
            return LLMRequestTracer.record_request(
                trace_id=trace_id,
                run_id=run_id,
                source=source,
                provider="new-api",
                model=str(IMAGE_GENERATION_MODEL or ""),
                url=url,
                method="POST",
                headers=headers,
                request=payload,
                status="created",
            )
        except Exception:
            return 0

    def _finish_trace(
        self,
        *,
        log_id: int,
        started: float,
        response_status: int,
        status: str,
        response: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        try:
            import time as _time
            from core.tracing import LLMRequestTracer

            LLMRequestTracer.finish_request(
                log_id=log_id,
                response=response or {},
                response_status=response_status,
                status=status,
                error=error,
                latency_ms=int((_time.time() - started) * 1000),
            )
        except Exception:
            pass

    def _call_new_api(
        self,
        *,
        prompt: str,
        size: str,
        quality: str,
        background: str,
    ) -> dict[str, Any]:
        if not str(NEW_API_KEY or "").strip():
            raise ValueError("NEW_API_KEY is not configured")

        payload = self._build_payload(
            prompt=prompt,
            size=size,
            quality=quality,
            background=background,
        )
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        base_url = str(NEW_API_BASE_URL or "").rstrip("/")
        url = f"{base_url}/responses"
        headers = {
            "Authorization": f"Bearer {NEW_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        import time as _time

        started = _time.time()
        log_id = self._record_trace_start(url, headers, payload)
        response_status = 0
        text_chunks: list[str] = []
        image_item: dict[str, Any] = {}
        image_b64 = ""

        logger.info("  [image_generation] >> %s | model=%s", url, IMAGE_GENERATION_MODEL)
        proxy_handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(proxy_handler)
        try:
            with opener.open(req, timeout=IMAGE_GENERATION_TIMEOUT) as response:
                response_status = getattr(response, "status", None) or (
                    response.getcode() if hasattr(response, "getcode") else 200
                )
                for obj in _iter_sse_objects(response):
                    event_type = obj.get("type")
                    if event_type == "response.output_text.delta":
                        text_chunks.append(str(obj.get("delta") or ""))
                        continue
                    if event_type != "response.output_item.done":
                        continue
                    item = obj.get("item") or {}
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") != "image_generation_call":
                        continue
                    image_item = item
                    image_b64 = _extract_image_b64(item)
                    if image_b64:
                        break

            if not image_b64:
                raise ValueError("missing image_generation_call image result")
            image_bytes = base64.b64decode(image_b64, validate=True)
            result = {
                "image_b64": image_b64,
                "image_bytes": len(image_bytes),
                "text_output": "".join(text_chunks),
                "revised_prompt": str(image_item.get("revised_prompt") or ""),
            }
            self._finish_trace(
                log_id=log_id,
                started=started,
                response_status=int(response_status or 200),
                status="success",
                response={
                    "image_bytes": result["image_bytes"],
                    "text_output": result["text_output"][:300],
                    "has_revised_prompt": bool(result["revised_prompt"]),
                },
            )
            return result
        except Exception as exc:
            status = getattr(exc, "code", None) or response_status or 0
            self._finish_trace(
                log_id=log_id,
                started=started,
                response_status=int(status or 0),
                status="error",
                error=str(exc),
            )
            raise

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return ToolResult(error="Missing 'prompt' argument")

        size = _normalize_option(args.get("size"), _ALLOWED_SIZES, "1024x1024")
        quality = _normalize_option(args.get("quality"), _ALLOWED_QUALITIES, "high")
        background = _normalize_option(args.get("background"), _ALLOWED_BACKGROUNDS, "auto")
        try:
            result = await asyncio.to_thread(
                self._call_new_api,
                prompt=prompt,
                size=size,
                quality=quality,
                background=background,
            )
            image_b64 = result["image_b64"]
            saved = save_generated_image(
                image_b64,
                prompt=prompt,
                metadata={
                    "model": IMAGE_GENERATION_MODEL,
                    "size": size,
                    "quality": quality,
                    "background": background,
                },
            )
            payload = {
                "mime": "image/png",
                "model": IMAGE_GENERATION_MODEL,
                "size": size,
                "quality": quality,
                "background": background,
                "reply_token": saved["reply_token"],
                "saved_path": saved["path"],
                "image_bytes": saved["bytes"],
                "revised_prompt": result.get("revised_prompt", ""),
                "text_output": result.get("text_output", ""),
                "usage_hint": "把 reply_token 原样放进 reply(content) 即可发送图片，不要改写 token。",
            }
            return ToolResult(output=json.dumps(payload, ensure_ascii=False), exit_code=0)
        except urllib.error.URLError as exc:
            logger.error("[image_generation] request failed: %s", exc, exc_info=True)
            return ToolResult(error=f"Image generation failed: {str(exc)}")
        except Exception as exc:
            logger.error("[image_generation] failed: %s", exc, exc_info=True)
            return ToolResult(error=f"Image generation failed: {str(exc)}")
