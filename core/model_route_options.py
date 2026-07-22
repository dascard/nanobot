"""兼容入口；实现已下沉到无业务依赖的 LLM foundation。"""

from foundation.llm.model_options import (
    ENABLE_THINKING_AUTO,
    ENABLE_THINKING_FALSE,
    ENABLE_THINKING_TRUE,
    apply_enable_thinking_to_payload,
    model_defaults_to_disabled_thinking,
    normalize_enable_thinking,
)

__all__ = [
    "ENABLE_THINKING_AUTO",
    "ENABLE_THINKING_FALSE",
    "ENABLE_THINKING_TRUE",
    "apply_enable_thinking_to_payload",
    "model_defaults_to_disabled_thinking",
    "normalize_enable_thinking",
]
