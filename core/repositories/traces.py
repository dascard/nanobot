"""运行轨迹仓库。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.database import AgentRun


class AgentRunRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, run_id: str) -> AgentRun | None:
        return self.db.query(AgentRun).filter(AgentRun.run_id == run_id).first()

    def add(self, row: AgentRun) -> AgentRun:
        self.db.add(row)
        return row
