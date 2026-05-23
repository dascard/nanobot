"""用户仓库。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.database import User


class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, user_id: str) -> User | None:
        return self.db.query(User).filter(User.id == user_id).first()

    def ensure(self, user_id: str, *, name: str = "") -> User:
        row = self.get(user_id)
        if row is None:
            row = User(id=user_id, name=name)
            self.db.add(row)
        elif name and row.name != name:
            row.name = name
        return row
