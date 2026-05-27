"""LLM 流式响应追踪辅助。"""

from __future__ import annotations

import json
import time
from typing import Any


CONTENT_KEYS = ("content",)
REASONING_KEYS = (
    "reasoning_content",
    "reasoning",
    "reasoning_text",
    "thinking",
    "thinking_content",
)


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _extract_choice_text(payload: Any, keys: tuple[str, ...]) -> str:
    if not isinstance(payload, dict):
        return ""
    parts: list[str] = []
    for choice in payload.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        for container in (choice.get("delta"), choice.get("message"), choice):
            if not isinstance(container, dict):
                continue
            for key in keys:
                text = _coerce_text(container.get(key))
                if text:
                    parts.append(text)
    for key in keys:
        text = _coerce_text(payload.get(key))
        if text:
            parts.append(text)
    return "".join(parts)


class LLMStreamTraceAccumulator:
    """聚合流式 chunk，补齐正文、推理文本和耗时指标。"""

    def __init__(self, *, started: float | None = None, max_chunks: int = 20) -> None:
        self.started = time.time() if started is None else started
        self.max_chunks = max(1, int(max_chunks or 20))
        self.content_parts: list[str] = []
        self.reasoning_parts: list[str] = []
        self.chunks_sample: list[Any] = []
        self.usage: Any = None
        self.chunk_count = 0
        self.first_chunk_ms: int | None = None
        self.first_reasoning_ms: int | None = None
        self.last_reasoning_ms: int | None = None
        self.first_content_ms: int | None = None
        self.last_content_ms: int | None = None

    def _elapsed_ms(self, now: float | None = None) -> int:
        current = time.time() if now is None else now
        return max(0, int((current - self.started) * 1000))

    def record_chunk(self, payload: Any, *, now: float | None = None) -> None:
        elapsed_ms = self._elapsed_ms(now)
        if self.first_chunk_ms is None:
            self.first_chunk_ms = elapsed_ms

        self.chunk_count += 1
        if len(self.chunks_sample) >= self.max_chunks:
            self.chunks_sample.pop(0)
        self.chunks_sample.append(payload)

        if isinstance(payload, dict) and payload.get("usage"):
            self.usage = payload.get("usage")

        reasoning = _extract_choice_text(payload, REASONING_KEYS)
        if reasoning:
            if self.first_reasoning_ms is None:
                self.first_reasoning_ms = elapsed_ms
            self.last_reasoning_ms = elapsed_ms
            self.reasoning_parts.append(reasoning)

        content = _extract_choice_text(payload, CONTENT_KEYS)
        if content:
            if self.first_content_ms is None:
                self.first_content_ms = elapsed_ms
            self.last_content_ms = elapsed_ms
            self.content_parts.append(content)

    def build_response(self, *, now: float | None = None) -> dict[str, Any]:
        total_latency_ms = self._elapsed_ms(now)
        reasoning_content = "".join(self.reasoning_parts)
        content = "".join(self.content_parts)
        if self.first_reasoning_ms is not None:
            reasoning_end_ms = (
                self.first_content_ms
                if self.first_content_ms is not None
                else self.last_reasoning_ms
            )
            reasoning_elapsed_ms = max(0, int((reasoning_end_ms or self.first_reasoning_ms) - self.first_reasoning_ms))
        else:
            reasoning_elapsed_ms = 0

        metrics = {
            "total_latency_ms": total_latency_ms,
            "chunk_count": self.chunk_count,
            "first_chunk_ms": self.first_chunk_ms,
            "first_reasoning_ms": self.first_reasoning_ms,
            "last_reasoning_ms": self.last_reasoning_ms,
            "first_content_ms": self.first_content_ms,
            "last_content_ms": self.last_content_ms,
            "reasoning_elapsed_ms": reasoning_elapsed_ms,
            "reasoning_char_count": len(reasoning_content),
            "content_char_count": len(content),
        }
        result: dict[str, Any] = {
            "content": content,
            "chunks_sample": list(self.chunks_sample),
            "stream_metrics": metrics,
        }
        if reasoning_content:
            result["reasoning_content"] = reasoning_content
        if self.usage is not None:
            result["usage"] = self.usage
        return result
