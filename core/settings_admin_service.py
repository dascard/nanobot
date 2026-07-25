"""系统设置的 CQRS-lite Query/Command Service。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from core.db.settings_contracts import (
    SystemSettingRecord,
    SystemSettingRepositoryPort,
    SystemSettingWriteRecord,
)


@dataclass(frozen=True, slots=True)
class SystemSettingWrite:
    key: str
    value: str
    description: str = ""

    def __post_init__(self) -> None:
        key = str(self.key or "").strip()
        if not key:
            raise ValueError("设置 key 不能为空")
        if len(key) > 255:
            raise ValueError("设置 key 超过长度上限")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "value", str(self.value))
        object.__setattr__(
            self,
            "description",
            str(self.description or "").strip()[:500],
        )

    def to_record(self) -> SystemSettingWriteRecord:
        return SystemSettingWriteRecord(
            key=self.key,
            value=self.value,
            description=self.description,
        )


class SystemSettingQueryService:
    def __init__(self, repository: SystemSettingRepositoryPort) -> None:
        self.repository = repository

    def get(self, key: str) -> SystemSettingRecord | None:
        return self.repository.get(str(key or "").strip())

    def list_all(self) -> tuple[SystemSettingRecord, ...]:
        return tuple(self.repository.list_all())

    def list_by_keys(
        self,
        keys: Iterable[str],
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
        return tuple(self.repository.list_by_keys(normalized))


class SystemSettingCommandService:
    def __init__(self, repository: SystemSettingRepositoryPort) -> None:
        self.repository = repository

    def upsert(
        self,
        *,
        key: str,
        value: object,
        description: str = "",
    ) -> SystemSettingRecord:
        rows = self.upsert_many((
            SystemSettingWrite(
                key=key,
                value=str(value),
                description=description,
            ),
        ))
        return rows[0]

    def upsert_many(
        self,
        writes: Iterable[SystemSettingWrite],
    ) -> tuple[SystemSettingRecord, ...]:
        normalized = tuple(writes)
        if not normalized:
            return ()
        keys = [write.key for write in normalized]
        if len(keys) != len(set(keys)):
            raise ValueError("同一事务不能重复写入相同设置 key")
        try:
            rows = tuple(
                self.repository.upsert_many(
                    tuple(write.to_record() for write in normalized)
                )
            )
            if len(rows) != len(normalized):
                raise RuntimeError("设置仓储返回数量与写入数量不一致")
            self.repository.commit()
            return rows
        except BaseException:
            self.repository.rollback()
            raise


__all__ = [
    "SystemSettingCommandService",
    "SystemSettingQueryService",
    "SystemSettingWrite",
]
