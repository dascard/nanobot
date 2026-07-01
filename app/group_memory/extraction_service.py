"""群体记忆提取服务。

Web/Admin 入口复用 group_analysis 的仓储、预处理和分析管线，不在路由层重建业务流程。
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, or_

GROUP_MEMORY_MAX_LOGS = int(os.environ.get("GROUP_ANALYSIS_MAX_LOGS", "5000"))
GROUP_MEMORY_PROMPT_CHAR_BUDGET = int(os.environ.get("GROUP_ANALYSIS_PROMPT_CHAR_BUDGET", "60000"))
GROUP_MEMORY_STYLE_PROMPT_CHAR_BUDGET = int(os.environ.get("GROUP_ANALYSIS_STYLE_PROMPT_CHAR_BUDGET", "24000"))


class GroupMemoryExtractionError(RuntimeError):
    """群体记忆提取失败。"""


class GroupMemoryGroupNotFound(GroupMemoryExtractionError):
    """找不到目标群。"""


class GroupMemoryInsufficientData(GroupMemoryExtractionError):
    """可分析群聊语料不足。"""


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
) -> GroupMemoryExtractionResult:
    """对指定群运行一次群体记忆提取。"""
    from creatures.nanobot.prompts.skills.group_analysis import analyzer
    from creatures.nanobot.prompts.skills.group_analysis.memory_candidates import extract_and_persist
    from creatures.nanobot.prompts.skills.group_analysis.preprocess import (
        build_analysis_payload,
        dedupe_group_logs,
        filter_analyzable_logs,
        resolve_analysis_window_hours,
    )
    from creatures.nanobot.prompts.skills.group_analysis.repository import GroupAnalysisRepository

    repo = GroupAnalysisRepository(db)
    group = repo.resolve_group(group_id)
    if not group:
        raise GroupMemoryGroupNotFound(f"未找到群: {group_id}")

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

    analysis = await analyzer.analyze_group(payload, instructions)
    source_meta = {
        "source": "manual_group_memory_extract",
        "latest_log_id": batch.latest_log_id,
        "raw_count": batch.raw_count,
        "window_hours": resolved_window_hours or 0,
        "source_log_ids": payload.get("source_log_ids", []),
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    stats = extract_and_persist(group.group_id, analysis, source_meta=source_meta)

    try:
        db.expire_all()
    except Exception:
        pass
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
    from core.database import GroupMemory
    from core.group_memory import should_inject
    from core.group_runtime.ids import normalize_group_session_id

    norm = normalize_group_session_id(group_id)
    rows = db.query(GroupMemory).filter(GroupMemory.group_id == norm).all()
    return {
        "memory_count": len(rows),
        "active_count": sum(1 for row in rows if row.status == "active"),
        "injectable_count": sum(1 for row in rows if should_inject(_memory_row_to_dict(row))),
    }


def build_group_memory_overview(db, *, limit: int = 300) -> list[dict]:
    """返回所有有群聊日志或群体记忆的群概览。"""
    from core.database import ChatLog, ChatStreamConfig, GroupMemory, User
    from core.group_runtime.ids import normalize_group_session_id

    groups: dict[str, dict] = {}

    def ensure(group_id: str) -> dict:
        norm = normalize_group_session_id(group_id)
        row = groups.setdefault(norm, {
            "group_id": norm,
            "raw_group_id": norm.removeprefix("group_"),
            "stream_id": f"qq:{norm.removeprefix('group_')}:group",
            "session_name": "",
            "log_count": 0,
            "latest_log_at": "",
            "memory_count": 0,
            "active_count": 0,
            "injectable_count": 0,
            "latest_memory_at": "",
            "last_injected_at": "",
            "recent_injected_ids": [],
            "_recent_injected_pairs": [],
            "group_profile_mode": "off",
        })
        return row

    log_rows = (
        db.query(
            ChatLog.session_id,
            func.count(ChatLog.id),
            func.max(ChatLog.created_at),
            func.max(ChatLog.session_name),
        )
        .filter(ChatLog.session_id.like("group_%"))
        .filter(ChatLog.role.in_(("ambient", "user", "assistant")))
        .group_by(ChatLog.session_id)
        .all()
    )
    for session_id, log_count, latest_at, session_name in log_rows:
        row = ensure(session_id)
        row["log_count"] = int(log_count or 0)
        row["latest_log_at"] = _fmt_dt(latest_at)
        row["session_name"] = session_name or row["session_name"]

    for user in db.query(User).filter(User.id.like("group_%")).all():
        row = ensure(user.id)
        if user.name:
            row["session_name"] = user.name

    memory_rows = (
        db.query(GroupMemory)
        .filter(GroupMemory.group_id.like("group_%"))
        .order_by(GroupMemory.last_seen.desc(), GroupMemory.id.desc())
        .all()
    )
    for memory in memory_rows:
        row = ensure(memory.group_id)
        row["memory_count"] += 1
        if memory.status == "active":
            row["active_count"] += 1
        if not row["latest_memory_at"]:
            row["latest_memory_at"] = _fmt_dt(memory.last_seen or memory.updated_at)
        injected_at = _fmt_dt(getattr(memory, "last_injected_at", None))
        if injected_at:
            if not row["last_injected_at"] or injected_at > row["last_injected_at"]:
                row["last_injected_at"] = injected_at
            row["_recent_injected_pairs"].append((memory.last_injected_at, memory.id))

    from core.group_memory import should_inject
    for memory in memory_rows:
        if should_inject(_memory_row_to_dict(memory)):
            ensure(memory.group_id)["injectable_count"] += 1

    cfg_rows = db.query(ChatStreamConfig).filter(
        or_(
            ChatStreamConfig.chat_stream_id.like("qq:%:group"),
            ChatStreamConfig.chat_stream_id.like("group_%"),
        )
    ).all()
    for cfg in cfg_rows:
        group_id = _group_id_from_stream_id(cfg.chat_stream_id)
        if group_id:
            ensure(group_id)["group_profile_mode"] = cfg.group_profile_mode or "off"

    items = list(groups.values())
    for item in items:
        pairs = sorted(item.pop("_recent_injected_pairs", []), key=lambda pair: pair[0], reverse=True)
        item["recent_injected_ids"] = [memory_id for _, memory_id in pairs[:10]]
    items.sort(key=lambda item: (
        item["latest_log_at"] or item["latest_memory_at"] or "",
        item["memory_count"],
    ), reverse=True)
    return items[:max(1, min(int(limit or 300), 1000))]


def _memory_row_to_dict(row) -> dict:
    return {
        "status": row.status,
        "inject_policy": getattr(row, "inject_policy", "auto") or "auto",
        "confidence": row.confidence,
        "decay_score": row.decay_score,
        "evidence_log_ids_json": row.evidence_log_ids_json,
        "memory_type": row.memory_type,
        "evidence_count": row.evidence_count,
    }


def _group_id_from_stream_id(stream_id: str) -> str:
    raw = str(stream_id or "").strip()
    if raw.startswith("qq:") and raw.endswith(":group"):
        return f"group_{raw.removeprefix('qq:').removesuffix(':group')}"
    if raw.startswith("group_"):
        return raw
    return ""


def _fmt_dt(value) -> str:
    return value.isoformat(sep=" ", timespec="seconds") if value else ""
