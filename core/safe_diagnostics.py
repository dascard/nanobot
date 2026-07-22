"""兼容入口；实现已下沉到无业务依赖的 LLM foundation。"""

from foundation.llm.safe_diagnostics import (
    DEFAULT_SAFE_SUMMARY_MAX_CHARS,
    safe_response_summary,
    safe_url_for_logging,
)

__all__ = [
    "DEFAULT_SAFE_SUMMARY_MAX_CHARS",
    "safe_response_summary",
    "safe_url_for_logging",
]
