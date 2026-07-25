"""群体记忆提取服务。

Web/Admin 入口复用 group_analysis 的仓储、预处理和分析管线，不在路由层重建业务流程。
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field

from core.db import (
    group_learning_query_repository,
    group_memory_repository,
    release_clean_session_transaction,
)
from core.settings_service import settings

GROUP_MEMORY_MAX_LOGS = int(os.environ.get("GROUP_ANALYSIS_MAX_LOGS", "5000"))
GROUP_MEMORY_PROMPT_CHAR_BUDGET = int(os.environ.get("GROUP_ANALYSIS_PROMPT_CHAR_BUDGET", "60000"))
GROUP_MEMORY_STYLE_PROMPT_CHAR_BUDGET = int(os.environ.get("GROUP_ANALYSIS_STYLE_PROMPT_CHAR_BUDGET", "24000"))


class GroupMemoryExtractionError(RuntimeError):
    """群体记忆提取失败。"""


class GroupMemoryGroupNotFound(GroupMemoryExtractionError):
    """找不到目标群。"""


class GroupMemoryInsufficientData(GroupMemoryExtractionError):
    """可分析群聊语料不足。"""


class GroupMemoryLearningDisabled(GroupMemoryExtractionError):
    """全局群学习 kill switch 未开启。"""


class GroupMemoryLearningFailed(GroupMemoryExtractionError):
    """共享群学习 Pipeline 未能完成手动提取。"""


@dataclass
class GroupMemoryExtractionResult:
    ok: bool
    group_id: str
    group_name: str
    window_hours: int | None
    raw_count: int
    eligible_count: int
    deduped_count: int
    message_count: int
    source_log_count: int
    stats: dict[str, int] = field(default_factory=dict)
    memory_count: int = 0
    active_count: int = 0
    injectable_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


async def extract_group_memories(
    db,
    group_id: str,
    *,
    window_hours: int | None = 24,
    instructions: str = "",
    aspects: object = None,
) -> GroupMemoryExtractionResult:
    """对指定群运行一次群体记忆提取。"""
    from app.group_analysis import analyzer
    from app.group_analysis.application_service import (
        GroupAnalysisApplicationService,
        build_group_analysis_learning_request,
    )
    from app.group_analysis.preprocess import (
        build_analysis_payload,
        dedupe_group_logs,
        filter_analyzable_logs,
        resolve_analysis_window_hours,
    )
    from app.group_analysis.repository import GroupAnalysisRepository
    from app.group_learning.pipeline_service import (
        build_group_learning_processor,
    )
    from core.chat_stream_identity import resolve_chat_stream_identity
    from core.group_learning import validate_aspect_selection

    repo = GroupAnalysisRepository(db)
    learning_query = group_learning_query_repository(db)
    group = repo.resolve_group(group_id)
    if not group:
        raise GroupMemoryGroupNotFound(f"未找到群: {group_id}")
    if not settings.get_bool("group_learning.enabled", False):
        raise GroupMemoryLearningDisabled("群学习功能当前未启用")
    selected_aspects = validate_aspect_selection(
        aspects,
        use_schedule_default=True,
    )

    resolved_window_hours = resolve_analysis_window_hours(window_hours, instructions)
    batch = repo.fetch_group_logs(
        group,
        window_hours=resolved_window_hours,
        limit=GROUP_MEMORY_MAX_LOGS,
    )
    eligible_logs = filter_analyzable_logs(batch.logs)
    logs = dedupe_group_logs(eligible_logs)
    if len(logs) < 3:
        raise GroupMemoryInsufficientData(f"群 {group.name} 可分析消息不足，至少需要 3 条")

    payload = build_analysis_payload(
        logs,
        prompt_budget=GROUP_MEMORY_PROMPT_CHAR_BUDGET,
        style_budget=GROUP_MEMORY_STYLE_PROMPT_CHAR_BUDGET,
    )
    if len(payload["messages"]) < 3:
        raise GroupMemoryInsufficientData(f"群 {group.name} 清洗后消息不足，至少需要 3 条")
    payload["group_stats"]["analysis_window"] = (
        "全部历史" if resolved_window_hours is None else f"最近{resolved_window_hours}小时"
    )
    identity = resolve_chat_stream_identity(
        platform="qq",
        chat_type="group",
        session_id=group.group_id,
    )
    learning_request = build_group_analysis_learning_request(
        chat_stream_id=identity.chat_stream_id,
        aspects=selected_aspects,
        payload=payload,
        trigger="manual",
        cursor_start_chat_log_id=0,
        cursor_end_chat_log_id=int(batch.latest_log_id or 0),
    )
    if learning_request is None:
        raise GroupMemoryInsufficientData(
            f"群 {group.name} 没有可作为正式证据的消息"
        )
    candidate_count_before = learning_query.count_candidates(
        chat_stream_id=identity.chat_stream_id,
    )

    if not release_clean_session_transaction(
        db,
        label="group_memory_extract_before_llm",
    ):
        in_transaction = getattr(db, "in_transaction", None)
        if callable(in_transaction) and in_transaction():
            raise RuntimeError("群体记忆提取在模型调用前仍持有数据库事务")
    application_result = await GroupAnalysisApplicationService(
        learning_pipeline=build_group_learning_processor(db),
        analyzer=analyzer.analyze_group,
    ).process_payload(
        aspects=selected_aspects,
        payload=payload,
        group_name=group.name,
        instructions=instructions,
        learning_request=learning_request,
    )
    outcome = application_result.learning_outcome
    if outcome is None or outcome.status != "succeeded":
        error_code = (
            outcome.error_code
            if outcome is not None
            else "group_learning_outcome_missing"
        )
        raise GroupMemoryLearningFailed(
            f"群学习手动提取失败：{error_code}"
        )

    try:
        db.expire_all()
    except Exception:
        pass
    candidate_count_after = learning_query.count_candidates(
        chat_stream_id=identity.chat_stream_id,
    )
    run = learning_query.get_run(outcome.run_id)
    stats = {
        "new": max(
            0,
            candidate_count_after - candidate_count_before,
        ),
        "updated": 0,
        "skipped": (
            int(run.rejected_count or 0)
            + int(run.conflict_count or 0)
            + int(run.waiting_count or 0)
            if run is not None
            else 0
        ),
    }
    counts = group_memory_counts(db, group.group_id)
    return GroupMemoryExtractionResult(
        ok=True,
        group_id=group.group_id,
        group_name=group.name,
        window_hours=resolved_window_hours,
        raw_count=batch.raw_count,
        eligible_count=len(eligible_logs),
        deduped_count=len(logs),
        message_count=len(payload["messages"]),
        source_log_count=len(payload.get("source_log_ids", [])),
        stats=stats,
        memory_count=counts["memory_count"],
        active_count=counts["active_count"],
        injectable_count=counts["injectable_count"],
    )


def group_memory_counts(db, group_id: str) -> dict[str, int]:
    """统计某群记忆数量。"""
    from app.group_memory.query_service import GroupMemoryQueryService

    return GroupMemoryQueryService(
        group_memory_repository(db)
    ).counts(
        group_id,
        platform="qq",
    ).to_dict()


def build_group_memory_overview(db, *, limit: int = 300) -> list[dict]:
    """返回所有有群聊日志或群体记忆的群概览。"""
    from app.group_memory.query_service import GroupMemoryQueryService

    items = GroupMemoryQueryService(
        group_memory_repository(db)
    ).build_overview(limit=limit)
    return [item.to_dict() for item in items]
