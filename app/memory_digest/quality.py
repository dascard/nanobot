"""MemoryDigest v2 质量元数据工具。"""

from __future__ import annotations


def build_quality(
    *,
    score: float,
    issues: list[str] | None = None,
    should_inject_preview: bool = False,
) -> dict:
    """构造摘要质量字段，避免失败摘要被当作可注入内容。"""
    normalized_score = max(0.0, min(1.0, float(score)))
    return {
        "score": normalized_score,
        "issues": list(issues or []),
        "should_inject_preview": bool(
            should_inject_preview
            and normalized_score >= 0.7
            and not list(issues or [])
        ),
    }
