"""memory_digests 数据清洗——用新的 _format_log_line 逻辑重新生成摘要。

线上执行：
  1. 备份: cp data/nanobot.db data/nanobot.db.bak.$(date +%Y%m%d_%H%M%S)
  2. dry-run: clean_memory_digests(dry_run=True)
  3. 正式:   clean_memory_digests(dry_run=False)

只 UPDATE memory_digests.content，不删 ChatLog，不动 persona。
"""

import logging
from typing import List

from core.database import ChatLog, MemoryDigest, SessionLocal
from core.daily_digest import _format_log_line, _build_progressive_layers, _to_day

logger = logging.getLogger("nanobot.data_clean")


def _regenerate_digest(logs: List[ChatLog]) -> tuple[str, str, str]:
    lines = [l for x in logs if (l := _format_log_line(x)) is not None]
    if not lines:
        return ("", "", "")
    return _build_progressive_layers(lines)


def _normalize_session(log: ChatLog) -> str:
    sid = (log.session_id or "").strip()
    uid = (log.user_id or "").strip()
    if uid.startswith("group_"):
        return sid if sid.startswith("group_") else uid
    return sid


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
        logger.info("找到 %d 个 digest 分组", len(groups))

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
                    logger.info(
                        "[dry-run] %s/%s: %d rows, %d→%d chars (%.0f%% reduction)",
                        session_id, digest_date, len(existing),
                        old_total, new_total,
                        (1 - new_total / max(old_total, 1)) * 100,
                    )
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

    logger.info("清洗完成: %s", stats)
    return stats
