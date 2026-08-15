"""周期系统自检 Watchdog；运行期开关热生效。"""

from __future__ import annotations

from collections.abc import Callable
import logging
import socket
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from core.selfcheck.engine import SelfcheckEngine, SelfcheckReport
from core.selfcheck.heartbeat import record_worker_cycle_with_factory
from core.settings_service import settings


logger = logging.getLogger("nanobot.selfcheck.watchdog")
WORKER_ID = "selfcheck-watchdog"
_INSTANCE_ID = (
    f"{socket.gethostname().strip() or 'host'}:{uuid4().hex}"
)[:128]


def _agent_runtime_registry() -> object | None:
    from core.agent_runtime.gateway import get_agent_runtime_registry

    try:
        return get_agent_runtime_registry()
    except RuntimeError:
        return None


def _endpoint_contracts() -> tuple[object, ...]:
    from api.admin.endpoint_registry import ADMIN_ENDPOINT_CONTRACT_REGISTRY

    return tuple(ADMIN_ENDPOINT_CONTRACT_REGISTRY.registry_snapshot)


def _composition_ready(app: object) -> bool:
    state = getattr(app, "state", None)
    root = getattr(state, "composition_root", None) if state is not None else None
    if root is None:
        return True
    value = getattr(getattr(root, "state", None), "value", "")
    return str(value or "") == "running"


def run_watchdog_once(
    *,
    app: object,
    session_factory: Callable[[], Session],
    engine_factory: Callable[..., SelfcheckEngine] = SelfcheckEngine,
) -> SelfcheckReport | None:
    """执行一次配置快照；关闭时不创建空 Run。"""

    if not settings.get_bool("selfcheck.watchdog_enabled", True):
        return None
    registry = _agent_runtime_registry()
    descriptors = tuple(registry.descriptors()) if registry is not None else ()
    db = session_factory()
    try:
        return engine_factory(
            app=app,
            db=db,
            testing=False,
            agent_descriptors=descriptors,
            agent_registry=registry,
            allow_model_checks=settings.get_bool(
                "selfcheck.model_canary_enabled",
                False,
            ),
            endpoint_contracts=_endpoint_contracts(),
        ).run(
            trigger="watchdog",
            requested_by=WORKER_ID,
        )
    finally:
        db.close()


def _record_cycle(
    session_factory: Callable[[], Session],
    *,
    success: bool,
    error_code: str = "",
    metadata: dict[str, object] | None = None,
) -> None:
    record_worker_cycle_with_factory(
        session_factory,
        worker_id=WORKER_ID,
        instance_id=_INSTANCE_ID,
        mode="embedded",
        success=success,
        error_code=error_code,
        metadata=metadata,
    )


def run_until_stopped(
    stop_event: Any,
    *,
    app: object,
    session_factory: Callable[[], Session] | None = None,
    initial_delay_seconds: float = 15.0,
) -> None:
    """周期执行；每轮重新读取设置，因此网页保存后无需重启。"""

    if session_factory is None:
        from core.db import SessionLocal

        session_factory = SessionLocal
    if stop_event.wait(max(0.0, float(initial_delay_seconds))):
        return
    while not stop_event.is_set():
        if not _composition_ready(app):
            _record_cycle(
                session_factory,
                success=True,
                metadata={"composition_ready": False},
            )
            if stop_event.wait(5.0):
                return
            continue
        enabled = settings.get_bool("selfcheck.watchdog_enabled", True)
        interval = settings.get_int(
            "selfcheck.watchdog_interval_seconds",
            900,
        )
        interval = max(60, min(86400, int(interval)))
        if not enabled:
            _record_cycle(
                session_factory,
                success=True,
                metadata={"enabled": False, "interval_seconds": interval},
            )
        else:
            try:
                report = run_watchdog_once(
                    app=app,
                    session_factory=session_factory,
                )
                _record_cycle(
                    session_factory,
                    success=True,
                    metadata={
                        "enabled": True,
                        "interval_seconds": interval,
                        "run_status": report.status if report is not None else "skipped",
                        "failed_checks": int(
                            (report.summary if report is not None else {}).get(
                                "failed",
                                0,
                            )
                        ),
                    },
                )
                if report is not None and report.status == "failed":
                    logger.error(
                        "Selfcheck watchdog detected failures run_id=%s failed=%s",
                        report.run_id,
                        report.summary.get("failed", 0),
                    )
            except Exception as exc:
                _record_cycle(
                    session_factory,
                    success=False,
                    error_code="selfcheck_watchdog_cycle_failed",
                    metadata={"enabled": True, "interval_seconds": interval},
                )
                logger.exception(
                    "Selfcheck watchdog cycle failed error_type=%s",
                    type(exc).__name__,
                )
        if stop_event.wait(interval):
            return


__all__ = ["WORKER_ID", "run_until_stopped", "run_watchdog_once"]
