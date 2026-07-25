"""系统设置持久化的不可变 DTO 与 Repository Port。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class SystemSettingRecord:
    key: str
    value: str
    description: str
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class SystemSettingWriteRecord:
    key: str
    value: str
    description: str


@runtime_checkable
class SystemSettingRepositoryPort(Protocol):
    def get(self, key: str) -> SystemSettingRecord | None: ...

    def list_all(self) -> Sequence[SystemSettingRecord]: ...

    def list_by_keys(
        self,
        keys: Sequence[str],
    ) -> Sequence[SystemSettingRecord]: ...

    def upsert_many(
        self,
        writes: Sequence[SystemSettingWriteRecord],
    ) -> Sequence[SystemSettingRecord]: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


__all__ = [
    "SystemSettingRecord",
    "SystemSettingRepositoryPort",
    "SystemSettingWriteRecord",
]
