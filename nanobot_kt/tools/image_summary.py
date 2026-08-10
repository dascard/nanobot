"""图片摘要工具的 KT Provider 与多模态薄 Adapter。"""

from __future__ import annotations

import asyncio as _asyncio
import json
import logging
import urllib.request
from typing import Any

from nanobot_kt.optional_tool_api import BaseTool, ExecutionMode, ToolResult
from nanobot_kt.optional_message_api import (
    content_parts_to_dicts,
    make_multimodal_content,
)

from app.tool_services.image_summary import (
    execute_image_summary,
    extract_completion_content as _extract_completion_content,
    normalize_image_files as _normalize_files,
    parse_image_summary_payload as _parse_json_payload,
)
from config import (
    IMAGE_SUMMARY_API_URL,
    IMAGE_SUMMARY_MAX_TOKENS,
    IMAGE_SUMMARY_TEMPERATURE,
    IMAGE_SUMMARY_TIMEOUT,
    IMAGE_SUMMARY_TOP_P,
)
from nanobot_kt.image_pipeline import prepare_image_parts
from nanobot_kt.tools.result_adapter import to_kt_tool_result


def _get_image_summary_route() -> dict:
    """解析表情包打标完整路由配置。结果缓存 30s 避免频繁 DB 查询。"""
    import time as _time
    from clients.classifier_client import resolve_model_route
    from core.database import SessionLocal, SystemSetting

    # 缓存：避免每次图片描述都查 DB
    cache = getattr(_get_image_summary_route, "_cache", None)
    now = _time.time()
    if cache and now - cache["ts"] < 30:
        return cache["route"]

    route = resolve_model_route("sticker_describe")

    saved_keys: set[str] = set()
    try:
        db = SessionLocal()
        try:
            saved_keys = {
                row.key for row in db.query(SystemSetting.key).filter(
                    SystemSetting.key.like("model.route.sticker_describe%")
                ).all()
            }
        finally:
            db.close()
    except Exception as e:
        logger.warning("[image_summary] route setting lookup failed, using defaults: %s", e)

    prefix = "model.route.sticker_describe"
    has_provider = f"{prefix}.provider" in saved_keys
    has_base_url = prefix in saved_keys or f"{prefix}.base_url" in saved_keys
    if not has_provider and not has_base_url:
        route["base_url"] = str(IMAGE_SUMMARY_API_URL or route["base_url"])
    if f"{prefix}.max_tokens" not in saved_keys:
        route["max_tokens"] = IMAGE_SUMMARY_MAX_TOKENS
    if f"{prefix}.temperature" not in saved_keys:
        route["temperature"] = IMAGE_SUMMARY_TEMPERATURE
    if f"{prefix}.timeout" not in saved_keys:
        route["timeout"] = IMAGE_SUMMARY_TIMEOUT

    _get_image_summary_route._cache = {"ts": now, "route": route}
    return route

logger = logging.getLogger("nanobot.tool.image_summary")
# 旧测试会在工具模块上 patch asyncio.to_thread；保留同一标准库模块对象。
asyncio = _asyncio

def _build_multimodal_content(files: list[str], focus: str) -> list[Any] | str:
    prompt_lines = [
        f"请为以下 {len(files)} 张图片生成结构化摘要。",
        "要求：",
        "- 输出严格 JSON，不要 Markdown，不要代码块，不要解释文字",
        "- 保留图片中的文字、物体、场景、布局和可疑信息",
        "- 看不清就写入 uncertainties，不要猜测",
        "- 如果图片里有文字，请尽量做 OCR",
    ]
    if focus:
        prompt_lines.append(f"- 额外关注点：{focus}")
    prompt_text = "\n".join(prompt_lines)
    try:
        from core.prompt_v2.tool_templates import render_tool_execution_template

        prompt_text = render_tool_execution_template(
            "tools/image_summary/user",
            {"image_count": str(len(files)), "focus": focus},
            fallback=prompt_text,
            expected_tool_name="image_summary",
        )
    except Exception:
        pass

    image_parts = prepare_image_parts(
        files,
        source_type="image_summary",
        source_name_prefix="image_summary",
        detail="low",
    )
    content = make_multimodal_content(prompt_text, images=image_parts)
    if isinstance(content, list):
        return content_parts_to_dicts(content)
    return content


