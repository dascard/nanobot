"""系统设置仓库。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.database import SystemSetting


class SettingsRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, key: str) -> SystemSetting | None:
        return db_query_setting(self.db, key)

    def set(self, key: str, value: str, *, description: str = "") -> SystemSetting:
        row = db_query_setting(self.db, key)
        if row is None:
            row = SystemSetting(key=key, value=value, description=description)
            self.db.add(row)
        else:
            row.value = value
            if description:
                row.description = description
        return row


def db_query_setting(db: Session, key: str) -> SystemSetting | None:
    return db.query(SystemSetting).filter(SystemSetting.key == key).first()
