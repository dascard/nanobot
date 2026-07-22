"""主动外呼进程配置、时钟与兼容 transport 规范化。"""

from __future__ import annotations

import math
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from core.database import SessionLocal


OUTREACH_LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


@contextmanager
def session_scope(db: Session | None = None) -> Iterator[Session]:
    """复用调用方事务，或为后台入口创建并可靠关闭短生命周期 Session。"""

    if db is not None:
        yield db
        return

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _positive_env_number(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是有限正数") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} 必须是有限正数")
    return value


def _positive_env_integer(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是正整数") from exc
    if value <= 0:
        raise ValueError(f"{name} 必须是正整数")
    return value


def _outreach_endpoint_revision() -> str:
    revision = os.getenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", "1").strip()
    if not revision or len(revision) > 128:
        raise ValueError("NANOBOT_QQ_PUSH_CONFIG_REVISION 必须是非空短文本")
    return revision


def _outreach_session_factory(session: Session):
    return sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=session.get_bind(),
    )


def _utc_naive(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current
    return current.astimezone(timezone.utc).replace(tzinfo=None)


def _outbound_occurrence_utc(value: datetime | None = None) -> datetime:
    """把主动外呼的上海本地业务时间转换为 Outbound 的 UTC naive 时间。"""

    current = value or datetime.now(OUTREACH_LOCAL_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=OUTREACH_LOCAL_TIMEZONE)
    return current.astimezone(timezone.utc).replace(tzinfo=None)


def _legacy_delivery_outcome(value: object):
    from core.outbound_transport import DeliveryOutcome

    if value is True:
        return DeliveryOutcome(
            category="success",
            error_type="",
            status_code=200,
            retry_after_seconds=None,
            duration_ms=0,
            safe_summary="投递成功",
            transport_phase="response_received",
        )
    if value is False:
        return DeliveryOutcome(
            category="payload",
            error_type="delivery_rejected",
            status_code=400,
            retry_after_seconds=None,
            duration_ms=0,
            safe_summary="远端拒绝本次投递",
            transport_phase="response_received",
        )
    return DeliveryOutcome(
        category="ambiguous",
        error_type="publish_outcome_unknown",
        status_code=None,
        retry_after_seconds=None,
        duration_ms=0,
        safe_summary="远端投递结果未知",
        transport_phase="write",
    )



__all__ = ["OUTREACH_LOCAL_TIMEZONE"]
