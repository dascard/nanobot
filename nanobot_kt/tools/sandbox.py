"""Sandbox 模型工具的 KT 适配层。"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Callable, Mapping
from typing import Any

from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolConfig,
    ToolResult,
)

from core.sandbox.contracts import (
    SandboxErrorCode,
    SandboxServiceError,
)
from core.sandbox.tool_service import SandboxToolService
from core.tool_registry import get_tool_def
from core.uow import UnitOfWork


ServiceFactory = Callable[[Any], SandboxToolService]


def _invalid_arguments() -> SandboxServiceError:
    return SandboxServiceError(
        SandboxErrorCode.AUTHORIZATION_FAILED,
        "Sandbox 工具参数无效或包含未允许字段",
    )


def _validate_value(value: Any, schema: Mapping[str, Any]) -> None:
    value_type = schema.get("type")
    if value_type == "string":
        if not isinstance(value, str) or "\x00" in value:
            raise _invalid_arguments()
        encoded_size = len(value.encode("utf-8"))
        if encoded_size < int(schema.get("minLength", 0)):
            raise _invalid_arguments()
        if schema.get("maxLength") is not None and encoded_size > int(
            schema["maxLength"],
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


def _validate_arguments(args: Any, schema: Mapping[str, Any]) -> dict[str, Any]:
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


class SandboxToolBase(BaseTool):
    """工具只负责严格参数校验和稳定信封，不接受 owner/Workspace/Docker 参数。"""

    needs_context = True
    supports_background = False

    def __init__(
        self,
        config: ToolConfig | None = None,
        *,
        session_factory: Callable[[], Any] | None = None,
        service_factory: ServiceFactory | None = None,
    ) -> None:
        super().__init__(config)
        self._session_factory = session_factory
        self._service_factory = service_factory or SandboxToolService.from_settings

    @property
    def description(self) -> str:
        definition = get_tool_def(self.tool_name)
        return definition.description if definition else self.tool_name

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        from core.tool_schema_preview import STATIC_TOOL_SCHEMAS

        return copy.deepcopy(STATIC_TOOL_SCHEMAS[self.tool_name]["parameters"])

    @staticmethod
    def _trusted_runtime_context(context: Any) -> dict[str, Any]:
        session = getattr(context, "session", None) if context is not None else None
        extra = getattr(session, "extra", None) if session is not None else None
        runtime = extra.get("nanobot_runtime_context") if isinstance(extra, dict) else None
        if not isinstance(runtime, Mapping):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "无法从受信请求上下文确认 Sandbox 身份",
            )
        return {str(key): value for key, value in runtime.items()}

    def _invoke_sync(
        self,
        args: dict[str, Any],
        runtime_context: dict[str, Any],
    ) -> dict[str, Any]:
        with UnitOfWork(session_factory=self._session_factory) as uow:
            if uow.db is None:
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "Sandbox 数据库会话不可用",
                    retryable=True,
                    stop=False,
                )
            service = self._service_factory(uow.db)
            try:
                operation = getattr(service, self.tool_name)
                result = operation(args, runtime_context)
                uow.commit()
                return result
            finally:
                service.close()

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        try:
            normalized = _validate_arguments(args, self.get_parameters_schema())
            runtime_context = self._trusted_runtime_context(kwargs.get("context"))
            result = await asyncio.to_thread(
                self._invoke_sync,
                normalized,
                runtime_context,
            )
        except SandboxServiceError as error:
            result = error.to_result()
        except Exception:
            result = SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 工具暂时不可用",
                retryable=True,
                hint="稍后重试；主聊天功能不受影响",
                stop=False,
            ).to_result()
        return ToolResult(
            output=json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            exit_code=0,
            metadata={"structured_content": result},
        )


class SandboxExecTool(SandboxToolBase):
    is_concurrency_safe = False

    @property
    def tool_name(self) -> str:
        return "sandbox_exec"


class SandboxPollTool(SandboxToolBase):
    is_concurrency_safe = True

    @property
    def tool_name(self) -> str:
        return "sandbox_poll"


class SandboxWriteStdinTool(SandboxToolBase):
    is_concurrency_safe = False

    @property
    def tool_name(self) -> str:
        return "sandbox_write_stdin"


class SandboxTerminateTool(SandboxToolBase):
    is_concurrency_safe = False

    @property
    def tool_name(self) -> str:
        return "sandbox_terminate"


class WorkspaceListTool(SandboxToolBase):
    @property
    def tool_name(self) -> str:
        return "workspace_list"


class WorkspaceReadTool(SandboxToolBase):
    @property
    def tool_name(self) -> str:
        return "workspace_read"


class WorkspaceSearchTool(SandboxToolBase):
    @property
    def tool_name(self) -> str:
        return "workspace_search"


class WorkspaceWriteTool(SandboxToolBase):
    is_concurrency_safe = False

    @property
    def tool_name(self) -> str:
        return "workspace_write"


class WorkspaceApplyPatchTool(SandboxToolBase):
    is_concurrency_safe = False

    @property
    def tool_name(self) -> str:
        return "workspace_apply_patch"


class AssetImportTool(SandboxToolBase):
    is_concurrency_safe = False

    @property
    def tool_name(self) -> str:
        return "asset_import"


class AssetPublishTool(SandboxToolBase):
    is_concurrency_safe = False

    @property
    def tool_name(self) -> str:
        return "asset_publish"


__all__ = [
    "AssetImportTool",
    "AssetPublishTool",
    "SandboxExecTool",
    "SandboxPollTool",
    "SandboxTerminateTool",
    "SandboxToolBase",
    "SandboxWriteStdinTool",
    "WorkspaceApplyPatchTool",
    "WorkspaceListTool",
    "WorkspaceReadTool",
    "WorkspaceSearchTool",
    "WorkspaceWriteTool",
]
