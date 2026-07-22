"""兼容入口；实现已下沉到无业务依赖的 LLM foundation。"""

from foundation.llm.stream_trace import (
    CONTENT_KEYS,
    REASONING_KEYS,
    LLMStreamTraceAccumulator,
)

__all__ = ["CONTENT_KEYS", "REASONING_KEYS", "LLMStreamTraceAccumulator"]
