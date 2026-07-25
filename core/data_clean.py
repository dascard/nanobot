"""memory_digests 数据清洗——用新的 _format_log_line 逻辑重新生成摘要。

线上执行：
  1. 备份: cp data/nanobot.db data/nanobot.db.bak.$(date +%Y%m%d_%H%M%S)
  2. dry-run: clean_memory_digests(dry_run=True)
  3. 正式:   clean_memory_digests(dry_run=False)

只 UPDATE memory_digests.content，不删 ChatLog，不动 persona。
"""

import logging

from core.database import ChatLog, MemoryDigest, SessionLocal
from core.daily_digest import (
    _build_progressive_layers,
    _format_log_line,
    _normalize_chatlog_session_id,
    _to_day,
)

logger = logging.getLogger("nanobot.data_clean")


def _regenerate_digest(logs: list[ChatLog]) -> tuple[str, str, str]:
    lines = [formatted_line for x in logs if (formatted_line := _format_log_line(x)) is not None]
    if not lines:
        return ("", "", "")
    return _build_progressive_layers(lines)


def _normalize_session(log: ChatLog) -> str:
    return _normalize_chatlog_session_id(
        log.session_id,
        log.user_id,
    )


def clean_memory_digests(dry_run: bool = True) -> dict:
    """遍历 memory_digests，用清洗后的 _format_log_line 重新生成。

    返回: {"deleted": N, "updated": N, "skipped": N, "errors": [...]}
    """
    # 抑制 compaction 的 COMPACT_API_KEY 警告
    logging.getLogger("nanobot.compact").setLevel(logging.ERROR)

    db = SessionLocal()
    stats = {"deleted": 0, "updated": 0, "skipped": 0, "errors": []}

    try:
        groups = (
            db.query(MemoryDigest.session_id, MemoryDigest.digest_date)
            .distinct().all()
        )
        print(f"[data_clean] {'[DRY-RUN] ' if dry_run else ''}{len(groups)} 个 digest 分组待检查")

        for session_id, digest_date in groups:
            try:
                logs = (
                    db.query(ChatLog)
                    .filter(ChatLog.created_at.isnot(None))
                    .order_by(ChatLog.id.asc()).all()
                )
                day_logs = [
                    x for x in logs
                    if _to_day(x.created_at) == digest_date
                    and _normalize_session(x) == session_id
                ]
                if not day_logs:
                    stats["skipped"] += 1
                    continue

                existing = (
                    db.query(MemoryDigest)
                    .filter(
                        MemoryDigest.session_id == session_id,
                        MemoryDigest.digest_date == digest_date,
                    )
                    .order_by(MemoryDigest.level.asc()).all()
                )
                if not existing:
                    stats["skipped"] += 1
                    continue

                if dry_run:
                    old_total = sum(len(d.content or "") for d in existing)
                    l0, l1, l2 = _regenerate_digest(day_logs)
                    new_total = len(l0) + len(l1) + len(l2)
                    pct = (1 - new_total / max(old_total, 1)) * 100
                    print(f"  {session_id}/{digest_date}: {old_total}→{new_total} chars ({pct:.0f}%)")
                    stats["updated"] += 1
                    continue

                l0, l1, l2 = _regenerate_digest(day_logs)
                contents = {0: l0, 1: l1, 2: l2}

                for d in existing:
                    new_content = contents.get(d.level, d.content or "")
                    if new_content != (d.content or ""):
                        d.content = new_content
                        stats["updated"] += 1

                db.commit()

            except Exception as e:
                stats["errors"].append(f"{session_id}/{digest_date}: {e}")
                logger.warning("清洗失败 %s/%s: %s", session_id, digest_date, e)
                db.rollback()

    finally:
        db.close()

    print(f"[data_clean] 完成: updated={stats['updated']} skipped={stats['skipped']} errors={len(stats['errors'])}")
    logger.info("清洗完成: %s", stats)
    return stats
