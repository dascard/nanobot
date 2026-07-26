"""群体记忆 Repository Port 的 SQLAlchemy Adapter。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.db.group_memory_contracts import (
    ChatStreamConfigRecord,
    GroupLogSummaryRecord,
    GroupMemoryRecord,
    GroupMemoryRepositoryConflict,
    GroupMemoryRepositoryPort,
    GroupUserRecord,
)
from core.db.models import ChatLog, ChatStreamConfig, GroupMemory, User


def _memory_record(row: GroupMemory) -> GroupMemoryRecord:
    return GroupMemoryRecord(
        id=int(row.id or 0),
        chat_stream_id=str(row.chat_stream_id or ""),
        group_id=str(row.group_id or ""),
        memory_type=str(row.memory_type or ""),
        content=str(row.content or ""),
        content_hash=str(row.content_hash or ""),
        cluster_key=str(row.cluster_key or ""),
        evidence_log_ids_json=str(row.evidence_log_ids_json or "[]"),
        confidence=float(row.confidence or 0.0),
        evidence_count=int(row.evidence_count or 0),
        first_seen=row.first_seen,
        last_seen=row.last_seen,
        updated_at=row.updated_at,
        decay_score=float(row.decay_score or 0.0),
        status=str(row.status or ""),
        inject_policy=str(row.inject_policy or "auto"),
        disabled_reason=str(row.disabled_reason or ""),
        rejected_reason=str(row.rejected_reason or ""),
        merged_into_id=(
            int(row.merged_into_id)
            if row.merged_into_id is not None
            else None
        ),
        last_injected_at=row.last_injected_at,
        injected_count=int(row.injected_count or 0),
        source=str(row.source or "group_analysis"),
        meta_json=str(row.meta_json or "{}"),
        created_at=row.created_at,
        approval_source=str(row.approval_source or ""),
        governance_mode=str(row.governance_mode or ""),
        approved_content_hash=str(row.approved_content_hash or ""),
        model_review_run_id=str(row.model_review_run_id or ""),
        model_contract_version=str(row.model_contract_version or ""),
        human_reviewer_id=str(row.human_reviewer_id or ""),
        human_reviewed_at=row.human_reviewed_at,
        human_action=str(row.human_action or ""),
        conflict_group_id=str(row.conflict_group_id or ""),
        version=int(row.version or 1),
    )


def _config_record(row: ChatStreamConfig) -> ChatStreamConfigRecord:
    return ChatStreamConfigRecord(
        chat_stream_id=str(row.chat_stream_id or ""),
        group_profile_mode=str(row.group_profile_mode or "off"),
    )


def _identity_filter(
    *,
    chat_stream_id: str,
    legacy_group_ids: Sequence[str],
):
    aliases = tuple(
        dict.fromkeys(
            str(item or "").strip()
            for item in legacy_group_ids
            if str(item or "").strip()
        )
    )
    legacy = and_(
        or_(
            GroupMemory.chat_stream_id.is_(None),
            GroupMemory.chat_stream_id == "",
        ),
        GroupMemory.group_id.in_(aliases or ("__none__",)),
    )
    return or_(
        GroupMemory.chat_stream_id == str(chat_stream_id or "").strip(),
        legacy,
    )


class SqlAlchemyGroupMemoryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_memories(
        self,
        *,
        chat_stream_id: str,
        legacy_group_ids: Sequence[str],
        memory_type: str = "",
        limit: int = 100,
    ) -> tuple[GroupMemoryRecord, ...]:
        query = self._session.query(GroupMemory).filter(
            _identity_filter(
                chat_stream_id=chat_stream_id,
                legacy_group_ids=legacy_group_ids,
            )
        )
        if memory_type:
            query = query.filter(
                GroupMemory.memory_type == str(memory_type)
            )
        rows = (
            query.order_by(
                GroupMemory.confidence.desc(),
                GroupMemory.last_seen.desc(),
                GroupMemory.id.asc(),
            )
            .limit(max(1, min(int(limit), 100_000)))
            .all()
        )
        return tuple(_memory_record(row) for row in rows)

    def get_memory(self, memory_id: int) -> GroupMemoryRecord | None:
        row = self._session.get(GroupMemory, int(memory_id))
        return _memory_record(row) if row is not None else None

    def find_duplicate(
        self,
        *,
        chat_stream_id: str,
        legacy_group_ids: Sequence[str],
        memory_type: str,
        content_hash: str,
        exclude_id: int,
        alternate_hashes: Sequence[str] = (),
    ) -> GroupMemoryRecord | None:
        # 管理端(归一化 32 位)与群学习治理管线(原文 64 位)历史上使用
        # 不同的 content_hash 算法,存量行两种格式并存;查重必须同时匹配。
        hashes = sorted({
            str(content_hash),
            *(str(item) for item in alternate_hashes if str(item)),
        })
        row = (
            self._session.query(GroupMemory)
            .filter(
                GroupMemory.id != int(exclude_id),
                _identity_filter(
                    chat_stream_id=chat_stream_id,
                    legacy_group_ids=legacy_group_ids,
                ),
                GroupMemory.memory_type == str(memory_type),
                GroupMemory.content_hash.in_(hashes),
            )
            .first()
        )
        return _memory_record(row) if row is not None else None

    def list_all_memories(self) -> tuple[GroupMemoryRecord, ...]:
        rows = (
            self._session.query(GroupMemory)
            .order_by(
                GroupMemory.last_seen.desc(),
                GroupMemory.id.desc(),
            )
            .all()
        )
        return tuple(_memory_record(row) for row in rows)

    def list_group_log_summaries(
        self,
    ) -> tuple[GroupLogSummaryRecord, ...]:
        rows = (
            self._session.query(
                ChatLog.session_id,
                func.max(ChatLog.user_id),
                func.count(ChatLog.id),
                func.max(ChatLog.created_at),
                func.max(ChatLog.session_name),
            )
            .filter(
                ChatLog.role.in_(("ambient", "user", "assistant"))
            )
            .group_by(ChatLog.session_id)
            .all()
        )
        return tuple(
            GroupLogSummaryRecord(
                session_id=str(session_id or ""),
                user_id=str(user_id or ""),
                log_count=int(log_count or 0),
                latest_at=latest_at,
                session_name=str(session_name or ""),
            )
            for (
                session_id,
                user_id,
                log_count,
                latest_at,
                session_name,
            ) in rows
        )

    def list_group_users(self) -> tuple[GroupUserRecord, ...]:
        return tuple(
            GroupUserRecord(
                user_id=str(row.id or ""),
                name=str(row.name or ""),
            )
            for row in self._session.query(User).all()
        )

    def list_stream_configs(
        self,
    ) -> tuple[ChatStreamConfigRecord, ...]:
        return tuple(
            _config_record(row)
            for row in self._session.query(ChatStreamConfig).all()
        )

    def get_stream_config(
        self,
        chat_stream_ids: Sequence[str],
    ) -> ChatStreamConfigRecord | None:
        normalized = tuple(
            dict.fromkeys(
                str(item or "").strip()
                for item in chat_stream_ids
                if str(item or "").strip()
            )
        )
        if not normalized:
            return None
        rows = (
            self._session.query(ChatStreamConfig)
            .filter(ChatStreamConfig.chat_stream_id.in_(normalized))
            .all()
        )
        by_id = {str(row.chat_stream_id): row for row in rows}
        for candidate in normalized:
            row = by_id.get(candidate)
            if row is not None:
                return _config_record(row)
        return None

    def set_injection_mode(
        self,
        chat_stream_id: str,
        mode: str,
    ) -> ChatStreamConfigRecord:
        normalized_id = str(chat_stream_id or "").strip()
        row = self._session.get(ChatStreamConfig, normalized_id)
        if row is None:
            row = ChatStreamConfig(chat_stream_id=normalized_id)
            self._session.add(row)
        row.group_profile_mode = str(mode)
        self._session.flush()
        return _config_record(row)

    def update_memory(
        self,
        memory_id: int,
        **values: object,
    ) -> GroupMemoryRecord:
        row = self._session.get(GroupMemory, int(memory_id))
        if row is None:
            raise LookupError("group memory not found")
        for key, value in values.items():
            if not hasattr(row, key):
                raise ValueError(f"不支持更新字段：{key}")
            setattr(row, key, value)
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise GroupMemoryRepositoryConflict(
                "群体记忆唯一约束冲突"
            ) from exc
        return _memory_record(row)

    def mark_injected(
        self,
        memory_ids: Sequence[int],
        *,
        injected_at: datetime,
    ) -> int:
        normalized = tuple(
            sorted({int(item) for item in memory_ids if int(item) > 0})
        )
        if not normalized:
            return 0
        rows = (
            self._session.query(GroupMemory)
            .filter(GroupMemory.id.in_(normalized))
            .all()
        )
        for row in rows:
            row.last_injected_at = injected_at
            row.injected_count = int(row.injected_count or 0) + 1
        self._session.flush()
        return len(rows)

    def commit(self) -> None:
        try:
            self._session.commit()
        except IntegrityError as exc:
            raise GroupMemoryRepositoryConflict(
                "群体记忆唯一约束冲突"
            ) from exc

    def rollback(self) -> None:
        self._session.rollback()


def group_memory_repository(
    value: Session | GroupMemoryRepositoryPort,
) -> GroupMemoryRepositoryPort:
    if isinstance(value, GroupMemoryRepositoryPort):
        return value
    return SqlAlchemyGroupMemoryRepository(value)


__all__ = [
    "SqlAlchemyGroupMemoryRepository",
    "group_memory_repository",
]
