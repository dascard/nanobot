"""旧群分析候选 Writer 的拒绝型兼容墓碑。

正式写入统一由 ``GroupAnalysisApplicationService`` 和群学习 Pipeline
负责；保留本模块仅用于观测尚未迁移的内部调用。
"""

from __future__ import annotations


class LegacyMemoryCandidateWriterRetired(RuntimeError):
    """旧 ``extract_and_persist`` 写入口已永久退役。"""


def extract_and_persist(
    group_id: str,
    analysis: dict,
    *,
    source_meta: dict | None = None,
) -> dict[str, int]:
    """记录兼容命中后拒绝，不能恢复第二套候选写入链。"""

    from core.lifecycle import record_compatibility_usage

    record_compatibility_usage(
        "schema.legacy_group_analysis_memory_candidate_write"
    )
    raise LegacyMemoryCandidateWriterRetired(
        "旧群分析候选 Writer 已退役，请使用 GroupAnalysisApplicationService"
    )


__all__ = [
    "LegacyMemoryCandidateWriterRetired",
    "extract_and_persist",
]
