"""SQL 查询层——不调用 LLM。"""

import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import or_

from .schemas import GroupRef, RawChatLog, GroupLogBatch

logger = logging.getLogger("nanobot.tool.group_analysis.repository")


class GroupAnalysisRepository:
    def __init__(self, db):
        self.db = db

    def resolve_group(self, group_id: str) -> GroupRef | None:
        """群号/群名解析。纯数字→group_xxx，group_前缀→直接查，其他→name LIKE。"""
        from core.database import User

        group_id = str(group_id or "").strip()
        if not group_id:
            return None

        if group_id.isdigit():
            ngid = f"group_{group_id}"
        elif group_id.startswith("group_"):
            ngid = group_id
        else:
            matched = (
                self.db.query(User)
                .filter(
                    User.id.like("group_%"),
                    User.name.like(f"%{group_id}%"),
                )
                .all()
            )
            if len(matched) == 0:
                return None
            if len(matched) > 1:
                # 多匹配 → 返回 None，交由上层展示候选列表
                logger.info(
                    "[group_analysis.repo] 群名 '%s' 匹配到 %d 个群: %s",
                    group_id, len(matched),
                    [(u.id, u.name) for u in matched],
                )
                return None
            ngid = matched[0].id

        legacy_id = ngid.removeprefix("group_")
        user = self.db.query(User).filter(User.id == ngid).first()
        # 不存在的 group_xxx → 查 ChatLog 确认是否存在
        if not user:
            from core.database import ChatLog
            has_logs = self.db.query(ChatLog).filter(
                or_(ChatLog.session_id == ngid, ChatLog.session_id == legacy_id),
            ).first()
            if not has_logs:
                logger.info("[group_analysis.repo] 群 %s 无 User 记录且无 ChatLog", ngid)
                return None
            name = legacy_id
        else:
            name = user.name or legacy_id

        return GroupRef(
            group_id=ngid,
            legacy_group_id=legacy_id,
            name=name,
        )

    def get_group_candidates(self, group_id: str) -> list[dict]:
        """群名模糊匹配的候选列表——用于 resolve_group 返回 None 后展示选项。"""
        from core.database import User

        group_id = str(group_id or "").strip()
        if not group_id:
            return []
        matched = (
            self.db.query(User)
            .filter(User.id.like("group_%"), User.name.like(f"%{group_id}%"))
            .limit(10).all()
        )
        return [
            {"id": u.id.removeprefix("group_"), "name": u.name or u.id}
            for u in matched
        ]

    def fetch_group_logs(
        self,
        group: GroupRef,
        *,
        window_hours: int | None = None,
        limit: int = 5000,
    ) -> GroupLogBatch:
        """查群聊 ChatLog。时间过滤已下推 SQL 层。"""
        from core.database import ChatLog

        t0 = time.monotonic()

        q = self.db.query(ChatLog).filter(
            or_(
                ChatLog.session_id == group.group_id,
                ChatLog.session_id == group.legacy_group_id,
            )
        )

        if window_hours and window_hours > 0:
            cutoff = datetime.now() - timedelta(hours=window_hours)
            q = q.filter(ChatLog.created_at >= cutoff)

        rows = (
            q.order_by(ChatLog.id.desc())
            .limit(limit)
            .all()
        )
        rows.reverse()

        logs = [
            RawChatLog(
                id=getattr(r, "id", 0) or 0,
                role=getattr(r, "role", "") or "",
                user_id=getattr(r, "user_id", "") or "",
                sender_name=getattr(r, "sender_name", "") or "",
                content=getattr(r, "content", "") or "",
                created_at=getattr(r, "created_at", None),
                created_at_ts=(
                    getattr(r, "created_at", None).timestamp()
                    if getattr(r, "created_at", None)
                    else None
                ),
                message_id=getattr(r, "message_id", "") or "",
                source_message_ids_json=getattr(r, "source_message_ids_json", "") or "",
                session_id=getattr(r, "session_id", "") or "",
            )
            for r in rows
        ]

        sql_ms = round((time.monotonic() - t0) * 1000)
        logger.info("[group_analysis.repo] %s window=%sh raw=%d sql_ms=%d",
                     group.group_id, window_hours or 0, len(logs), sql_ms)

        return GroupLogBatch(
            group=group,
            logs=logs,
            latest_log_id=max((x.id for x in logs), default=None),
            raw_count=len(logs),
        )
