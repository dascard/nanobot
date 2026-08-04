"""Session Plan 专用读写工具服务。"""

from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any

from core.agent_runtime.request_scope import require_current_runtime_context
from core.session_goal import (
    SessionGoalConflictError,
    SessionGoalError,
    SessionGoalPrincipal,
    SessionGoalService,
    SessionGoalStatus,
)
from core.tool_contracts.result import ToolServiceResult
from core.uow import UnitOfWork


def _bound_goal_context() -> tuple[dict[str, Any], SessionGoalPrincipal, str]:
    context = require_current_runtime_context()
    goal_id = str(context.get("session_goal_id") or "").strip()
    if not goal_id:
        raise SessionGoalConflictError("当前请求没有绑定 Session Goal")
    principal = SessionGoalPrincipal(
        str(context.get("platform") or ""),
        str(context.get("owner_type") or ""),
        str(context.get("owner_id") or ""),
        str(context.get("session_id") or ""),
    )
    return context, principal, goal_id


def _result(payload: dict[str, object]) -> ToolServiceResult:
    return ToolServiceResult(
        output=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        exit_code=0,
        metadata={"structured_content": payload},
    )


async def execute_session_plan_read(args: dict[str, Any]) -> ToolServiceResult:
    try:
        _context, principal, goal_id = _bound_goal_context()
        raw_revision = args.get("revision", 0)
        if isinstance(raw_revision, bool) or not isinstance(raw_revision, int):
            return ToolServiceResult(error="revision 必须是非负整数")
        with UnitOfWork() as uow:
            if uow.db is None:
                return ToolServiceResult(error="数据库会话不可用")
            service = SessionGoalService(uow.db)
            snapshot = service.get_goal(goal_id, principal)
            revision = raw_revision or (
                snapshot.approved_plan_revision
                if snapshot.approved_plan_revision > 0
                else snapshot.latest_plan_revision
            )
            if (
                snapshot.status
                in {SessionGoalStatus.APPROVED, SessionGoalStatus.EXECUTING}
                and revision != snapshot.approved_plan_revision
            ):
                return ToolServiceResult(
                    error="批准后只能读取已批准的 Session Plan 版本"
                )
            plan = service.get_plan(
                goal_id,
                principal,
                revision=revision,
            )
            payload: dict[str, object] = {
                "goal_id": goal_id,
                "goal_version": snapshot.version,
                "status": snapshot.status.value,
                "mode": snapshot.mode.value,
                "plan": None,
            }
            if plan is not None:
                payload["plan"] = {
                    **asdict(plan),
                    "created_at": plan.created_at.isoformat(),
                }
            return _result(payload)
    except SessionGoalError as exc:
        return ToolServiceResult(error=str(exc))


async def execute_session_plan_write(args: dict[str, Any]) -> ToolServiceResult:
    try:
        context, principal, goal_id = _bound_goal_context()
        content = args.get("content")
        expected_version = args.get("expected_version")
        if not isinstance(content, str):
            return ToolServiceResult(error="content 必须是字符串")
        if isinstance(expected_version, bool) or not isinstance(
            expected_version,
            int,
        ):
            return ToolServiceResult(error="expected_version 必须是正整数")
        with UnitOfWork() as uow:
            if uow.db is None:
                return ToolServiceResult(error="数据库会话不可用")
            service = SessionGoalService(uow.db)
            snapshot = service.write_plan(
                goal_id=goal_id,
                principal=principal,
                content=content,
                expected_version=expected_version,
                actor_id=str(context.get("actor_id") or principal.owner_id),
                source_run_id=str(context.get("run_id") or ""),
            )
            uow.commit()
            return _result({
                "goal_id": goal_id,
                "goal_version": snapshot.version,
                "status": snapshot.status.value,
                "mode": snapshot.mode.value,
                "plan_revision": snapshot.latest_plan_revision,
                "plan_sha256": snapshot.latest_plan_sha256,
            })
    except SessionGoalError as exc:
        return ToolServiceResult(error=str(exc))


__all__ = [
    "execute_session_plan_read",
    "execute_session_plan_write",
]
