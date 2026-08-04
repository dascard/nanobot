"""Sandbox 模型工具的框架无关执行服务。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from typing import Any

from core.sandbox.contracts import (
    SandboxErrorCode,
    SandboxServiceError,
)
from core.sandbox.tool_service import SandboxToolService
from core.tool_contracts.result import ToolServiceResult
from core.uow import UnitOfWork


ServiceFactory = Callable[[Any], SandboxToolService]


def _invalid_arguments() -> SandboxServiceError:
    return SandboxServiceError(
        SandboxErrorCode.AUTHORIZATION_FAILED,
        "Sandbox 工具参数无效或包含未允许字段",
    )


def _validate_value(value: Any, schema: Mapping[str, Any]) -> None:
    alternatives = schema.get("oneOf")
    if isinstance(alternatives, list):
        matches = 0
        for alternative in alternatives:
            if not isinstance(alternative, Mapping):
                raise _invalid_arguments()
            try:
                _validate_value(value, alternative)
            except SandboxServiceError:
                continue
            matches += 1
        if matches != 1:
            raise _invalid_arguments()
        return

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        raise _invalid_arguments()

    value_type = schema.get("type")
    if value_type == "string":
        if not isinstance(value, str) or "\x00" in value:
            raise _invalid_arguments()
        encoded_size = len(value.encode("utf-8"))
        if encoded_size < int(schema.get("minLength", 0)):
            raise _invalid_arguments()
        if schema.get("maxLength") is not None and encoded_size > int(
            schema["maxLength"]
        ):
            raise _invalid_arguments()
    elif value_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise _invalid_arguments()
        if schema.get("minimum") is not None and value < int(schema["minimum"]):
            raise _invalid_arguments()
        if schema.get("maximum") is not None and value > int(schema["maximum"]):
            raise _invalid_arguments()
    elif value_type == "boolean":
        if not isinstance(value, bool):
            raise _invalid_arguments()
    elif value_type == "array":
        if not isinstance(value, list):
            raise _invalid_arguments()
        if len(value) < int(schema.get("minItems", 0)):
            raise _invalid_arguments()
        if schema.get("maxItems") is not None and len(value) > int(
            schema["maxItems"]
        ):
            raise _invalid_arguments()
        item_schema = schema.get("items")
        if not isinstance(item_schema, Mapping):
            raise _invalid_arguments()
        for item in value:
            _validate_value(item, item_schema)
    elif value_type == "object":
        if not isinstance(value, Mapping):
            raise _invalid_arguments()
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            raise _invalid_arguments()
        normalized_keys = {str(key) for key in value}
        if schema.get("additionalProperties") is False and (
            normalized_keys - set(properties)
        ):
            raise _invalid_arguments()
        for required in schema.get("required") or []:
            if required not in normalized_keys:
                raise _invalid_arguments()
        for raw_name, item in value.items():
            name = str(raw_name)
            item_schema = properties.get(name)
            if not isinstance(item_schema, Mapping):
                if schema.get("additionalProperties") is False:
                    raise _invalid_arguments()
                continue
            _validate_value(item, item_schema)
    else:
        raise _invalid_arguments()


def validate_sandbox_arguments(
    args: Any,
    schema: Mapping[str, Any],
) -> dict[str, Any]:
    """按 canonical schema 拒绝额外字段和不可信类型。"""

    if not isinstance(args, Mapping):
        raise _invalid_arguments()
    normalized = {str(key): value for key, value in args.items()}
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        raise _invalid_arguments()
    if set(normalized) - set(properties):
        raise _invalid_arguments()
    for required in schema.get("required") or []:
        if required not in normalized:
            raise _invalid_arguments()
    for name, value in normalized.items():
        field_schema = properties.get(name)
        if not isinstance(field_schema, Mapping):
            raise _invalid_arguments()
        _validate_value(value, field_schema)
    return normalized


def _invoke_sandbox_tool(
    tool_name: str,
    args: dict[str, Any],
    runtime_context: dict[str, Any],
    *,
    session_factory: Callable[[], Any] | None,
    service_factory: ServiceFactory,
) -> dict[str, Any]:
    with UnitOfWork(session_factory=session_factory) as uow:
        if uow.db is None:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 数据库会话不可用",
                retryable=True,
                stop=False,
            )
        service = service_factory(uow.db)
        try:
            operation = getattr(service, tool_name, None)
            if not callable(operation):
                raise SandboxServiceError(
                    SandboxErrorCode.AUTHORIZATION_FAILED,
                    "Sandbox 工具身份无效",
                )
            result = operation(args, runtime_context)
            uow.commit()
            return result
        finally:
            service.close()


async def execute_sandbox_tool(
    tool_name: str,
    args: dict[str, Any],
    *,
    session_factory: Callable[[], Any] | None = None,
    service_factory: ServiceFactory = SandboxToolService.from_settings,
) -> ToolServiceResult:
    """在当前受信 Runtime owner 下执行一个 Sandbox 工具。"""

    try:
        from core.agent_runtime.request_scope import (
            require_current_runtime_context,
        )
        from core.tool_schema_preview import STATIC_TOOL_SCHEMAS

        definition = STATIC_TOOL_SCHEMAS.get(str(tool_name or "").strip())
        if not isinstance(definition, Mapping):
            raise _invalid_arguments()
        parameters = definition.get("parameters")
        if not isinstance(parameters, Mapping):
            raise _invalid_arguments()
        normalized = validate_sandbox_arguments(args, parameters)
        runtime_context = require_current_runtime_context()
        result = await asyncio.to_thread(
            _invoke_sandbox_tool,
            str(tool_name),
            normalized,
            runtime_context,
            session_factory=session_factory,
            service_factory=service_factory,
        )
    except SandboxServiceError as error:
        result = error.to_result()
    except RuntimeError:
        result = SandboxServiceError(
            SandboxErrorCode.AUTHORIZATION_FAILED,
            "无法从受信请求上下文确认 Sandbox 身份",
        ).to_result()
    except Exception:
        result = SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "Sandbox 工具暂时不可用",
            retryable=True,
            hint="稍后重试；主聊天功能不受影响",
            stop=False,
        ).to_result()

    failed = result.get("status") == "error"
    error = result.get("error")
    error_code = (
        str(error.get("code") or "sandbox_error")
        if isinstance(error, Mapping)
        else "sandbox_error"
    )
    summary = str(result.get("summary") or "Sandbox 工具执行失败")
    return ToolServiceResult(
        output=json.dumps(result, ensure_ascii=False, separators=(",", ":")),
        exit_code=1 if failed else 0,
        error=f"{error_code}: {summary}" if failed else None,
        metadata={"structured_content": result},
    )


__all__ = [
    "ServiceFactory",
    "execute_sandbox_tool",
    "validate_sandbox_arguments",
]
