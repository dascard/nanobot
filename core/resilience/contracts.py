"""与具体任务、传输和存储实现无关的失败分类。"""

from __future__ import annotations

from enum import StrEnum


class FailureCategory(StrEnum):
    """控制重试与降级的有限分类；不得由异常正文推断。"""

    VALIDATION = "validation"
    AUTHORIZATION = "authorization"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    TRANSIENT_TRANSPORT = "transient_transport"
    CONTRACT_VIOLATION = "contract_violation"
    CONFLICT = "conflict"
    QUOTA = "quota"
    CANCELLED = "cancelled"
    PERMANENT = "permanent"


__all__ = ["FailureCategory"]
