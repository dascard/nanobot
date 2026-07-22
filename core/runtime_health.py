"""主服务 liveness/readiness 状态与本地依赖探针。"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import text

from core.database import run_session_phase


@dataclass(frozen=True)
class RuntimeReadinessState:
    startup_complete: bool = False
    prompt_runtime_ready: bool = False
    testing: bool = False
    shutting_down: bool = False


_state_lock = threading.Lock()
_state = RuntimeReadinessState()


def mark_starting(*, testing: bool) -> None:
    global _state
    with _state_lock:
        _state = RuntimeReadinessState(testing=bool(testing))


def mark_prompt_runtime_ready() -> None:
    global _state
    with _state_lock:
        _state = replace(_state, prompt_runtime_ready=True)


def mark_startup_complete() -> None:
    global _state
    with _state_lock:
        _state = replace(_state, startup_complete=True, shutting_down=False)


def mark_stopping() -> None:
    global _state
    with _state_lock:
        _state = replace(_state, startup_complete=False, shutting_down=True)


def readiness_snapshot() -> dict[str, Any]:
    with _state_lock:
        state = _state

    checks: dict[str, bool] = {
        "startup_complete": state.startup_complete and not state.shutting_down,
        "database": _database_ready(),
        "prompt_runtime": state.prompt_runtime_ready,
    }
    bridge_state = "skipped_testing"
    if not state.testing:
        try:
            from nanobot_kt.bridge import (
                BridgeLifecycleState,
                get_bridge_lifecycle_state,
            )

            current = get_bridge_lifecycle_state()
            bridge_state = current.value
            checks["bridge"] = current is BridgeLifecycleState.RUNNING
        except Exception:
            bridge_state = "unavailable"
            checks["bridge"] = False
    else:
        checks["bridge"] = True

    blockers = [name for name, ok in checks.items() if not ok]
    return {
        "status": "ready" if not blockers else "not_ready",
        "ready": not blockers,
        "checks": checks,
        "bridge_state": bridge_state,
        "blocking_reasons": blockers,
    }


def _database_ready() -> bool:
    try:
        return bool(
            run_session_phase(
                lambda db: int(db.execute(text("SELECT 1")).scalar_one()) == 1
            )
        )
    except Exception:
        return False


def database_healthcheck_main() -> int:
    """供无 HTTP 端点的 worker 容器执行本地数据库健康检查。"""

    return 0 if _database_ready() else 1


if __name__ == "__main__":
    raise SystemExit(database_healthcheck_main())
