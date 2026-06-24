"""Image generation tool — 通过 new-api Responses 接口生成可发送图片。"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import urllib.error
import urllib.request
from typing import Any
from collections.abc import Iterator

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

from config import (
    IMAGE_GENERATION_MODEL,
    IMAGE_GENERATION_PROMPT_MAX_CHARS,
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


# ── 增强 SSE 解析 helpers ──

def _extract_response_completed_image_b64(obj: dict[str, Any]) -> str:
    """从 response.completed 的 response.output 中提取图片。"""
    response = obj.get("response")
    if not isinstance(response, dict):
        return ""
    output = response.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if isinstance(item, dict) and item.get("type") == "image_generation_call":
            b64 = _extract_image_b64(item)
            if b64:
                return b64
    return ""


def _extract_any_stream_image_b64(obj: dict[str, Any]) -> str:
    """从任意 SSE 事件中提取图片 base64（覆盖多种上游形态）。"""
    event_type = str(obj.get("type") or "")

    # 流式 partial image
    if event_type == "response.image_generation_call.partial_image":
        b64 = obj.get("partial_image_b64") or obj.get("b64_json") or obj.get("result")
        if isinstance(b64, str) and b64.strip():
            return _compact_b64(b64)

    # 标准 output_item.done
    item = obj.get("item")
    if isinstance(item, dict) and item.get("type") == "image_generation_call":
        b64 = _extract_image_b64(item)
        if b64:
            return b64

    # 某些网关可能直接把 image_generation_call 作为事件本体
    if event_type == "image_generation_call":
        b64 = _extract_image_b64(obj)
        if b64:
            return b64

    # completed 聚合
    if event_type == "response.completed":
        return _extract_response_completed_image_b64(obj)

    return ""


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


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
                    "maxLength": IMAGE_GENERATION_PROMPT_MAX_CHARS,
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

        # 增强 SSE 解析状态
        last_image_b64 = ""
        last_completed: dict[str, Any] = {}
        last_error_event: dict[str, Any] = {}
        image_event_seen = False
        debug_events: list[dict[str, Any]] = []

        logger.info("  [image_generation] >> %s | model=%s", url, IMAGE_GENERATION_MODEL)
        proxy_handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(proxy_handler)
        try:
            with opener.open(req, timeout=IMAGE_GENERATION_TIMEOUT) as response:
                response_status = getattr(response, "status", None) or (
                    response.getcode() if hasattr(response, "getcode") else 200
                )
                for obj in _iter_sse_objects(response):
                    event_type = str(obj.get("type") or "")

                    # 只保留尾部事件，避免日志爆炸
                    if len(debug_events) < 80:
                        debug_events.append(obj)
                    else:
                        debug_events.pop(0)
                        debug_events.append(obj)

                    logger.debug(
                        "[image_generation] SSE type=%s keys=%s",
                        event_type,
                        list(obj.keys()),
                    )

                    if event_type == "response.output_text.delta":
                        text_chunks.append(str(obj.get("delta") or ""))
                        continue

                    if event_type in {"response.failed", "response.incomplete", "response.error"}:
                        last_error_event = obj
                        continue

                    if event_type == "response.completed":
                        response_obj = obj.get("response")
                        if isinstance(response_obj, dict):
                            last_completed = response_obj

                    b64 = _extract_any_stream_image_b64(obj)
                    if b64:
                        image_event_seen = True
                        last_image_b64 = b64

                        item = obj.get("item")
                        if isinstance(item, dict) and item.get("type") == "image_generation_call":
                            image_item = item
                            image_b64 = b64
                            break

                        if event_type == "response.completed":
                            image_b64 = b64
                            break

                        # partial image 先暂存，继续等最终图
                        continue

                    if event_type == "response.completed":
                        if last_image_b64:
                            image_b64 = last_image_b64
                        break

            # 没有最终图，回退到最后一个 partial image
            if not image_b64 and last_image_b64 and image_event_seen:
                image_b64 = last_image_b64
                logger.warning("[image_generation] using last partial image as fallback")

            if not image_b64:
                raise self._build_image_failure_error(
                    last_error_event=last_error_event,
                    last_completed=last_completed,
                    image_event_seen=image_event_seen,
                    debug_events=debug_events,
                )

            # 校验 PNG 魔数
            raw = base64.b64decode(image_b64, validate=True)
            if not raw.startswith(_PNG_MAGIC):
                raise ValueError(
                    f"image result is not a PNG (first 8 bytes: {raw[:8]!r})"
                )

            result = {
                "image_b64": image_b64,
                "image_bytes": len(raw),
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
                error=str(exc)[:500],
            )
            raise

    def _build_image_failure_error(
        self,
        *,
        last_error_event: dict[str, Any],
        last_completed: dict[str, Any],
        image_event_seen: bool,
        debug_events: list[dict[str, Any]],
    ) -> ValueError:
        """根据上游事件构造分型错误信息。"""

        # 优先：上游明确报错
        if last_error_event:
            return ValueError(
                "image generation upstream error: "
                + json.dumps(last_error_event, ensure_ascii=False)[:1000]
            )

        status = str(last_completed.get("status") or "")
        error = last_completed.get("error")
        incomplete = last_completed.get("incomplete_details")
        moderation = last_completed.get("moderation")
        output = last_completed.get("output")
        tool_usage = last_completed.get("tool_usage")

        # content_filter / safety 等
        if error or incomplete or moderation or status in {"failed", "incomplete"}:
            return ValueError(
                "image generation blocked or incomplete: "
                + json.dumps(
                    {
                        "status": status,
                        "error": error,
                        "incomplete_details": incomplete,
                        "moderation": moderation,
                    },
                    ensure_ascii=False,
                )[:1000]
            )

        # new-api 聚合失败：tool_usage 有统计但 output 为空
        if tool_usage and not output:
            return ValueError(
                "image generation produced tool_usage but no image result; "
                "possible upstream aggregation/moderation issue"
            )

        # SSE 有图片事件但最终未拿到
        if image_event_seen:
            return ValueError(
                "image generation image event was seen but final image result was unavailable"
            )

        # 完全无生图事件
        debug_summary = json.dumps(
            [{"type": e.get("type"), "keys": list(e.keys())[:5]} for e in debug_events[-20:]],
            ensure_ascii=False,
        )[:800]
        return ValueError(
            f"missing image_generation_call image result; last {min(len(debug_events), 20)} events: {debug_summary}"
        )

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return ToolResult(error="prompt is required")
        if len(prompt) > IMAGE_GENERATION_PROMPT_MAX_CHARS:
            return ToolResult(
                error=f"prompt too long: {len(prompt)} > {IMAGE_GENERATION_PROMPT_MAX_CHARS}"
            )

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
