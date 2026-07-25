"""群体记忆只读查询服务。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from core.chat_stream_identity import (
    ChatStreamIdentity,
    identity_storage_aliases,
    parse_compatibility_chat_stream_identity,
)
from core.db.group_memory_contracts import (
    GroupMemoryQueryRepositoryPort,
    GroupMemoryRecord,
)
from core.group_memory import (
    group_memory_compatibility_projection,
    resolve_group_memory_identity,
    validate_group_memory_row_identity,
)
from core.group_learning.prompt_injection import (
    evaluate_group_memory_prompt_injection,
)


@dataclass(frozen=True, slots=True)
class GroupMemoryListResult:
    group_id: str
    chat_stream_id: str
    memories: tuple[GroupMemoryRecord, ...]

    @property
    def total(self) -> int:
        return len(self.memories)


@dataclass(frozen=True, slots=True)
class GroupMemoryCounts:
    memory_count: int
    active_count: int
    injectable_count: int

    def to_dict(self) -> dict[str, int]:
        return {
            "memory_count": self.memory_count,
            "active_count": self.active_count,
            "injectable_count": self.injectable_count,
        }


@dataclass(frozen=True, slots=True)
class GroupMemoryOverviewItem:
    group_id: str
    raw_group_id: str
    stream_id: str
    session_name: str
    log_count: int
    latest_log_at: str
    memory_count: int
    active_count: int
    injectable_count: int
    latest_memory_at: str
    last_injected_at: str
    recent_injected_ids: tuple[int, ...]
    group_profile_mode: str

    def to_dict(self) -> dict[str, object]:
        return {
            "group_id": self.group_id,
            "raw_group_id": self.raw_group_id,
            "stream_id": self.stream_id,
            "session_name": self.session_name,
            "log_count": self.log_count,
            "latest_log_at": self.latest_log_at,
            "memory_count": self.memory_count,
            "active_count": self.active_count,
            "injectable_count": self.injectable_count,
            "latest_memory_at": self.latest_memory_at,
            "last_injected_at": self.last_injected_at,
            "recent_injected_ids": list(self.recent_injected_ids),
            "group_profile_mode": self.group_profile_mode,
        }


class GroupMemoryQueryService:
    def __init__(
        self,
        repository: GroupMemoryQueryRepositoryPort,
    ) -> None:
        self.repository = repository

    def list_memories(
        self,
        group_id: str,
        *,
        platform: str = "qq",
        memory_type: str = "",
        limit: int = 100,
    ) -> GroupMemoryListResult:
        identity = resolve_group_memory_identity(
            group_id,
            platform=platform,
        )
        legacy_group_ids = identity_storage_aliases(
            identity,
            include_raw_group_id=True,
        )
        rows = tuple(
            self.repository.list_memories(
                chat_stream_id=identity.chat_stream_id,
                legacy_group_ids=legacy_group_ids,
                memory_type=str(memory_type or "").strip(),
                limit=max(1, min(int(limit), 2000)),
            )
        )
        for row in rows:
            validate_group_memory_row_identity(row, identity)
        return GroupMemoryListResult(
            group_id=group_memory_compatibility_projection(identity),
            chat_stream_id=identity.chat_stream_id,
            memories=rows,
        )

    def counts(
        self,
        group_id: str,
        *,
        platform: str = "qq",
    ) -> GroupMemoryCounts:
        identity = resolve_group_memory_identity(
            group_id,
            platform=platform,
        )
        rows = tuple(
            self.repository.list_memories(
                chat_stream_id=identity.chat_stream_id,
                legacy_group_ids=identity_storage_aliases(
                    identity,
                    include_raw_group_id=True,
                ),
                limit=100_000,
            )
        )
        for row in rows:
            validate_group_memory_row_identity(row, identity)
        return GroupMemoryCounts(
            memory_count=len(rows),
            active_count=sum(
                1 for row in rows if row.status == "active"
            ),
            injectable_count=sum(
                1
                for row in rows
                if evaluate_group_memory_prompt_injection(row).eligible
            ),
        )

    def get_profile_mode(
        self,
        chat_stream_ids: tuple[str, ...] | list[str],
    ) -> str:
        row = self.repository.get_stream_config(chat_stream_ids)
        return row.group_profile_mode if row is not None else "off"

    def build_overview(
        self,
        *,
        limit: int = 300,
    ) -> tuple[GroupMemoryOverviewItem, ...]:
        groups: dict[str, dict[str, object]] = {}

        def ensure(identity: ChatStreamIdentity) -> dict[str, object]:
            return groups.setdefault(
                identity.chat_stream_id,
                {
                    "group_id": group_memory_compatibility_projection(
                        identity
                    ),
                    "raw_group_id": identity.external_session_id,
                    "stream_id": identity.chat_stream_id,
                    "session_name": "",
                    "log_count": 0,
                    "latest_log_at": "",
                    "memory_count": 0,
                    "active_count": 0,
                    "injectable_count": 0,
                    "latest_memory_at": "",
                    "last_injected_at": "",
                    "recent_injected_pairs": [],
                    "group_profile_mode": "off",
                },
            )

        for summary in self.repository.list_group_log_summaries():
            identity = _group_identity_from_storage(
                summary.session_id,
                summary.user_id,
            )
            if identity is None:
                continue
            row = ensure(identity)
            row["log_count"] = summary.log_count
            row["latest_log_at"] = _fmt_dt(summary.latest_at)
            row["session_name"] = (
                summary.session_name or row["session_name"]
            )

        for user in self.repository.list_group_users():
            identity = _group_identity_from_storage(user.user_id)
            if identity is None:
                continue
            row = ensure(identity)
            if user.name:
                row["session_name"] = user.name

        memories = tuple(self.repository.list_all_memories())
        for memory in memories:
            identity = _group_identity_from_storage(
                memory.chat_stream_id,
                memory.group_id,
            )
            if identity is None:
                continue
            validate_group_memory_row_identity(memory, identity)
            row = ensure(identity)
            row["memory_count"] = int(row["memory_count"]) + 1
            if memory.status == "active":
                row["active_count"] = int(row["active_count"]) + 1
            if not row["latest_memory_at"]:
                row["latest_memory_at"] = _fmt_dt(
                    memory.last_seen or memory.updated_at
                )
            if memory.last_injected_at is not None:
                injected_at = _fmt_dt(memory.last_injected_at)
                if (
                    not row["last_injected_at"]
                    or injected_at > str(row["last_injected_at"])
                ):
                    row["last_injected_at"] = injected_at
                pairs = row["recent_injected_pairs"]
                assert isinstance(pairs, list)
                pairs.append((memory.last_injected_at, memory.id))
            if evaluate_group_memory_prompt_injection(memory).eligible:
                row["injectable_count"] = (
                    int(row["injectable_count"]) + 1
                )

        for config in self.repository.list_stream_configs():
            identity = _group_identity_from_storage(
                config.chat_stream_id
            )
            if identity is not None:
                ensure(identity)["group_profile_mode"] = (
                    config.group_profile_mode or "off"
                )

        items: list[GroupMemoryOverviewItem] = []
        for row in groups.values():
            pairs = row.pop("recent_injected_pairs")
            assert isinstance(pairs, list)
            pairs.sort(key=lambda pair: pair[0], reverse=True)
            items.append(
                GroupMemoryOverviewItem(
                    group_id=str(row["group_id"]),
                    raw_group_id=str(row["raw_group_id"]),
                    stream_id=str(row["stream_id"]),
                    session_name=str(row["session_name"]),
                    log_count=int(row["log_count"]),
                    latest_log_at=str(row["latest_log_at"]),
                    memory_count=int(row["memory_count"]),
                    active_count=int(row["active_count"]),
                    injectable_count=int(row["injectable_count"]),
                    latest_memory_at=str(row["latest_memory_at"]),
                    last_injected_at=str(row["last_injected_at"]),
                    recent_injected_ids=tuple(
                        int(memory_id)
                        for _, memory_id in pairs[:10]
                    ),
                    group_profile_mode=str(
                        row["group_profile_mode"]
                    ),
                )
            )
        items.sort(
            key=lambda item: (
                item.latest_log_at or item.latest_memory_at,
                item.memory_count,
            ),
            reverse=True,
        )
        normalized_limit = max(1, min(int(limit or 300), 1000))
        return tuple(items[:normalized_limit])


def _group_identity_from_storage(
    *values: object,
) -> ChatStreamIdentity | None:
    for value in values:
        identity = parse_compatibility_chat_stream_identity(
            str(value or ""),
            legacy_platform="qq",
        )
        if identity is not None and identity.chat_type == "group":
            return identity
    return None


def _fmt_dt(value: datetime | None) -> str:
    return (
        value.isoformat(sep=" ", timespec="seconds")
        if value
        else ""
    )


__all__ = [
    "GroupMemoryCounts",
    "GroupMemoryListResult",
    "GroupMemoryOverviewItem",
    "GroupMemoryQueryService",
]
