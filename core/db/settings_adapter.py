"""系统设置 Repository Port 的 SQLAlchemy Adapter。"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from core.db.models import SystemSetting
from core.db.settings_contracts import (
    SystemSettingRecord,
    SystemSettingRepositoryPort,
    SystemSettingWriteRecord,
)


def _setting_record(row: SystemSetting) -> SystemSettingRecord:
    return SystemSettingRecord(
        key=str(row.key or ""),
        value=str(row.value or ""),
        description=str(getattr(row, "description", "") or ""),
        updated_at=getattr(row, "updated_at", None),
    )


class SqlAlchemySystemSettingRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, key: str) -> SystemSettingRecord | None:
        row = self._session.get(SystemSetting, str(key))
        return _setting_record(row) if row is not None else None

    def list_all(self) -> tuple[SystemSettingRecord, ...]:
        return tuple(
            _setting_record(row)
            for row in self._session.query(SystemSetting).all()
        )

    def list_by_keys(
        self,
        keys: Sequence[str],
    ) -> tuple[SystemSettingRecord, ...]:
        normalized = tuple(
            dict.fromkeys(
                str(key or "").strip()
                for key in keys
                if str(key or "").strip()
            )
        )
        if not normalized:
            return ()
        rows = (
            self._session.query(SystemSetting)
            .filter(SystemSetting.key.in_(normalized))
            .all()
        )
        by_key = {str(row.key): row for row in rows}
        return tuple(
            _setting_record(by_key[key])
            for key in normalized
            if key in by_key
        )

    def upsert_many(
        self,
        writes: Sequence[SystemSettingWriteRecord],
    ) -> tuple[SystemSettingRecord, ...]:
        keys = tuple(write.key for write in writes)
        existing = {
            str(row.key): row
            for row in (
                self._session.query(SystemSetting)
                .filter(SystemSetting.key.in_(keys or ("__none__",)))
                .all()
            )
        }
        rows: list[SystemSetting] = []
        for write in writes:
            row = existing.get(write.key)
            if row is None:
                row = SystemSetting(
                    key=write.key,
                    value=write.value,
                    description=write.description,
                )
                self._session.add(row)
                existing[write.key] = row
            else:
                row.value = write.value
                if write.description:
                    row.description = write.description
            rows.append(row)
        self._session.flush()
        return tuple(_setting_record(row) for row in rows)

    def delete_many(self, keys: Sequence[str]) -> int:
        normalized = tuple(
            dict.fromkeys(
                str(key or "").strip()
                for key in keys
                if str(key or "").strip()
            )
        )
        if not normalized:
            return 0
        deleted = (
            self._session.query(SystemSetting)
            .filter(SystemSetting.key.in_(normalized))
            .delete(synchronize_session=False)
        )
        self._session.flush()
        return int(deleted or 0)

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()


def system_setting_repository(
    value: Session | SystemSettingRepositoryPort,
) -> SystemSettingRepositoryPort:
    if isinstance(value, SystemSettingRepositoryPort):
        return value
    return SqlAlchemySystemSettingRepository(value)


__all__ = [
    "SqlAlchemySystemSettingRepository",
    "system_setting_repository",
]
