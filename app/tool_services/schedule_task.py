"""在当前会话 owner 边界内管理定时推送任务的应用服务。"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from core.scheduled_task_contract import scheduled_task_program_schema
from core.tool_contracts.result import ToolServiceResult as ToolResult

logger = logging.getLogger("nanobot.tool.schedule_task")


def _utc_now_naive() -> datetime:
    from core.schedule_spec import utc_now_naive

    return utc_now_naive()


def _format_next_fire(value: datetime | None) -> str:
    if value is None:
        return "待排程"
    from core.schedule_spec import SHANGHAI

    localized = value.replace(tzinfo=timezone.utc).astimezone(SHANGHAI)
    return localized.strftime("%m-%d %H:%M")


def _schedule_display_for_row(task: Any) -> str:
    from core.schedule_spec import schedule_display, spec_from_fields

    spec = spec_from_fields(
        getattr(task, "schedule_kind", None),
        getattr(task, "schedule_spec", None),
        task.cron_expr,
    )
    if spec is None:
        return f"schedule={task.cron_expr}(无法解析)"
    return f"schedule={schedule_display(spec)}"


def _owned_task_query(db: Any, model: Any, owner_chat_stream_id: str) -> Any:
    """普通 Agent 工具的所有任务查询都固定在当前 owner。"""

    return db.query(model).filter(
        model.owner_chat_stream_id == owner_chat_stream_id,
        model.owner_migration_required == 0,
    )


def _program_tool_names(program: dict[str, Any]) -> set[str]:
    names: set[str] = set()

    def visit(steps: Any) -> None:
        for step in steps if isinstance(steps, list) else ():
            if not isinstance(step, dict):
                continue
            operation = str(step.get("op") or "")
            if operation == "tool":
                name = str(step.get("tool") or "").strip()
                if name:
                    names.add(name)
            elif operation == "branch":
                visit(step.get("then"))
                visit(step.get("else"))
            elif operation == "loop":
                visit(step.get("steps"))

    visit(program.get("steps"))
    return names


def _unavailable_program_tools(
    program: dict[str, Any],
) -> dict[str, str]:
    """按当前请求 ToolPlan 预检直接工具步骤；执行时仍会再次校验。"""

    from core.tool_plan import get_current_tool_plan

    plan = get_current_tool_plan()
    if plan is None:
        return {}
    return {
        name: plan.disabled_reason(name)
        for name in sorted(_program_tool_names(program))
        if not plan.can_execute(name)
    }


def _is_model_emit_program(program: dict[str, Any]) -> bool:
    steps = program.get("steps")
    return (
        isinstance(steps, list)
        and len(steps) == 2
        and [step.get("op") for step in steps if isinstance(step, dict)]
        == ["model", "emit"]
    )


def _latest_execution_payload(db: Any, task_id: int) -> dict[str, Any] | None:
    from core.database import ScheduledTaskExecution

    row = (
        db.query(ScheduledTaskExecution)
        .filter(ScheduledTaskExecution.task_id == int(task_id))
        .order_by(ScheduledTaskExecution.id.desc())
        .first()
    )
    if row is None:
        return None
    return {
        "execution_id": int(row.id),
        "status": str(row.status),
        "current_step_id": str(row.current_step_id or ""),
        "error_code": str(row.last_error_code or ""),
        "error_summary": str(row.last_error_summary or ""),
        "started_at": (
            row.started_at.isoformat() if row.started_at else None
        ),
        "finished_at": (
            row.finished_at.isoformat() if row.finished_at else None
        ),
    }


class ScheduleTaskService:
    """管理当前 owner 的定时推送任务。"""

    @property
    def tool_name(self) -> str:
        return "schedule_task"

    @property
    def description(self) -> str:
        return (
            "管理定时推送任务。支持创建、查看、修改、启停、立即执行和删除。"
            "判断标准是意图而非措辞：只要用户的意图涉及未来某时刻或持续跟进，"
            "就应主动创建，不必等用户说出'定时任务'。"
            "'提醒我/叫我/别忘了'或'X小时后再看看'=一次性任务；"
            "'每天/每周推送、总结某内容'=循环任务；"
            "'帮我关注XX/盯着XX/有进展告诉我'这类暗示=循环任务，"
            "自行选择合理频率(如每天上午)定期搜集推送，创建后说明频率即可。"
            "'看看定时任务'时列出，'停掉XX任务'时禁用，'现在执行XX任务'时立即运行。"
            "schedule 支持四种写法(按 Asia/Shanghai 解释): "
            "'30m'=30分钟后触发一次; 'every 2h'=每2小时循环; "
            "'0 9 * * *'=cron(分 时 日 月 周); "
            "'2026-08-01T15:00'=指定时刻触发一次。"
            "普通 Agent 只能管理和投递到当前 runtime_context 对应的私聊或群聊。"
        )

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "create(创建) | list(列出) | update(修改) | toggle(启停) | run(立即执行) | delete(删除)",
                    "enum": ["create", "list", "update", "toggle", "run", "delete"],
                },
                "task_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "任务ID（list 详情、update/toggle/run/delete 使用）",
                },
                "name": {"type": "string", "description": "任务名（create/update）"},
                "schedule": {
                    "type": "string",
                    "description": (
                        "触发规则（create/update），Asia/Shanghai 时区。"
                        "四种写法: '30m'=30分钟后一次; 'every 2h'=每2小时; "
                        "'0 9 * * *'=cron(分 时 日 月 周); "
                        "'2026-08-01T15:00'=指定时刻一次"
                    ),
                },
                "cron_expr": {
                    "type": "string",
                    "description": "旧参数，等价于 schedule 的 cron 写法（Asia/Shanghai 时区，分 时 日 月 周）",
                },
                "target_type": {
                    "type": "string",
                    "description": "兼容参数；必须与当前会话类型一致，不能跨会话投递",
                    "enum": ["private", "group"],
                },
                "target_id": {
                    "type": "string",
                    "description": "兼容参数；必须与当前会话目标一致，不能指定其他 QQ 或群",
                },
                "prompt_template": {
                    "type": "string",
                    "maxLength": 16000,
                    "description": "兼容写法：自动转换为 model→emit 程序；使用 program 时可省略",
                },
                "content": {
                    "type": "string",
                    "maxLength": 65536,
                    "description": "固定推送正文；编译为单个 emit，不调用模型",
                },
                "program": scheduled_task_program_schema(),
                "idempotency_key": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 512,
                    "description": "手动 run 必填；同一请求重试必须复用同一幂等键",
                },
            },
            "required": ["action"],
        }

    async def execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        del kwargs
        from core.database import ScheduledTask
        from core.agent_runtime.request_scope import (
            require_current_runtime_context,
        )
        from core.scheduled_task_contract import (
            ScheduledTaskContractError,
            apply_scheduled_task_owner,
            ensure_task_target_matches_owner,
            scheduled_task_owner_from_runtime_context,
            apply_scheduled_task_program,
            normalize_scheduled_task_definition,
        )
        from core.uow import UnitOfWork

        action = str(args.get("action", "create")).strip()
        task_id = args.get("task_id")
        if action not in {
            "create",
            "list",
            "update",
            "toggle",
            "run",
            "delete",
        }:
            return ToolResult(error=f"不支持的 action: {action}")

        try:
            try:
                owner = scheduled_task_owner_from_runtime_context(
                    require_current_runtime_context()
                )
            except (RuntimeError, ScheduledTaskContractError) as exc:
                return ToolResult(error=f"无法确认定时任务 owner: {exc}")
            uow = UnitOfWork()
            db = uow.open()
            try:
                if action == "list":
                    query = _owned_task_query(
                        db,
                        ScheduledTask,
                        owner.chat_stream_id,
                    )
                    if task_id is not None:
                        task = query.filter(
                            ScheduledTask.id == int(task_id)
                        ).first()
                        if task is None:
                            return ToolResult(
                                error=f"任务 {task_id} 不存在"
                            )
                        raw_program = str(task.program_json or "")
                        try:
                            if raw_program:
                                program = json.loads(raw_program)
                                if not isinstance(program, dict):
                                    raise ValueError("program 不是对象")
                            else:
                                program = (
                                    normalize_scheduled_task_definition(
                                        name=task.name,
                                        prompt_template=(
                                            task.prompt_template
                                        ),
                                    )[2]
                                )
                        except (
                            ScheduledTaskContractError,
                            TypeError,
                            ValueError,
                        ):
                            program = {
                                "error": "任务 program 无法解析",
                            }
                        payload = {
                            "id": int(task.id),
                            "name": str(task.name),
                            "enabled": bool(task.enabled),
                            "schedule": _schedule_display_for_row(task),
                            "next_fire_at": _format_next_fire(
                                task.next_fire_at
                            ),
                            "prompt_template": str(
                                task.prompt_template or ""
                            ),
                            "program": program,
                            "unavailable_tools": _unavailable_program_tools(
                                program
                            ),
                            "latest_execution": (
                                _latest_execution_payload(db, int(task.id))
                            ),
                        }
                        return ToolResult(
                            output=json.dumps(
                                payload,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                            exit_code=0,
                        )
                    tasks = query.all()
                    if not tasks:
                        return ToolResult(output="暂无定时任务", exit_code=0)
                    lines = []
                    for t in tasks:
                        s = "启用" if t.enabled else "禁用"
                        attempted = (
                            t.last_attempt_at.strftime("%m-%d %H:%M")
                            if t.last_attempt_at
                            else "从未"
                        )
                        succeeded = (
                            t.last_success_at.strftime("%m-%d %H:%M")
                            if t.last_success_at
                            else "从未"
                        )
                        latest_execution = _latest_execution_payload(
                            db,
                            int(t.id),
                        )
                        workflow = (
                            "从未"
                            if latest_execution is None
                            else str(latest_execution["status"])
                        )
                        workflow_error = (
                            ""
                            if latest_execution is None
                            else str(
                                latest_execution.get("error_code") or ""
                            )
                        )
                        lines.append(
                            f"[{t.id}] {s} {t.name} "
                            f"| {_schedule_display_for_row(t)} "
                            f"| 下次={_format_next_fire(t.next_fire_at)} "
                            f"| 最近尝试={attempted} | 最近成功={succeeded} "
                            f"| 投递状态={t.delivery_status} "
                            f"| 工作流={workflow}"
                            + (
                                f"({workflow_error})"
                                if workflow_error
                                else ""
                            )
                        )
                    return ToolResult(output="\n".join(lines), exit_code=0)

                if action == "run":
                    if not task_id:
                        return ToolResult(error="run 需要 task_id")
                    idempotency_key = str(args.get("idempotency_key") or "").strip()
                    if not idempotency_key:
                        return ToolResult(error="run 需要显式幂等键")
                    from core.scheduled_task_outbound import (
                        ScheduledTaskNotFoundError,
                    )
                    from core.scheduled_workflow import (
                        enqueue_scheduled_task_execution,
                    )
                    try:
                        result = enqueue_scheduled_task_execution(
                            db,
                            task_id=int(task_id),
                            trigger_type="manual",
                            manual_idempotency_key=idempotency_key,
                            expected_owner_chat_stream_id=(
                                owner.chat_stream_id
                            ),
                        )
                    except ScheduledTaskNotFoundError:
                        return ToolResult(error=f"任务 {task_id} 不存在")
                    db.commit()
                    return ToolResult(
                        output=(
                            "任务执行已入队 "
                            f"execution_id={result.execution_id} "
                            f"status={result.status} "
                            f"deduplicated={str(result.deduplicated).lower()}"
                        ),
                        exit_code=0,
                    )

                if action == "delete":
                    if not task_id:
                        return ToolResult(error="delete 需要 task_id")
                    t = _owned_task_query(
                        db,
                        ScheduledTask,
                        owner.chat_stream_id,
                    ).filter(ScheduledTask.id == task_id).first()
                    if not t:
                        return ToolResult(error=f"任务 {task_id} 不存在")
                    from core.scheduled_task_outbound import (
                        cancel_scheduled_task_deliveries,
                    )

                    cancellation = cancel_scheduled_task_deliveries(
                        db,
                        task=t,
                        reason_type="task_deleted",
                        safe_summary="任务已删除",
                    )
                    if cancellation.unsafe:
                        db.rollback()
                        return ToolResult(error="任务仍有投递中或结果不确定记录")
                    task_name = str(t.name)
                    db.delete(t)
                    db.commit()
                    return ToolResult(output=f"任务 {task_id} ({task_name}) 已删除", exit_code=0)

                if action == "toggle":
                    if not task_id:
                        return ToolResult(error="toggle 需要 task_id")
                    t = _owned_task_query(
                        db,
                        ScheduledTask,
                        owner.chat_stream_id,
                    ).filter(ScheduledTask.id == task_id).first()
                    if not t:
                        return ToolResult(error=f"任务 {task_id} 不存在")
                    from core.scheduled_task_outbound import (
                        cancel_scheduled_task_deliveries,
                    )

                    cancellation = cancel_scheduled_task_deliveries(
                        db,
                        task=t,
                        reason_type="task_toggled",
                        safe_summary="任务启停状态已修改",
                    )
                    if cancellation.unsafe:
                        db.rollback()
                        return ToolResult(error="任务仍有投递中或结果不确定记录")
                    t.enabled = 0 if t.enabled else 1
                    if t.enabled:
                        # 重新启用时清空预计算槽,由调度器按当前时间重排
                        t.next_fire_at = None
                    t.definition_version = int(t.definition_version or 0) + 1
                    t.updated_at = _utc_now_naive()
                    db.commit()
                    s = "启用" if t.enabled else "禁用"
                    return ToolResult(output=f"任务 {task_id} ({t.name}) 已{s}", exit_code=0)

                if action == "update":
                    if not task_id:
                        return ToolResult(error="update 需要 task_id")
                    t = _owned_task_query(
                        db,
                        ScheduledTask,
                        owner.chat_stream_id,
                    ).filter(ScheduledTask.id == task_id).first()
                    if not t:
                        return ToolResult(error=f"任务 {task_id} 不存在")
                    try:
                        ensure_task_target_matches_owner(
                            owner,
                            target_type=args.get("target_type"),
                            target_id=args.get("target_id"),
                        )
                        proposed_name = (
                            args.get("name")
                            if args.get("name") is not None
                            else t.name
                        )
                        proposed_prompt = (
                            args.get("prompt_template")
                            if args.get("prompt_template") is not None
                            else t.prompt_template
                        )
                        definition_fields = [
                            key
                            for key in (
                                "program",
                                "content",
                                "prompt_template",
                            )
                            if args.get(key) is not None
                        ]
                        if len(definition_fields) > 1:
                            return ToolResult(
                                error=(
                                    "program、content 和 prompt_template "
                                    "只能修改一个"
                                )
                            )
                        existing_program_json = str(
                            t.program_json or ""
                        )
                        existing_program = (
                            json.loads(existing_program_json)
                            if existing_program_json
                            else None
                        )
                        proposed_content = None
                        if args.get("program") is not None:
                            proposed_program = args.get("program")
                            proposed_prompt = ""
                        elif args.get("content") is not None:
                            proposed_program = None
                            proposed_prompt = ""
                            proposed_content = args.get("content")
                        elif args.get("prompt_template") is not None:
                            if (
                                isinstance(existing_program, dict)
                                and not _is_model_emit_program(
                                    existing_program
                                )
                            ):
                                return ToolResult(
                                    error=(
                                        "当前任务是确定性 program；不能仅用 "
                                        "prompt_template 覆盖，请提交完整 "
                                        "program 或固定 content"
                                    )
                                )
                            proposed_program = None
                        else:
                            proposed_program = existing_program
                        (
                            normalized_name,
                            normalized_prompt,
                            normalized_program,
                            _program_json,
                            _program_sha256,
                        ) = normalize_scheduled_task_definition(
                            name=proposed_name,
                            prompt_template=proposed_prompt,
                            program=proposed_program,
                            content=proposed_content,
                        )
                        unavailable = _unavailable_program_tools(
                            normalized_program
                        )
                        if definition_fields and unavailable:
                            detail = "；".join(
                                f"{name}: {reason}"
                                for name, reason in unavailable.items()
                            )
                            return ToolResult(
                                error=f"任务所需工具当前不可用：{detail}"
                            )
                    except ScheduledTaskContractError as exc:
                        return ToolResult(error=str(exc))
                    except (TypeError, ValueError):
                        return ToolResult(error="现有任务 program 无法解析")
                    from core.scheduled_task_outbound import (
                        cancel_scheduled_task_deliveries,
                    )

                    cancellation = cancel_scheduled_task_deliveries(
                        db,
                        task=t,
                        reason_type="task_updated",
                        safe_summary="任务定义已修改",
                    )
                    if cancellation.unsafe:
                        db.rollback()
                        return ToolResult(error="任务仍有投递中或结果不确定记录")
                    apply_scheduled_task_program(
                        t,
                        name=normalized_name,
                        prompt_template=normalized_prompt,
                        program=normalized_program,
                    )
                    t.target_type = owner.target_type
                    t.target_id = owner.target_id
                    schedule_text = str(
                        args.get("schedule") or args.get("cron_expr") or ""
                    ).strip()
                    if schedule_text:
                        from core.schedule_spec import (
                            ScheduleSpecError,
                            resolve_schedule_fields,
                        )

                        try:
                            fields = resolve_schedule_fields(
                                schedule=schedule_text,
                                cron_expr=None,
                                now_utc=_utc_now_naive(),
                            )
                        except ScheduleSpecError as spec_exc:
                            db.rollback()
                            return ToolResult(error=f"schedule 无效: {spec_exc}")
                        t.schedule_kind = fields.schedule_kind
                        t.schedule_spec = fields.schedule_spec
                        t.cron_expr = fields.cron_expr
                        t.next_fire_at = fields.next_fire_at
                    t.definition_version = int(t.definition_version or 0) + 1
                    t.updated_at = _utc_now_naive()
                    db.commit()
                    return ToolResult(output=f"任务 {task_id} ({t.name}) 已更新", exit_code=0)

                # create
                try:
                    ensure_task_target_matches_owner(
                        owner,
                        target_type=args.get("target_type"),
                        target_id=args.get("target_id"),
                    )
                    definition_fields = [
                        key
                        for key in (
                            "program",
                            "content",
                            "prompt_template",
                        )
                        if args.get(key) is not None
                    ]
                    if len(definition_fields) > 1:
                        return ToolResult(
                            error=(
                                "program、content 和 prompt_template "
                                "只能填写一个"
                            )
                        )
                    (
                        name,
                        prompt,
                        normalized_program,
                        _program_json,
                        _program_sha256,
                    ) = normalize_scheduled_task_definition(
                        name=args.get("name"),
                        prompt_template=args.get("prompt_template"),
                        program=args.get("program"),
                        content=args.get("content"),
                    )
                    unavailable = _unavailable_program_tools(
                        normalized_program
                    )
                    if unavailable:
                        detail = "；".join(
                            f"{name}: {reason}"
                            for name, reason in unavailable.items()
                        )
                        return ToolResult(
                            error=f"任务所需工具当前不可用：{detail}"
                        )
                except ScheduledTaskContractError as exc:
                    return ToolResult(error=str(exc))
                schedule_text = str(
                    args.get("schedule") or args.get("cron_expr") or ""
                ).strip()
                if not schedule_text:
                    return ToolResult(
                        error="create 需要 schedule(或 cron_expr)"
                    )
                from core.schedule_spec import (
                    ScheduleSpecError,
                    resolve_schedule_fields,
                )

                try:
                    fields = resolve_schedule_fields(
                        schedule=schedule_text,
                        cron_expr=None,
                        now_utc=_utc_now_naive(),
                    )
                except ScheduleSpecError as spec_exc:
                    return ToolResult(error=f"schedule 无效: {spec_exc}")
                t = ScheduledTask(
                    cron_expr=fields.cron_expr,
                    schedule_kind=fields.schedule_kind,
                    schedule_spec=fields.schedule_spec,
                    next_fire_at=fields.next_fire_at,
                    target_type=owner.target_type,
                    target_id=owner.target_id,
                    definition_version=1,
                    updated_at=_utc_now_naive(),
                )
                apply_scheduled_task_program(
                    t,
                    name=name,
                    prompt_template=prompt,
                    program=normalized_program,
                )
                apply_scheduled_task_owner(t, owner)
                db.add(t)
                db.commit()
                logger.info(
                    "[schedule_task] Created id=%s schedule=%s",
                    t.id,
                    fields.display,
                )
                return ToolResult(
                    output=(
                        f"已创建 (id={t.id}): {name} | {fields.display} "
                        f"| 下次触发 {_format_next_fire(fields.next_fire_at)}"
                    ),
                    exit_code=0,
                )
            finally:
                uow.close()
        except Exception as exc:
            logger.error(
                "[schedule_task] Failed error_type=%s",
                type(exc).__name__,
            )
            return ToolResult(error="定时任务操作失败")
