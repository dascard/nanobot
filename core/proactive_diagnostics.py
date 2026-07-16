"""主动外呼正文生成使用的结构化安全诊断。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_GENERATION_ERROR_SUMMARIES = {
    "model_truncated": "主动外呼正文生成被截断",
    "model_finish_error": "主动外呼正文生成未正常结束",
    "empty_response": "主动外呼正文生成返回空正文",
    "contract_error": "主动外呼正文不符合生成契约",
    "generation_timeout": "主动外呼正文生成超时",
    "generation_error": "主动外呼正文生成失败",
}
_CONTRACT_ERROR_TYPES = frozenset({
    "model_truncated",
    "model_finish_error",
    "empty_response",
    "contract_error",
})
_JUDGEMENT_ERROR_SUMMARIES = {
    "model_error": "主动外呼 Judge 调用失败",
    "model_truncated": "主动外呼 Judge 返回被截断",
    "model_finish_error": "主动外呼 Judge 未正常结束",
    "empty_response": "主动外呼 Judge 返回空正文",
    "contract_error": "主动外呼 Judge 不符合判断契约",
}
_RESEARCH_BLOCK_SUMMARIES = {
    "empty_query": "主动研究查询为空",
    "sensitive_query": "主动研究查询不符合安全约束",
    "budget_guard_unavailable": "主动研究预算守卫不可用",
    "tool_guard_unavailable": "主动研究工具守卫不可用",
    "budget_exhausted": "主动研究调用预算已耗尽",
    "empty_draft": "主动研究返回空草稿",
    "insufficient_sources": "主动研究可核验来源不足",
    "unsafe_control_syntax": "主动研究草稿包含不安全控制语法",
    "unverified_url": "主动研究草稿包含未核验链接",
    "draft_budget_too_small": "主动研究正文预算不足",
    "runner_unavailable": "主动研究运行器不可用",
}
_RESEARCH_REASON_CODES = frozenset({
    *_RESEARCH_BLOCK_SUMMARIES,
    "timeout",
    "runtime_error",
    "contract_error",
})


@dataclass(frozen=True, slots=True)
class GenerationFailureDiagnostic:
    """可安全写入返回值与持久账本的生成失败诊断。"""

    error_type: str
    summary: str


def generation_failure_for_type(
    error_type: Any,
    *,
    default: str = "generation_error",
) -> GenerationFailureDiagnostic:
    """将受控错误类型映射为固定摘要，未知类型回退到 ``default``。"""

    normalized_default = str(default or "generation_error").strip()
    if normalized_default not in _GENERATION_ERROR_SUMMARIES:
        normalized_default = "generation_error"
    normalized = str(error_type or "").strip()
    if normalized not in _GENERATION_ERROR_SUMMARIES:
        normalized = normalized_default
    return GenerationFailureDiagnostic(
        error_type=normalized,
        summary=_GENERATION_ERROR_SUMMARIES[normalized],
    )


class OutreachModelContractError(RuntimeError):
    """模型成功返回 HTTP 响应，但不满足主动外呼正文生成契约。"""

    def __init__(self, message: str, *, error_type: str = "contract_error") -> None:
        super().__init__(message)
        normalized = str(error_type or "").strip()
        self.error_type = (
            normalized if normalized in _CONTRACT_ERROR_TYPES else "contract_error"
        )


def generation_failure_from_exception(
    exc: BaseException,
) -> GenerationFailureDiagnostic:
    """仅按异常类别生成诊断，不读取可能包含凭据的异常原文。"""

    if isinstance(exc, OutreachModelContractError):
        return generation_failure_for_type(exc.error_type, default="contract_error")
    if isinstance(exc, TimeoutError):
        return generation_failure_for_type("generation_timeout")
    return generation_failure_for_type("generation_error")


def judgement_failure_for_type(
    error_type: Any,
    *,
    default: str = "contract_error",
) -> GenerationFailureDiagnostic:
    """将 Judge 失败类型映射为固定摘要，未知类型按契约失败处理。"""

    normalized_default = str(default or "contract_error").strip()
    if normalized_default not in _JUDGEMENT_ERROR_SUMMARIES:
        normalized_default = "contract_error"
    normalized = str(error_type or "").strip()
    if normalized not in _JUDGEMENT_ERROR_SUMMARIES:
        normalized = normalized_default
    return GenerationFailureDiagnostic(
        error_type=normalized,
        summary=_JUDGEMENT_ERROR_SUMMARIES[normalized],
    )


def normalize_research_reason_code(reason_code: Any) -> str:
    """只保留 Research 契约声明的阻断原因码。"""

    normalized = str(reason_code or "").strip()
    if not normalized:
        return ""
    return normalized if normalized in _RESEARCH_REASON_CODES else "contract_error"


def research_failure_diagnostic(
    *,
    reason_code: Any,
    error: Any,
) -> GenerationFailureDiagnostic:
    """把研究阻断原因收敛到受控内部诊断。"""

    normalized_reason = normalize_research_reason_code(reason_code) or "contract_error"
    if bool(str(error or "")):
        return generation_failure_for_type("generation_error")
    if normalized_reason == "timeout":
        return generation_failure_for_type("generation_timeout")
    if normalized_reason == "runtime_error":
        return generation_failure_for_type("generation_error")
    if normalized_reason == "contract_error":
        return generation_failure_for_type("contract_error")
    return GenerationFailureDiagnostic(
        error_type=normalized_reason,
        summary=_RESEARCH_BLOCK_SUMMARIES[normalized_reason],
    )


def safe_research_error(*, reason_code: Any, error: Any) -> str:
    """替换 Research payload 中不可信的原始异常文本。"""

    if not str(error or ""):
        return ""
    diagnostic = research_failure_diagnostic(
        reason_code=reason_code,
        error=error,
    )
    return diagnostic.summary
