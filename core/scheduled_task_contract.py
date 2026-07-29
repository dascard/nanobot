"""定时任务定义、归属和执行身份的统一合同。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.chat_stream_identity import (
    ChatStreamIdentityError,
    parse_canonical_chat_stream_id,
    resolve_chat_stream_identity,
)


SCHEDULED_TASK_OWNER_PLATFORM = "qq"
SCHEDULED_TASK_TIMEZONE = "Asia/Shanghai"
MAX_SCHEDULED_TASK_NAME_CHARS = 120
MAX_SCHEDULED_TASK_PROMPT_CHARS = 16_000
MAX_SCHEDULED_TASK_PROGRAM_BYTES = 64 * 1024
MAX_SCHEDULED_TASK_STEPS = 100
MAX_SCHEDULED_TASK_LOOP_ITERATIONS = 100
MAX_SCHEDULED_TASK_DURATION_SECONDS = 600
MAX_SCHEDULED_TASK_WAIT_SECONDS = 86_400
SCHEDULED_TASK_PROGRAM_VERSION = 1
_STEP_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_PROGRAM_OPERATIONS = frozenset(
    {"set", "tool", "model", "branch", "loop", "wait", "emit"}
)
_PROGRAM_FORBIDDEN_TOOLS = frozenset(
    {"schedule_task", "reply", "no_reply", "skill"}
)
_EXPRESSION_OPERATORS = frozenset(
    {
        "$ref",
        "$eq",
        "$ne",
        "$lt",
        "$lte",
        "$gt",
        "$gte",
        "$and",
        "$or",
        "$not",
        "$exists",
        "$concat",
        "$coalesce",
    }
)


class ScheduledTaskContractError(ValueError):
    """定时任务定义或身份不满足安全合同。"""


@dataclass(frozen=True, slots=True)
class ScheduledTaskOwner:
    """一个可持久化、可重建真实运行时主体的任务 owner。"""

    chat_stream_id: str
    platform: str
    chat_type: str
    session_id: str
    external_session_id: str
    created_by_actor_id: str

    @property
    def target_type(self) -> str:
        return self.chat_type

    @property
    def target_id(self) -> str:
        return self.external_session_id


def _normalized_actor_id(value: object) -> str:
    actor_id = str(value or "").strip()
    if len(actor_id) > 255:
        raise ScheduledTaskContractError("任务创建者标识不能超过 255 个字符")
    return actor_id


def scheduled_task_owner_from_target(
    *,
    target_type: object,
    target_id: object,
    platform: object = SCHEDULED_TASK_OWNER_PLATFORM,
    created_by_actor_id: object = "",
) -> ScheduledTaskOwner:
    """从受信投递目标构造任务 owner；不猜测缺失的会话类型。"""

    normalized_type = str(target_type or "").strip().lower()
    normalized_id = str(target_id or "").strip()
    normalized_platform = str(platform or "").strip().lower()
    try:
        identity = resolve_chat_stream_identity(
            platform=normalized_platform,
            chat_type=normalized_type,
            session_id=normalized_id,
        )
    except ChatStreamIdentityError as exc:
        raise ScheduledTaskContractError(f"定时任务 owner 无效: {exc}") from exc
    return ScheduledTaskOwner(
        chat_stream_id=identity.chat_stream_id,
        platform=identity.platform,
        chat_type=identity.chat_type,
        session_id=identity.legacy_runtime_session_id,
        external_session_id=identity.external_session_id,
        created_by_actor_id=_normalized_actor_id(created_by_actor_id),
    )


def scheduled_task_owner_from_runtime_context(
    context: Mapping[str, Any],
) -> ScheduledTaskOwner:
    """只从受信请求上下文解析当前会话 owner 和审计 actor。"""

    chat_type = (
        "group"
        if bool(context.get("is_group"))
        or str(context.get("chat_type") or "").strip().lower() == "group"
        else "private"
    )
    platform = str(
        context.get("platform") or SCHEDULED_TASK_OWNER_PLATFORM
    ).strip().lower()
    session_id = str(context.get("session_id") or "").strip()
    if not session_id:
        if chat_type == "group":
            session_id = str(context.get("group_id") or "").strip()
        else:
            session_id = str(context.get("user_id") or "").strip()
    actor_id = str(context.get("user_id") or "").strip()
    owner = scheduled_task_owner_from_target(
        target_type=chat_type,
        target_id=session_id,
        platform=platform,
        created_by_actor_id=actor_id,
    )
    if chat_type == "group":
        runtime_group_id = str(context.get("group_id") or "").strip()
        if (
            runtime_group_id
            and runtime_group_id != owner.external_session_id
        ):
            raise ScheduledTaskContractError(
                "请求上下文中的 group_id 与 session_id 不一致"
            )
    return owner


def scheduled_task_owner_from_persisted(
    *,
    chat_stream_id: object,
    platform: object,
    chat_type: object,
    session_id: object,
    created_by_actor_id: object = "",
) -> ScheduledTaskOwner:
    """严格校验持久化 owner 快照，拒绝字段之间不一致。"""

    raw_stream_id = str(chat_stream_id or "").strip()
    try:
        identity = parse_canonical_chat_stream_id(raw_stream_id)
    except ChatStreamIdentityError as exc:
        raise ScheduledTaskContractError(
            f"定时任务持久化 owner 无效: {exc}"
        ) from exc
    normalized_platform = str(platform or "").strip().lower()
    normalized_chat_type = str(chat_type or "").strip().lower()
    normalized_session_id = str(session_id or "").strip()
    if (
        normalized_platform != identity.platform
        or normalized_chat_type != identity.chat_type
        or normalized_session_id != identity.legacy_runtime_session_id
    ):
        raise ScheduledTaskContractError("定时任务持久化 owner 字段不一致")
    return ScheduledTaskOwner(
        chat_stream_id=identity.chat_stream_id,
        platform=identity.platform,
        chat_type=identity.chat_type,
        session_id=identity.legacy_runtime_session_id,
        external_session_id=identity.external_session_id,
        created_by_actor_id=_normalized_actor_id(created_by_actor_id),
    )


def apply_scheduled_task_owner(task: Any, owner: ScheduledTaskOwner) -> None:
    """把唯一 owner 投影写入 ORM 任务。"""

    task.owner_chat_stream_id = owner.chat_stream_id
    task.owner_platform = owner.platform
    task.owner_chat_type = owner.chat_type
    task.owner_session_id = owner.session_id
    task.created_by_actor_id = owner.created_by_actor_id
    task.owner_migration_required = 0


def validate_scheduled_task_definition(
    *,
    name: object,
    prompt_template: object,
) -> tuple[str, str]:
    """统一校验 API、工具和执行器使用的任务文本上限。"""

    normalized_name = str(name or "").strip()
    normalized_prompt = str(prompt_template or "").strip()
    if not normalized_name:
        raise ScheduledTaskContractError("任务名称不能为空")
    if len(normalized_name) > MAX_SCHEDULED_TASK_NAME_CHARS:
        raise ScheduledTaskContractError(
            f"任务名称不能超过 {MAX_SCHEDULED_TASK_NAME_CHARS} 个字符"
        )
    if not normalized_prompt:
        raise ScheduledTaskContractError("任务提示模板不能为空")
    if len(normalized_prompt) > MAX_SCHEDULED_TASK_PROMPT_CHARS:
        raise ScheduledTaskContractError(
            "任务提示模板不能超过 "
            f"{MAX_SCHEDULED_TASK_PROMPT_CHARS} 个 Unicode 字符"
        )
    if len(normalized_prompt.encode("utf-8")) > MAX_SCHEDULED_TASK_PROGRAM_BYTES:
        raise ScheduledTaskContractError(
            "任务定义 UTF-8 大小不能超过 "
            f"{MAX_SCHEDULED_TASK_PROGRAM_BYTES} 字节"
        )
    return normalized_name, normalized_prompt


def validate_scheduled_task_name(name: object) -> str:
    """校验不依赖旧 ``prompt_template`` 的任务名称。"""

    normalized_name = str(name or "").strip()
    if not normalized_name:
        raise ScheduledTaskContractError("任务名称不能为空")
    if len(normalized_name) > MAX_SCHEDULED_TASK_NAME_CHARS:
        raise ScheduledTaskContractError(
            f"任务名称不能超过 {MAX_SCHEDULED_TASK_NAME_CHARS} 个字符"
        )
    return normalized_name


def _canonical_program_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ScheduledTaskContractError(
            "任务 program 必须是可序列化的有限 JSON"
        ) from exc


def _validate_variable_name(value: object, *, field_name: str) -> str:
    name = str(value or "").strip()
    if not _VARIABLE_NAME_RE.fullmatch(name):
        raise ScheduledTaskContractError(
            f"{field_name} 必须是 1-64 字符的安全变量名"
        )
    return name


def _validate_expression(value: Any, *, depth: int = 0) -> Any:
    """校验受限 JSON 表达式；绝不解释 Python、SQL 或模板代码。"""

    if depth > 20:
        raise ScheduledTaskContractError("任务表达式嵌套不能超过 20 层")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        # canonical JSON 的 allow_nan=False 会拒绝 NaN/Infinity。
        _canonical_program_json({"value": value})
        return value
    if isinstance(value, list):
        return [
            _validate_expression(item, depth=depth + 1)
            for item in value
        ]
    if not isinstance(value, Mapping):
        raise ScheduledTaskContractError("任务表达式只支持 JSON 值")

    operator_keys = [
        str(key)
        for key in value
        if str(key).startswith("$")
    ]
    if not operator_keys:
        return {
            str(key): _validate_expression(item, depth=depth + 1)
            for key, item in value.items()
        }
    if len(value) != 1 or len(operator_keys) != 1:
        raise ScheduledTaskContractError(
            "表达式运算对象必须且只能包含一个 $ 运算符"
        )
    operator = operator_keys[0]
    if operator not in _EXPRESSION_OPERATORS:
        raise ScheduledTaskContractError(f"不支持的表达式运算符: {operator}")
    operand = value[operator]
    if operator == "$ref":
        reference = str(operand or "").strip()
        if (
            not reference
            or len(reference) > 255
            or any(
                not _VARIABLE_NAME_RE.fullmatch(part)
                for part in reference.split(".")
            )
        ):
            raise ScheduledTaskContractError("$ref 必须是安全的点分路径")
        return {operator: reference}
    if operator in {"$and", "$or", "$concat", "$coalesce"}:
        if not isinstance(operand, list) or not operand:
            raise ScheduledTaskContractError(
                f"{operator} 必须接收非空数组"
            )
        return {
            operator: [
                _validate_expression(item, depth=depth + 1)
                for item in operand
            ]
        }
    if operator in {"$eq", "$ne", "$lt", "$lte", "$gt", "$gte"}:
        if not isinstance(operand, list) or len(operand) != 2:
            raise ScheduledTaskContractError(
                f"{operator} 必须接收两个参数"
            )
        return {
            operator: [
                _validate_expression(item, depth=depth + 1)
                for item in operand
            ]
        }
    return {
        operator: _validate_expression(operand, depth=depth + 1)
    }


def _normalize_program_steps(
    raw_steps: object,
    *,
    seen_ids: set[str],
    counter: list[int],
    root_loop_limit: int,
) -> list[dict[str, Any]]:
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ScheduledTaskContractError("任务 program.steps 必须是非空数组")
    normalized_steps: list[dict[str, Any]] = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, Mapping):
            raise ScheduledTaskContractError("每个任务步骤必须是对象")
        step_id = str(raw_step.get("id") or "").strip()
        if not _STEP_ID_RE.fullmatch(step_id):
            raise ScheduledTaskContractError(
                "步骤 id 必须以英文字母开头，只含字母、数字、_、-，最长 64"
            )
        if step_id in seen_ids:
            raise ScheduledTaskContractError(f"步骤 id 重复: {step_id}")
        seen_ids.add(step_id)
        counter[0] += 1
        if counter[0] > MAX_SCHEDULED_TASK_STEPS:
            raise ScheduledTaskContractError(
                f"任务静态步骤不能超过 {MAX_SCHEDULED_TASK_STEPS} 个"
            )
        operation = str(raw_step.get("op") or "").strip().lower()
        if operation not in _PROGRAM_OPERATIONS:
            raise ScheduledTaskContractError(
                f"步骤 {step_id} 的 op 不受支持"
            )
        step: dict[str, Any] = {"id": step_id, "op": operation}

        if operation == "set":
            step["name"] = _validate_variable_name(
                raw_step.get("name"),
                field_name=f"步骤 {step_id}.name",
            )
            if "value" not in raw_step:
                raise ScheduledTaskContractError(
                    f"步骤 {step_id} 缺少 value"
                )
            step["value"] = _validate_expression(raw_step["value"])
        elif operation == "tool":
            tool_name = str(raw_step.get("tool") or "").strip()
            if not tool_name or len(tool_name) > 128:
                raise ScheduledTaskContractError(
                    f"步骤 {step_id}.tool 无效"
                )
            if tool_name in _PROGRAM_FORBIDDEN_TOOLS:
                raise ScheduledTaskContractError(
                    f"任务程序不能直接调用 {tool_name}"
                )
            args = raw_step.get("args", {})
            if not isinstance(args, Mapping):
                raise ScheduledTaskContractError(
                    f"步骤 {step_id}.args 必须是对象"
                )
            step["tool"] = tool_name
            step["args"] = _validate_expression(dict(args))
            if raw_step.get("save_as") is not None:
                step["save_as"] = _validate_variable_name(
                    raw_step.get("save_as"),
                    field_name=f"步骤 {step_id}.save_as",
                )
            recovery = str(
                raw_step.get("recovery") or "ambiguous"
            ).strip().lower()
            if recovery not in {"safe_retry", "ambiguous"}:
                raise ScheduledTaskContractError(
                    f"步骤 {step_id}.recovery 只支持 safe_retry/ambiguous"
                )
            step["recovery"] = recovery
            max_attempts = raw_step.get("max_attempts", 1)
            if (
                type(max_attempts) is not int
                or max_attempts < 1
                or max_attempts > 3
            ):
                raise ScheduledTaskContractError(
                    f"步骤 {step_id}.max_attempts 必须是 1-3"
                )
            step["max_attempts"] = max_attempts
            if raw_step.get("idempotency_arg") is not None:
                step["idempotency_arg"] = _validate_variable_name(
                    raw_step.get("idempotency_arg"),
                    field_name=f"步骤 {step_id}.idempotency_arg",
                )
        elif operation == "model":
            if "prompt" not in raw_step:
                raise ScheduledTaskContractError(
                    f"步骤 {step_id} 缺少 prompt"
                )
            prompt = _validate_expression(raw_step["prompt"])
            if (
                isinstance(prompt, str)
                and len(prompt) > MAX_SCHEDULED_TASK_PROMPT_CHARS
            ):
                raise ScheduledTaskContractError(
                    f"步骤 {step_id}.prompt 不能超过 "
                    f"{MAX_SCHEDULED_TASK_PROMPT_CHARS} 个字符"
                )
            step["prompt"] = prompt
            step["save_as"] = _validate_variable_name(
                raw_step.get("save_as") or f"{step_id}_output",
                field_name=f"步骤 {step_id}.save_as",
            )
            max_attempts = raw_step.get("max_attempts", 2)
            if (
                type(max_attempts) is not int
                or max_attempts < 1
                or max_attempts > 3
            ):
                raise ScheduledTaskContractError(
                    f"步骤 {step_id}.max_attempts 必须是 1-3"
                )
            step["max_attempts"] = max_attempts
        elif operation == "branch":
            if "condition" not in raw_step:
                raise ScheduledTaskContractError(
                    f"步骤 {step_id} 缺少 condition"
                )
            step["condition"] = _validate_expression(
                raw_step["condition"]
            )
            step["then"] = _normalize_program_steps(
                raw_step.get("then"),
                seen_ids=seen_ids,
                counter=counter,
                root_loop_limit=root_loop_limit,
            )
            raw_else = raw_step.get("else", [])
            if raw_else:
                step["else"] = _normalize_program_steps(
                    raw_else,
                    seen_ids=seen_ids,
                    counter=counter,
                    root_loop_limit=root_loop_limit,
                )
            else:
                step["else"] = []
        elif operation == "loop":
            if "items" not in raw_step:
                raise ScheduledTaskContractError(
                    f"步骤 {step_id} 缺少 items"
                )
            step["items"] = _validate_expression(raw_step["items"])
            step["item"] = _validate_variable_name(
                raw_step.get("item") or "item",
                field_name=f"步骤 {step_id}.item",
            )
            step["index"] = _validate_variable_name(
                raw_step.get("index") or "index",
                field_name=f"步骤 {step_id}.index",
            )
            max_iterations = raw_step.get(
                "max_iterations",
                root_loop_limit,
            )
            if (
                type(max_iterations) is not int
                or max_iterations < 1
                or max_iterations > root_loop_limit
            ):
                raise ScheduledTaskContractError(
                    f"步骤 {step_id}.max_iterations 必须是 1-{root_loop_limit}"
                )
            step["max_iterations"] = max_iterations
            step["steps"] = _normalize_program_steps(
                raw_step.get("steps"),
                seen_ids=seen_ids,
                counter=counter,
                root_loop_limit=root_loop_limit,
            )
        elif operation == "wait":
            if "seconds" not in raw_step:
                raise ScheduledTaskContractError(
                    f"步骤 {step_id} 缺少 seconds"
                )
            seconds = _validate_expression(raw_step["seconds"])
            if isinstance(seconds, (int, float)) and not isinstance(
                seconds, bool
            ):
                if seconds < 0 or seconds > MAX_SCHEDULED_TASK_WAIT_SECONDS:
                    raise ScheduledTaskContractError(
                        f"步骤 {step_id}.seconds 必须在 0-"
                        f"{MAX_SCHEDULED_TASK_WAIT_SECONDS} 之间"
                    )
            step["seconds"] = seconds
        else:
            content = raw_step.get("content")
            if content is None and raw_step.get("from") is not None:
                content = {"$ref": str(raw_step.get("from") or "")}
            if content is None:
                raise ScheduledTaskContractError(
                    f"步骤 {step_id} 缺少 content"
                )
            step["content"] = _validate_expression(content)

        normalized_steps.append(step)
    return normalized_steps


def _validate_program_references(
    steps: list[dict[str, Any]],
) -> None:
    """校验所有 ``$ref`` 至少指向已声明变量或程序内步骤输出。"""

    step_ids: set[str] = set()
    variable_names: set[str] = set()
    references: list[str] = []

    def collect_expression(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                collect_expression(item)
            return
        if not isinstance(value, Mapping):
            return
        if set(value) == {"$ref"}:
            references.append(str(value["$ref"]))
            return
        for item in value.values():
            collect_expression(item)

    def visit(items: list[dict[str, Any]]) -> None:
        for step in items:
            step_ids.add(str(step["id"]))
            operation = step["op"]
            if operation == "set":
                variable_names.add(str(step["name"]))
                collect_expression(step["value"])
            elif operation == "tool":
                if step.get("save_as"):
                    variable_names.add(str(step["save_as"]))
                collect_expression(step["args"])
            elif operation == "model":
                variable_names.add(str(step["save_as"]))
                collect_expression(step["prompt"])
            elif operation == "branch":
                collect_expression(step["condition"])
                visit(step["then"])
                visit(step["else"])
            elif operation == "loop":
                variable_names.add(str(step["item"]))
                variable_names.add(str(step["index"]))
                collect_expression(step["items"])
                visit(step["steps"])
            elif operation == "wait":
                collect_expression(step["seconds"])
            else:
                collect_expression(step["content"])

    visit(steps)
    for reference in references:
        parts = reference.split(".")
        if parts[0] == "variables":
            valid = len(parts) >= 2 and parts[1] in variable_names
        elif parts[0] == "steps":
            valid = (
                len(parts) >= 3
                and parts[1] in step_ids
                and parts[2] == "output"
            )
        else:
            valid = (
                len(parts) >= 2
                and parts[0] in step_ids
                and parts[1] == "output"
            )
        if not valid:
            raise ScheduledTaskContractError(
                f"任务表达式引用未声明或不是步骤输出: {reference}"
            )


def normalize_scheduled_task_program(
    program: object,
) -> tuple[dict[str, Any], str, str]:
    """返回规范 program、canonical JSON 与 SHA-256。"""

    if not isinstance(program, Mapping):
        raise ScheduledTaskContractError("任务 program 必须是对象")
    version = program.get("version", SCHEDULED_TASK_PROGRAM_VERSION)
    if version != SCHEDULED_TASK_PROGRAM_VERSION:
        raise ScheduledTaskContractError(
            f"任务 program.version 只支持 {SCHEDULED_TASK_PROGRAM_VERSION}"
        )
    raw_limits = program.get("limits", {})
    if not isinstance(raw_limits, Mapping):
        raise ScheduledTaskContractError("任务 program.limits 必须是对象")
    max_steps = raw_limits.get("max_steps", MAX_SCHEDULED_TASK_STEPS)
    max_loops = raw_limits.get(
        "max_loop_iterations",
        MAX_SCHEDULED_TASK_LOOP_ITERATIONS,
    )
    max_duration = raw_limits.get(
        "max_duration_seconds",
        MAX_SCHEDULED_TASK_DURATION_SECONDS,
    )
    if type(max_steps) is not int or not 1 <= max_steps <= MAX_SCHEDULED_TASK_STEPS:
        raise ScheduledTaskContractError(
            f"limits.max_steps 必须是 1-{MAX_SCHEDULED_TASK_STEPS}"
        )
    if (
        type(max_loops) is not int
        or not 1 <= max_loops <= MAX_SCHEDULED_TASK_LOOP_ITERATIONS
    ):
        raise ScheduledTaskContractError(
            "limits.max_loop_iterations 必须是 1-"
            f"{MAX_SCHEDULED_TASK_LOOP_ITERATIONS}"
        )
    if (
        type(max_duration) is not int
        or not 1 <= max_duration <= MAX_SCHEDULED_TASK_DURATION_SECONDS
    ):
        raise ScheduledTaskContractError(
            "limits.max_duration_seconds 必须是 1-"
            f"{MAX_SCHEDULED_TASK_DURATION_SECONDS}"
        )
    counter = [0]
    steps = _normalize_program_steps(
        program.get("steps"),
        seen_ids=set(),
        counter=counter,
        root_loop_limit=max_loops,
    )
    _validate_program_references(steps)
    if counter[0] > max_steps:
        raise ScheduledTaskContractError(
            f"任务静态步骤超过 limits.max_steps={max_steps}"
        )
    normalized = {
        "version": SCHEDULED_TASK_PROGRAM_VERSION,
        "steps": steps,
        "limits": {
            "max_steps": max_steps,
            "max_loop_iterations": max_loops,
            "max_duration_seconds": max_duration,
        },
    }
    canonical = _canonical_program_json(normalized)
    if len(canonical.encode("utf-8")) > MAX_SCHEDULED_TASK_PROGRAM_BYTES:
        raise ScheduledTaskContractError(
            "任务 program UTF-8 大小不能超过 "
            f"{MAX_SCHEDULED_TASK_PROGRAM_BYTES} 字节"
        )
    return (
        normalized,
        canonical,
        hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def scheduled_task_program_schema() -> dict[str, Any]:
    """返回 API/KT 共享的 program 参数结构，避免仅暴露无约束 object。"""

    expression = {
        "description": (
            "JSON 常量或受限表达式；表达式对象只能包含一个 "
            "$ref/$eq/$ne/$lt/$lte/$gt/$gte/$and/$or/$not/"
            "$exists/$concat/$coalesce 运算符"
        )
    }
    step = {
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "id": {
                "type": "string",
                "pattern": r"^[A-Za-z][A-Za-z0-9_-]{0,63}$",
            },
            "op": {
                "type": "string",
                "enum": sorted(_PROGRAM_OPERATIONS),
            },
            "name": {"type": "string"},
            "value": expression,
            "tool": {"type": "string", "maxLength": 128},
            "args": {"type": "object"},
            "save_as": {"type": "string"},
            "recovery": {
                "type": "string",
                "enum": ["safe_retry", "ambiguous"],
            },
            "max_attempts": {
                "type": "integer",
                "minimum": 1,
                "maximum": 3,
            },
            "idempotency_arg": {"type": "string"},
            "prompt": expression,
            "condition": expression,
            "then": {"type": "array", "items": {"type": "object"}},
            "else": {"type": "array", "items": {"type": "object"}},
            "items": expression,
            "item": {"type": "string"},
            "index": {"type": "string"},
            "steps": {"type": "array", "items": {"type": "object"}},
            "max_iterations": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_SCHEDULED_TASK_LOOP_ITERATIONS,
            },
            "seconds": expression,
            "content": expression,
            "from": {"type": "string"},
        },
        "required": ["id", "op"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "description": (
            "version=1 的统一任务程序；只有 model 步骤调用模型，"
            "其余步骤由持久执行器直接解释"
        ),
        "properties": {
            "version": {"type": "integer", "enum": [1]},
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_SCHEDULED_TASK_STEPS,
                "items": step,
            },
            "limits": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "max_steps": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_SCHEDULED_TASK_STEPS,
                    },
                    "max_loop_iterations": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_SCHEDULED_TASK_LOOP_ITERATIONS,
                    },
                    "max_duration_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_SCHEDULED_TASK_DURATION_SECONDS,
                    },
                },
            },
        },
        "required": ["version", "steps"],
    }


def build_legacy_scheduled_task_program(
    prompt_template: object,
) -> dict[str, Any]:
    """把旧任务明确迁移为 ``model -> emit``，不猜测业务语义。"""

    prompt = str(prompt_template or "").strip()
    if not prompt:
        raise ScheduledTaskContractError("任务提示模板不能为空")
    if len(prompt) > MAX_SCHEDULED_TASK_PROMPT_CHARS:
        raise ScheduledTaskContractError(
            "任务提示模板不能超过 "
            f"{MAX_SCHEDULED_TASK_PROMPT_CHARS} 个 Unicode 字符"
        )
    return {
        "version": SCHEDULED_TASK_PROGRAM_VERSION,
        "steps": [
            {
                "id": "legacy_model",
                "op": "model",
                "prompt": prompt,
                "save_as": "legacy_output",
                "max_attempts": 2,
            },
            {
                "id": "legacy_emit",
                "op": "emit",
                "content": {"$ref": "steps.legacy_model.output"},
            },
        ],
        "limits": {
            "max_steps": MAX_SCHEDULED_TASK_STEPS,
            "max_loop_iterations": MAX_SCHEDULED_TASK_LOOP_ITERATIONS,
            "max_duration_seconds": MAX_SCHEDULED_TASK_DURATION_SECONDS,
        },
    }


def normalize_scheduled_task_definition(
    *,
    name: object,
    prompt_template: object = "",
    program: object | None = None,
) -> tuple[str, str, dict[str, Any], str, str]:
    """统一 API、Agent 工具、迁移与执行器使用的任务定义入口。"""

    normalized_name = validate_scheduled_task_name(name)
    normalized_prompt = str(prompt_template or "").strip()
    if len(normalized_prompt) > MAX_SCHEDULED_TASK_PROMPT_CHARS:
        raise ScheduledTaskContractError(
            "任务提示模板不能超过 "
            f"{MAX_SCHEDULED_TASK_PROMPT_CHARS} 个 Unicode 字符"
        )
    selected_program = (
        build_legacy_scheduled_task_program(normalized_prompt)
        if program is None
        else program
    )
    normalized_program, program_json, program_sha256 = (
        normalize_scheduled_task_program(selected_program)
    )
    return (
        normalized_name,
        normalized_prompt,
        normalized_program,
        program_json,
        program_sha256,
    )


def apply_scheduled_task_program(
    task: Any,
    *,
    name: object,
    prompt_template: object = "",
    program: object | None = None,
) -> dict[str, Any]:
    """校验并把统一定义投影到 ORM 任务。"""

    (
        normalized_name,
        normalized_prompt,
        normalized_program,
        program_json,
        program_sha256,
    ) = normalize_scheduled_task_definition(
        name=name,
        prompt_template=prompt_template,
        program=program,
    )
    task.name = normalized_name
    task.prompt_template = normalized_prompt
    task.program_json = program_json
    task.program_sha256 = program_sha256
    return normalized_program


def ensure_task_target_matches_owner(
    owner: ScheduledTaskOwner,
    *,
    target_type: object,
    target_id: object,
) -> None:
    """普通 Agent 工具只能投递到当前 owner 会话。"""

    requested_type = str(target_type or owner.target_type).strip().lower()
    requested_id = str(target_id or owner.target_id).strip()
    if (
        requested_type != owner.target_type
        or requested_id != owner.target_id
    ):
        raise ScheduledTaskContractError(
            "普通会话不能创建或修改其他会话的定时任务"
        )


__all__ = [
    "MAX_SCHEDULED_TASK_DURATION_SECONDS",
    "MAX_SCHEDULED_TASK_LOOP_ITERATIONS",
    "MAX_SCHEDULED_TASK_NAME_CHARS",
    "MAX_SCHEDULED_TASK_PROGRAM_BYTES",
    "MAX_SCHEDULED_TASK_PROMPT_CHARS",
    "MAX_SCHEDULED_TASK_STEPS",
    "MAX_SCHEDULED_TASK_WAIT_SECONDS",
    "SCHEDULED_TASK_PROGRAM_VERSION",
    "SCHEDULED_TASK_OWNER_PLATFORM",
    "SCHEDULED_TASK_TIMEZONE",
    "ScheduledTaskContractError",
    "ScheduledTaskOwner",
    "apply_scheduled_task_program",
    "apply_scheduled_task_owner",
    "build_legacy_scheduled_task_program",
    "ensure_task_target_matches_owner",
    "normalize_scheduled_task_definition",
    "normalize_scheduled_task_program",
    "scheduled_task_program_schema",
    "scheduled_task_owner_from_persisted",
    "scheduled_task_owner_from_runtime_context",
    "scheduled_task_owner_from_target",
    "validate_scheduled_task_definition",
    "validate_scheduled_task_name",
]