class ImageSummaryTool(BaseTool):
    """使用本地 Qwen 视觉模型对图片做结构化摘要。"""

    @property
    def tool_name(self) -> str:
        return "image_summary"

    @property
    def description(self) -> str:
        return (
            "生成图片摘要并输出结构化 JSON。"
            "当你需要 OCR、细节归档、版面分析或多图整理时使用。"
            "files 必须是 http(s) URL 或 data:image/... base64。"
            "不支持 QQ/OneBot file_id、CQ码、裸文件名、相对路径。"
            "如果只有 file_id 而没有 URL，不要调用此工具。"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "图片 URL 列表。必须是 http(s) URL 或 data:image/... base64。"
                        "不支持 QQ/OneBot file_id、CQ码、裸文件名、相对路径。"
                    ),
                    "minItems": 1,
                },
                "focus": {
                    "type": "string",
                    "description": "可选，摘要重点，如 OCR、人物、场景、风险、表格",
                },
            },
            "required": ["files"],
        }

    @staticmethod
    def _system_prompt() -> str:
        fallback = (
            "你是本地 Qwen 视觉摘要模型。"
            "请根据输入图片输出严格 JSON，禁止 Markdown、禁止代码块、禁止额外解释。"
            "如果图片不清晰，请在 uncertainties 中说明，不要猜测。"
            "输出结构必须包含："
            "{"
            "\"image_count\": int, "
            "\"overall_summary\": str, "
            "\"per_image\": [{\"index\": int, \"summary\": str, \"text\": [str], \"objects\": [str], \"scene\": str, \"uncertainties\": [str]}], "
            "\"keywords\": [str], "
            "\"risk_flags\": [str], "
            "\"confidence\": \"high|medium|low\""
            "}"
        )
        try:
            from core.prompt_v2.tool_templates import render_tool_execution_template

            return render_tool_execution_template(
                "tools/image_summary/system",
                {},
                fallback=fallback,
                expected_tool_name="image_summary",
            )
        except Exception:
            return fallback

    def _build_payload(self, files: list[str], focus: str, route: dict) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": _build_multimodal_content(files, focus)},
            ],
            "max_tokens": route.get("max_tokens", IMAGE_SUMMARY_MAX_TOKENS),
            "temperature": route.get("temperature", IMAGE_SUMMARY_TEMPERATURE),
            "top_p": IMAGE_SUMMARY_TOP_P,
        }
        if route.get("model"):
            payload["model"] = route["model"]
        from core.model_route_options import apply_enable_thinking_to_payload
        return apply_enable_thinking_to_payload(
            payload,
            str(route.get("model", "")),
            route.get("enable_thinking", "auto"),
        )

    def _call_qwen(self, files: list[str], focus: str) -> str:
        from clients.classifier_client import (
            call_model_route_response,
            ensure_model_route_enabled,
        )

        route = _get_image_summary_route()
        ensure_model_route_enabled("sticker_describe", route)
        payload = self._build_payload(files, focus, route)
        if route.get("binding_candidates"):
            logger.info(
                "  [image_summary] >> route=sticker_describe | files=%d",
                len(files),
            )
            response = call_model_route_response(
                route_key="sticker_describe",
                messages=payload["messages"],
                max_tokens=int(payload["max_tokens"]),
                temperature=float(payload["temperature"]),
                timeout=float(route.get("timeout", IMAGE_SUMMARY_TIMEOUT)),
            )
            content = str(response.content or "")
            logger.info("  [image_summary] << raw: %.120s", content)
            return content

        # 尚未迁移到 Route Binding 的旧配置保留直接调用兼容路径。正式绑定
        # 一旦存在，上面的统一 helper 负责候选切换、熔断和 Trace。
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        base_url = str(route.get("base_url", "")).rstrip("/")
        url = f"{base_url}/chat/completions"

        headers = {"Content-Type": "application/json"}
        if route.get("api_key"):
            headers["Authorization"] = f"Bearer {route['api_key']}"

        logger.info("  [image_summary] >> %s | files=%d", url, len(files))
        import time as _time
        started = _time.time()
        log_id = 0
        try:
            from core.llm_trace_context import get_llm_trace_vars
            from core.tracing import LLMRequestTracer

            trace_id, run_id, trace_source = get_llm_trace_vars()
            source = (
                trace_source
                if str(trace_source or "").startswith("image_summary.")
                else "image_summary.tool"
            )
            log_id = LLMRequestTracer.record_request(
                trace_id=trace_id,
                run_id=run_id,
                source=source,
                provider=str(route.get("provider_id", "") or "local_vision"),
                model=str(route.get("model", "") or ""),
                url=url,
                method="POST",
                headers=headers,
                request=payload,
                status="created",
            )
        except Exception:
            pass
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        timeout = route.get("timeout", IMAGE_SUMMARY_TIMEOUT)
        proxy_handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(proxy_handler)
        try:
            with opener.open(req, timeout=timeout) as response:
                response_status = getattr(response, "status", None) or (
                    response.getcode() if hasattr(response, "getcode") else 200
                )
                body = json.loads(response.read().decode("utf-8"))
            try:
                from core.tracing import LLMRequestTracer
                LLMRequestTracer.finish_request(
                    log_id=log_id,
                    response=body,
                    response_status=response_status,
                    status="success",
                    latency_ms=int((_time.time() - started) * 1000),
                )
            except Exception:
                pass
        except Exception as e:
            try:
                from core.tracing import LLMRequestTracer
                status = getattr(e, "code", 0) or 0
                LLMRequestTracer.finish_request(
                    log_id=log_id,
                    response={},
                    response_status=status,
                    status="error",
                    error=str(e),
                    latency_ms=int((_time.time() - started) * 1000),
                )
            except Exception:
                pass
            raise

        content = _extract_completion_content(body)
        logger.info("  [image_summary] << raw: %.120s", content)
        return content

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        del kwargs
        result = await execute_image_summary(
            args,
            summarize=self._call_qwen,
        )
        return to_kt_tool_result(result)


__all__ = [
    "ImageSummaryTool",
    "_build_multimodal_content",
    "_extract_completion_content",
    "_get_image_summary_route",
    "_normalize_files",
    "_parse_json_payload",
    "prepare_image_parts",
]
