"""运行时工具决策仓库。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.database import RuntimeToolDecision


class RuntimeToolDecisionRepository:
    def __init__(self, db: Session):
        self.db = db

    def add(self, row: RuntimeToolDecision) -> RuntimeToolDecision:
        self.db.add(row)
        return row

    def recent_for_session(self, session_id: str, *, limit: int = 20) -> list[RuntimeToolDecision]:
        return (
            self.db.query(RuntimeToolDecision)
            .filter(RuntimeToolDecision.session_id == session_id)
            .order_by(RuntimeToolDecision.created_at.desc())
            .limit(limit)
            .all()
        )
