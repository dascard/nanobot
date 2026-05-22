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
        """群号/群名解析。

        支持纯群号、group_ session_id、qq:<id>:group stream_id、群名精确/模糊匹配。
        群名匹配会合并 User.name、ChatLog.session_name 和运行时 session_name。
        """
        query = str(group_id or "").strip()
        if not query:
            return None

        direct_group_id = self._normalize_direct_group_id(query)
        if direct_group_id:
            return self._build_group_ref(direct_group_id)

        candidates = self._find_group_candidates(query, limit=20)
        if not candidates:
            return None

        exact = [
            item for item in candidates
            if str(item.get("name") or "").strip().lower() == query.lower()
        ]
        if len(exact) == 1:
            return self._build_group_ref(str(exact[0]["group_id"]))
        if len(candidates) == 1:
            return self._build_group_ref(str(candidates[0]["group_id"]))

        # 多匹配 → 返回 None，交由上层展示候选列表
        logger.info(
            "[group_analysis.repo] 群名 '%s' 匹配到 %d 个群: %s",
            query, len(candidates),
            [(item.get("group_id"), item.get("name")) for item in candidates],
        )
        return None

    @staticmethod
    def _normalize_direct_group_id(group_id: str) -> str:
        raw = str(group_id or "").strip()
        if raw.isdigit():
            return f"group_{raw}"
        if raw.startswith("group_"):
            return raw
        if raw.startswith("qq:") and raw.endswith(":group"):
            return f"group_{raw.removeprefix('qq:').removesuffix(':group')}"
        return ""

    def _build_group_ref(self, group_id: str) -> GroupRef | None:
        from core.database import ChatLog, ChatStreamConfig, User

        ngid = str(group_id or "").strip()
        if not ngid:
            return None
        legacy_id = ngid.removeprefix("group_")
        user = self.db.query(User).filter(User.id == ngid).first()
        name = user.name if user else ""

        latest_log = self.db.query(ChatLog).filter(
            or_(ChatLog.session_id == ngid, ChatLog.session_id == legacy_id),
        ).order_by(ChatLog.id.desc()).first()
        if not name and latest_log:
            name = latest_log.session_name or ""

        if not name:
            runtime_info = self._runtime_group_info(ngid)
            name = str(runtime_info.get("session_name") or "")

        if not user:
            stream_id = f"qq:{legacy_id}:group"
            has_config = self.db.query(ChatStreamConfig).filter(
                ChatStreamConfig.chat_stream_id == stream_id,
            ).first()
            has_runtime = bool(self._runtime_group_info(ngid))
            if not latest_log and not has_config and not has_runtime:
                logger.info("[group_analysis.repo] 群 %s 无 User 记录且无 ChatLog", ngid)
                return None

        return GroupRef(
            group_id=ngid,
            legacy_group_id=legacy_id,
            name=name or legacy_id,
        )

    def get_group_candidates(self, group_id: str) -> list[dict]:
        """群名模糊匹配的候选列表——用于 resolve_group 返回 None 后展示选项。"""
        group_id = str(group_id or "").strip()
        if not group_id:
            return []
        return [
            {"id": item["group_id"].removeprefix("group_"), "name": item["name"]}
            for item in self._find_group_candidates(group_id, limit=10)
        ]

    @staticmethod
    def _name_matches(name: str, query: str) -> bool:
        name_norm = GroupAnalysisRepository._normalize_match_text(name)
        query_norm = GroupAnalysisRepository._normalize_match_text(query)
        if not name_norm or not query_norm:
            return False
        if query_norm in name_norm:
            return True
        if len(query_norm) < 3:
            return False
        pos = 0
        for ch in query_norm:
            idx = name_norm.find(ch, pos)
            if idx < 0:
                return False
            pos = idx + 1
        return True

    @staticmethod
    def _normalize_match_text(value: str) -> str:
        text = str(value or "").strip().lower()
        return "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")

    @staticmethod
    def _runtime_group_info(group_id: str) -> dict:
        try:
            from core.timing_runtime import get_group_runtime
            snapshot = get_group_runtime().snapshot_states()
            return snapshot.get(group_id) or {}
        except Exception:
            return {}

    def _find_group_candidates(self, query: str, limit: int = 10) -> list[dict]:
        from core.database import ChatLog, User

        query = str(query or "").strip()
        if not query:
            return []
        candidates: dict[str, dict] = {}

        def add(group_id: str, name: str = "", source: str = "", recent_at=None):
            gid = self._normalize_direct_group_id(group_id)
            if not gid:
                return
            display_name = str(name or "").strip()
            if not display_name and not self._name_matches(gid, query):
                return
            if display_name and not self._name_matches(display_name, query):
                return
            old = candidates.get(gid)
            if old:
                sources = set(old.get("sources") or [])
                if source:
                    sources.add(source)
                old["sources"] = sorted(sources)
                if display_name and not old.get("name"):
                    old["name"] = display_name
                if recent_at and (not old.get("_recent_at") or recent_at > old["_recent_at"]):
                    old["_recent_at"] = recent_at
                return
            candidates[gid] = {
                "group_id": gid,
                "id": gid.removeprefix("group_"),
                "name": display_name or gid,
                "sources": [source] if source else [],
                "_recent_at": recent_at,
            }

        # 群名常带学校/分隔符/后缀，不能只靠 SQL LIKE 精确包含 query；
        # 先取有限候选，再用 _name_matches 做标准化和有序匹配。
        for row in self.db.query(User).filter(
            User.id.like("group_%"),
        ).limit(1000).all():
            add(row.id, row.name or "", "users", None)

        for row in self.db.query(ChatLog).filter(
            ChatLog.session_id.like("group_%"),
            ChatLog.session_name != "",
        ).order_by(ChatLog.id.desc()).limit(500).all():
            add(row.session_id or "", row.session_name or "", "chat_logs", row.created_at)

        try:
            from core.timing_runtime import get_group_runtime
            for gid, info in get_group_runtime().snapshot_states().items():
                add(gid, str(info.get("session_name") or ""), "runtime", None)
        except Exception:
            pass

        items = list(candidates.values())
        items.sort(key=lambda item: (
            0 if str(item.get("name") or "").strip().lower() == query.lower() else 1,
            -(item.get("_recent_at").timestamp() if item.get("_recent_at") else 0),
            item.get("name") or "",
        ))
        for item in items:
            item.pop("_recent_at", None)
        return items[:max(1, min(int(limit or 10), 50))]

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
        ).filter(ChatLog.role.in_(("ambient", "user", "assistant")))

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
                meta_json=getattr(r, "meta_json", "") or "{}",
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
