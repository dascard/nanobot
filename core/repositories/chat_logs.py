"""聊天日志仓库。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.database import ChatLog


class ChatLogRepository:
    def __init__(self, db: Session):
        self.db = db

    def add(self, row: ChatLog) -> ChatLog:
        self.db.add(row)
        return row

    def find_ambient_by_message_id(self, session_id: str, message_id: str) -> ChatLog | None:
        if not message_id:
            return None
        return (
            self.db.query(ChatLog)
            .filter(
                ChatLog.session_id == session_id,
                ChatLog.message_id == message_id,
                ChatLog.role == "ambient",
            )
            .first()
        )
