"""主动外呼评估租约的 Repository/Port 边界。"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import delete, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core.db.models.proactive import ProactiveOutreachLease
from core.sqlite_retry import run_sqlite_locked_retry


class EvaluationLeasePort(Protocol):
    def acquire(
        self,
        session: Session,
        *,
        user_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> str | None: ...

    def release(
        self,
        session: Session,
        *,
        user_id: str,
        owner_token: str,
    ) -> None: ...

    def is_owned(
        self,
        session: Session,
        *,
        user_id: str,
        owner_token: str,
        now: datetime | None = None,
    ) -> bool: ...

    def fence_write(
        self,
        session: Session,
        *,
        user_id: str,
        owner_token: str | None,
    ) -> bool: ...


class SqlAlchemyEvaluationLease:
    """在调用方事务中执行 SQLite CAS 的生产适配器。"""

    def acquire(
        self,
        session: Session,
        *,
        user_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> str | None:
        owner_token = secrets.token_hex(32)

        def operation() -> str | None:
            try:
                # 先取得写锁再冻结租约时刻，避免锁等待直接吞掉租期。
                session.execute(text("BEGIN IMMEDIATE"))
                attempt_now = max(now, datetime.now())
                lease_expires_at = attempt_now + timedelta(
                    seconds=max(1, int(lease_seconds))
                )
                insert_statement = (
                    sqlite_insert(ProactiveOutreachLease)
                    .values(
                        user_id=user_id,
                        owner_token=owner_token,
                        lease_expires_at=lease_expires_at,
                        created_at=attempt_now,
                        updated_at=attempt_now,
                    )
                    .on_conflict_do_nothing(index_elements=["user_id"])
                )
                takeover_statement = (
                    update(ProactiveOutreachLease)
                    .where(
                        ProactiveOutreachLease.user_id == user_id,
                        ProactiveOutreachLease.lease_expires_at <= attempt_now,
                    )
                    .values(
                        owner_token=owner_token,
                        lease_expires_at=lease_expires_at,
                        updated_at=attempt_now,
                    )
                )
                acquired = session.execute(insert_statement).rowcount == 1
                if not acquired:
                    acquired = session.execute(takeover_statement).rowcount == 1
                if not acquired:
                    session.rollback()
                    return None
                session.commit()
                return owner_token
            except BaseException:
                session.rollback()
                raise

        return run_sqlite_locked_retry(
            operation,
            rollback=session.rollback,
            label="acquire proactive outreach lease",
        )

    def release(
        self,
        session: Session,
        *,
        user_id: str,
        owner_token: str,
    ) -> None:
        statement = delete(ProactiveOutreachLease).where(
            ProactiveOutreachLease.user_id == user_id,
            ProactiveOutreachLease.owner_token == owner_token,
        )

        def operation() -> None:
            try:
                session.execute(statement)
                session.commit()
            except BaseException:
                session.rollback()
                raise

        run_sqlite_locked_retry(
            operation,
            rollback=session.rollback,
            label="release proactive outreach lease",
        )

    def is_owned(
        self,
        session: Session,
        *,
        user_id: str,
        owner_token: str,
        now: datetime | None = None,
    ) -> bool:
        current = now or datetime.now()
        return bool(
            session.query(ProactiveOutreachLease.user_id)
            .filter(ProactiveOutreachLease.user_id == user_id)
            .filter(ProactiveOutreachLease.owner_token == owner_token)
            .filter(ProactiveOutreachLease.lease_expires_at > current)
            .first()
        )

    def fence_write(
        self,
        session: Session,
        *,
        user_id: str,
        owner_token: str | None,
    ) -> bool:
        if owner_token is None:
            return True

        def operation() -> bool:
            fence_at = datetime.now()
            matched = (
                session.query(ProactiveOutreachLease)
                .filter(ProactiveOutreachLease.user_id == user_id)
                .filter(ProactiveOutreachLease.owner_token == owner_token)
                .filter(ProactiveOutreachLease.lease_expires_at > fence_at)
                .update(
                    {ProactiveOutreachLease.updated_at: fence_at},
                    synchronize_session=False,
                )
            )
            return matched == 1

        return bool(run_sqlite_locked_retry(
            operation,
            rollback=session.rollback,
            label="fence proactive outreach evaluation write",
        ))


DEFAULT_EVALUATION_LEASE: EvaluationLeasePort = SqlAlchemyEvaluationLease()


def acquire_evaluation_lease(
    session: Session,
    *,
    user_id: str,
    now: datetime,
    lease_seconds: int = 900,
) -> str | None:
    return DEFAULT_EVALUATION_LEASE.acquire(
        session,
        user_id=user_id,
        now=now,
        lease_seconds=lease_seconds,
    )


def release_evaluation_lease(
    session: Session,
    *,
    user_id: str,
    owner_token: str,
) -> None:
    DEFAULT_EVALUATION_LEASE.release(
        session,
        user_id=user_id,
        owner_token=owner_token,
    )


def evaluation_lease_is_owned(
    session: Session,
    *,
    user_id: str,
    owner_token: str,
    now: datetime | None = None,
) -> bool:
    return DEFAULT_EVALUATION_LEASE.is_owned(
        session,
        user_id=user_id,
        owner_token=owner_token,
        now=now,
    )


def fence_evaluation_write(
    session: Session,
    *,
    user_id: str,
    owner_token: str | None,
) -> bool:
    return DEFAULT_EVALUATION_LEASE.fence_write(
        session,
        user_id=user_id,
        owner_token=owner_token,
    )


__all__ = [
    "DEFAULT_EVALUATION_LEASE",
    "EvaluationLeasePort",
    "SqlAlchemyEvaluationLease",
    "acquire_evaluation_lease",
    "evaluation_lease_is_owned",
    "fence_evaluation_write",
    "release_evaluation_lease",
]
