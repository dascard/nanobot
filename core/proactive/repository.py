"""主动外呼查询的 SQLAlchemy Repository 适配器。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from core.db.models.chat import ConversationTurn, User
from core.db.models.proactive import ProactiveOutreachLog


def history_clear_at(session: Session, user_id: str) -> datetime | None:
    row = session.query(User.history_clear_at).filter(User.id == user_id).first()
    return row[0] if row and row[0] is not None else None


def cancelled_row_is_reusable(
    session: Session,
    *,
    row: ProactiveOutreachLog | None,
    user_id: str,
    generation_at: datetime,
) -> bool:
    """只让新的合法评估代际复用从未发布的 cancelled 行。"""

    if (
        row is None
        or row.user_id != user_id
        or row.status != "cancelled"
        or row.outbound_run_id is not None
    ):
        return False
    clear_at = history_clear_at(session, user_id)
    return clear_at is None or generation_at > clear_at


def private_conversation_query(
    session: Session,
    user_id: str,
    *,
    roles: tuple[str, ...],
):
    """返回已经应用历史清除 fence 的私聊 Query。"""

    query = (
        session.query(ConversationTurn)
        .filter(ConversationTurn.user_id == user_id)
        .filter(ConversationTurn.session_id.like(r"private\_%", escape="\\"))
        .filter(ConversationTurn.role.in_(roles))
        .filter(ConversationTurn.created_at.isnot(None))
    )
    clear_at = history_clear_at(session, user_id)
    if clear_at is not None:
        query = query.filter(ConversationTurn.created_at > clear_at)
    return query


def current_pending_schedule(
    session: Session,
    user_id: str,
) -> ProactiveOutreachLog | None:
    return (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .filter(ProactiveOutreachLog.status == "pending")
        .order_by(
            ProactiveOutreachLog.created_at.desc(),
            ProactiveOutreachLog.id.desc(),
        )
        .first()
    )


def current_schedule_row(
    session: Session,
    user_id: str,
) -> ProactiveOutreachLog | None:
    return (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .filter(ProactiveOutreachLog.status.in_((
            "pending",
            "candidate",
            "sending",
            "queued",
            "delivering",
            "retry_wait",
            "blocked",
        )))
        .order_by(
            ProactiveOutreachLog.created_at.desc(),
            ProactiveOutreachLog.id.desc(),
        )
        .first()
    )


def latest_outreach_row(
    session: Session,
    user_id: str,
) -> ProactiveOutreachLog | None:
    return (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .order_by(
            ProactiveOutreachLog.created_at.desc(),
            ProactiveOutreachLog.id.desc(),
        )
        .first()
    )


def last_sent_outreach(
    session: Session,
    user_id: str,
) -> ProactiveOutreachLog | None:
    query = (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .filter(ProactiveOutreachLog.status.in_((
            "sent",
            "sent_after_ambiguous_replay",
        )))
    )
    clear_at = history_clear_at(session, user_id)
    if clear_at is not None:
        query = query.filter(ProactiveOutreachLog.created_at > clear_at)
    return query.order_by(
        ProactiveOutreachLog.created_at.desc(),
        ProactiveOutreachLog.id.desc(),
    ).first()


def last_effective_outreach(
    session: Session,
    user_id: str,
) -> ProactiveOutreachLog | None:
    return (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .filter(ProactiveOutreachLog.status.in_((
            "sending",
            "queued",
            "delivering",
            "retry_wait",
            "sent",
            "sent_after_ambiguous_replay",
        )))
        .order_by(
            ProactiveOutreachLog.created_at.desc(),
            ProactiveOutreachLog.id.desc(),
        )
        .first()
    )


def last_failed_forced_outreach(
    session: Session,
    user_id: str,
) -> ProactiveOutreachLog | None:
    return (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .filter(ProactiveOutreachLog.forced.is_(True))
        .filter(ProactiveOutreachLog.status.in_(("failed", "evaluation_error")))
        .order_by(
            ProactiveOutreachLog.created_at.desc(),
            ProactiveOutreachLog.id.desc(),
        )
        .first()
    )


def next_forced_attempt(session: Session, user_id: str) -> int:
    completed_attempts = (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .filter(ProactiveOutreachLog.forced.is_(True))
        .count()
    )
    return int(completed_attempts) + 1


def first_outreach_attempt(
    session: Session,
    user_id: str,
) -> ProactiveOutreachLog | None:
    return (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .filter(ProactiveOutreachLog.status != "cancelled")
        .order_by(
            ProactiveOutreachLog.created_at.asc(),
            ProactiveOutreachLog.id.asc(),
        )
        .first()
    )


def latest_next_check_at(session: Session, user_id: str) -> datetime | None:
    current_schedule = current_schedule_row(session, user_id)
    if current_schedule is not None and current_schedule.next_check_at is not None:
        return current_schedule.next_check_at
    row = (
        session.query(ProactiveOutreachLog.next_check_at)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .filter(ProactiveOutreachLog.status != "cancelled")
        .filter(ProactiveOutreachLog.next_check_at.isnot(None))
        .order_by(
            ProactiveOutreachLog.created_at.desc(),
            ProactiveOutreachLog.id.desc(),
        )
        .first()
    )
    return row[0] if row and row[0] is not None else None


def cancel_pre_clear_schedules(session: Session, user_id: str) -> int:
    clear_at = history_clear_at(session, user_id)
    if clear_at is None:
        return 0
    rows = (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .filter(ProactiveOutreachLog.status.in_((
            "pending",
            "candidate",
            "evaluation_error",
        )))
        .filter(ProactiveOutreachLog.created_at <= clear_at)
        .all()
    )
    for row in rows:
        row.status = "cancelled"
    if rows:
        session.commit()
    return len(rows)


def last_interaction_at(session: Session, user_id: str) -> datetime | None:
    row = (
        private_conversation_query(session, user_id, roles=("user",))
        .with_entities(ConversationTurn.created_at)
        .order_by(ConversationTurn.created_at.desc(), ConversationTurn.id.desc())
        .first()
    )
    return row[0] if row and row[0] is not None else None


__all__ = [
    "cancel_pre_clear_schedules",
    "cancelled_row_is_reusable",
    "current_pending_schedule",
    "current_schedule_row",
    "first_outreach_attempt",
    "history_clear_at",
    "last_effective_outreach",
    "last_failed_forced_outreach",
    "last_interaction_at",
    "last_sent_outreach",
    "latest_next_check_at",
    "latest_outreach_row",
    "next_forced_attempt",
    "private_conversation_query",
]
