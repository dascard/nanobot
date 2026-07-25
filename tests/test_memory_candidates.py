"""旧 ``memory_candidates`` Writer 的兼容墓碑测试。"""

from __future__ import annotations

import pytest


def test_legacy_memory_candidate_writer_always_rejects():
    from app.group_analysis.memory_candidates import (
        LegacyMemoryCandidateWriterRetired,
        extract_and_persist,
    )

    with pytest.raises(
        LegacyMemoryCandidateWriterRetired,
        match="GroupAnalysisApplicationService",
    ):
        extract_and_persist(
            "group_42",
            {
                "topics": {
                    "_generator": "llm",
                    "topics": [{
                        "topic": "不得由旧入口写入",
                        "evidence_log_ids": [1, 2],
                    }],
                },
            },
            source_meta={"source_log_ids": [1, 2]},
        )


def test_legacy_memory_candidate_writer_records_only_compatibility_usage():
    from app.group_analysis.memory_candidates import (
        LegacyMemoryCandidateWriterRetired,
        extract_and_persist,
    )
    from core.lifecycle import get_compatibility_usage_snapshot

    before = get_compatibility_usage_snapshot()
    old_count = (
        before.get(
            "schema.legacy_group_analysis_memory_candidate_write"
        ).count
        if before.get(
            "schema.legacy_group_analysis_memory_candidate_write"
        )
        else 0
    )

    with pytest.raises(LegacyMemoryCandidateWriterRetired):
        extract_and_persist("group_42", {})

    after = get_compatibility_usage_snapshot()
    assert (
        after[
            "schema.legacy_group_analysis_memory_candidate_write"
        ].count
        == old_count + 1
    )
