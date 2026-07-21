"""请求级 Sandbox 工具编排、身份门禁与运行账本。"""

from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from core.database import SandboxRun, SystemSetting, ToolOverride
from core.sandbox.asset_service import AssetService
from core.sandbox.backend import SandboxBackend
from core.sandbox.client import HttpSandboxdBackend
from core.sandbox.contracts import (
    PublishedAsset,
    SandboxErrorCode,
    SandboxServiceError,
    success_result,
)
from core.sandbox.identity import Principal, derive_principal
from core.sandbox.repositories import SandboxRunRepository
from core.sandbox.run_ledger import SandboxRunLedger
from core.sandbox.workspace_service import WorkspacePolicy, WorkspaceService
from core.settings_service import coerce_setting_value, settings
from core.tool_registry import SANDBOX_TOOL_NAMES
from core.tracing_context import get_tool_trace_context, get_trace_context


_IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}")
logger = logging.getLogger(__name__)


def _request_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"


def _setting(db: Session, key: str, default: Any = None) -> Any:
    """请求数据库覆盖优先，随后使用统一设置服务的环境变量/默认值。"""

    try:
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if row is not None and row.value is not None:
            from core.config_registry import SETTING_DEFS

            definition = SETTING_DEFS.get(key)
            if definition is not None:
                return coerce_setting_value(row.value, definition)
            return row.value
    except Exception:
        pass
    return settings.get(key, default)


def _bool_setting(db: Session, key: str, default: bool = False) -> bool:
    return bool(_setting(db, key, default))


def resolve_sandbox_setting(db: Session, key: str, default: Any = None) -> Any:
    return _setting(db, key, default)


def workspace_policy_from_settings(db: Session) -> WorkspacePolicy:
    return WorkspacePolicy(
        default_quota_bytes=int(_setting(db, "sandbox.workspace_quota_bytes")),
        total_quota_bytes=int(_setting(db, "sandbox.total_quota_bytes")),
        disk_max_percent=int(_setting(db, "sandbox.disk_max_percent")),
        disk_min_free_bytes=int(_setting(db, "sandbox.disk_min_free_bytes")),
    )


def _runtime_context(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SandboxServiceError(
            SandboxErrorCode.AUTHORIZATION_FAILED,
            "无法从受信请求上下文确认 Sandbox 身份",
        )
    return {str(key): item for key, item in value.items()}


def authorize_sandbox_principal(
    db: Session,
    tool_name: str,
    context: Mapping[str, Any] | None,
) -> tuple[Principal, dict[str, Any]]:
    """供模型工具与受信资产入口复用的同一套首期授权门禁。"""

    if tool_name not in SANDBOX_TOOL_NAMES:
        raise SandboxServiceError(
            SandboxErrorCode.AUTHORIZATION_FAILED,
            "Sandbox 工具身份无效",
        )
    runtime = _runtime_context(context)
    if not _bool_setting(db, "sandbox.enabled"):
        raise SandboxServiceError(
            SandboxErrorCode.SANDBOX_NOT_ENABLED,
            "Sandbox 当前未启用",
            hint="停止重试，并告知用户当前能力未开放",
        )
    if tool_name == "sandbox_exec" and not _bool_setting(
        db,
        "sandbox.exec_enabled",
    ):
        raise SandboxServiceError(
            SandboxErrorCode.SANDBOX_NOT_ENABLED,
            "Sandbox 执行能力当前未启用",
            hint="Workspace 文件工具仍可单独开放",
        )

    chat_type = str(runtime.get("chat_type") or "").strip().lower()
    runtime_chat_type = str(
        runtime.get("runtime_chat_type") or chat_type
    ).strip().lower()
    user_id = str(runtime.get("user_id") or "").strip()
    group_enabled = _bool_setting(db, "sandbox.group_enabled")
    if chat_type == "group" and not group_enabled:
        raise SandboxServiceError(
            SandboxErrorCode.SANDBOX_NOT_ENABLED,
            "群聊 Workspace 当前未启用",
            hint="停止重试，并告知用户当前只开放私聊 Workspace",
        )
    allowlist = False
    if user_id:
        allowlist = db.query(ToolOverride).filter(
            ToolOverride.tool_name == tool_name,
            ToolOverride.scope_type == "user",
            ToolOverride.scope_id == user_id,
            ToolOverride.enabled == 1,
        ).first() is not None
    if not (
        (
            runtime_chat_type == "private_superuser"
            and runtime.get("is_super_user") is True
        )
        or allowlist
    ):
        raise SandboxServiceError(
            SandboxErrorCode.AUTHORIZATION_FAILED,
            "当前身份没有使用 Sandbox 的权限",
        )

    principal = derive_principal(runtime, group_enabled=group_enabled)
    return principal, runtime


class SandboxToolService:
    """模型工具只传业务参数；owner、Workspace 与 Docker 策略均由服务端派生。"""

    def __init__(
        self,
        db: Session,
        backend: SandboxBackend,
        *,
        run_session_factory: Callable[[], Session] | None = None,
    ) -> None:
        self.db = db
        self.backend = backend
        self.workspace_service = WorkspaceService(
            db,
            policy=workspace_policy_from_settings(db),
        )
        self.asset_service = AssetService(
            db,
            workspace_service=self.workspace_service,
            max_asset_bytes=int(_setting(db, "sandbox.asset_max_bytes")),
        )
        self.run_repository = SandboxRunRepository(db)
        if run_session_factory is None:
            run_session_factory = sessionmaker(
                bind=db.get_bind(),
                autoflush=False,
                expire_on_commit=False,
            )
        self.run_ledger = SandboxRunLedger(run_session_factory)

    @classmethod
    def from_settings(cls, db: Session) -> "SandboxToolService":
        backend = HttpSandboxdBackend(
            socket_path=str(_setting(db, "sandbox.sandboxd_socket")),
            token_file=str(_setting(db, "sandbox.sandboxd_token_file")),
            timeout_seconds=float(_setting(db, "sandbox.backend_timeout_seconds")),
            run_timeout_seconds=float(_setting(db, "sandbox.run_timeout_seconds")),
        )
        return cls(db, backend)

    def close(self) -> None:
        close = getattr(self.backend, "close", None)
        if callable(close):
            close()

    def authorize(
        self,
        tool_name: str,
        context: Mapping[str, Any] | None,
    ) -> tuple[Principal, dict[str, Any]]:
        return authorize_sandbox_principal(self.db, tool_name, context)

    def _workspace(
        self,
        principal: Principal,
    ):
        workspace = self.workspace_service.ensure_default(principal)
        self.backend.ensure_workspace(
            workspace.id,
            request_id=_request_id("sbxensure"),
        )
        return workspace

    @staticmethod
    def _data(response: Mapping[str, Any]) -> dict[str, Any]:
        data = response.get("data")
        if not isinstance(data, Mapping):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 控制面返回了无效响应",
                retryable=True,
                stop=False,
            )
        return dict(data)

    def workspace_list(self, args: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        principal, _runtime = self.authorize("workspace_list", context)
        workspace = self._workspace(principal)
        return self.backend.list_files({
            "workspace_id": workspace.id,
            "path": str(args.get("path") or ""),
            "cursor": str(args.get("cursor") or ""),
            "limit": int(args.get("limit") or 100),
        })

    def workspace_read(self, args: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        principal, _runtime = self.authorize("workspace_read", context)
        workspace = self._workspace(principal)
        return self.backend.read_file({
            "workspace_id": workspace.id,
            "path": str(args.get("path") or ""),
            "offset": int(args.get("offset") or 0),
            "limit": int(args.get("limit") or 64 * 1024),
        })

    def workspace_search(self, args: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        principal, _runtime = self.authorize("workspace_search", context)
        workspace = self._workspace(principal)
        return self.backend.search_files({
            "workspace_id": workspace.id,
            "query": str(args.get("query") or ""),
            "path": str(args.get("path") or ""),
            "glob": str(args.get("glob") or ""),
            "limit": int(args.get("limit") or 50),
        })

    def workspace_write(self, args: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        principal, _runtime = self.authorize("workspace_write", context)
        workspace = self._workspace(principal)
        response = self.backend.write_file({
            "workspace_id": workspace.id,
            "path": str(args.get("path") or ""),
            "content": str(args.get("content") or ""),
            "overwrite": bool(args.get("overwrite", False)),
            "quota_bytes": int(workspace.quota_bytes),
        })
        data = self._data(response)
        if (
            data.get("used_bytes") is None
            or data.get("usage_delta_bytes") is None
        ):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 控制面返回了无效响应",
                retryable=True,
                stop=False,
            )
        self.workspace_service.record_usage_delta(
            workspace.id,
            delta_bytes=int(data["usage_delta_bytes"]),
            observed_used_bytes=int(data["used_bytes"]),
        )
        return response

    def asset_import(self, args: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        principal, _runtime = self.authorize("asset_import", context)
        self._workspace(principal)
        source_ref = str(args.get("source_ref") or "")
        logical_name = str(args.get("logical_name") or "").strip()
        prefix = "asset://sha256/"
        if not source_ref.startswith(prefix):
            raise SandboxServiceError(
                SandboxErrorCode.ASSET_NOT_AUTHORIZED,
                "资产不存在或当前 Workspace 无权访问",
                hint="附件引用需先通过受信上传接口登记",
            )
        source_link, source_asset = self.asset_service.require_authorized(
            principal,
            source_ref[len(prefix):],
        )
        asset, link = self.asset_service.import_authorized_ref(
            principal,
            source_ref,
            logical_name=logical_name or source_link.logical_name,
        )
        return success_result(
            "资产已链接到当前 Workspace",
            data={
                "ref": f"asset://sha256/{asset.sha256}",
                "logical_name": link.logical_name,
                "size_bytes": int(source_asset.size_bytes),
                "media_type": source_asset.media_type,
            },
            artifacts=[{
                "type": "asset",
                "ref": f"asset://sha256/{asset.sha256}",
                "logical_name": link.logical_name,
                "size_bytes": int(asset.size_bytes),
            }],
        )

    def asset_publish(self, args: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        principal, _runtime = self.authorize("asset_publish", context)
        try:
            from core.asset_tokens import AssetTokenError, signer_from_settings
            from core.asset_transport import build_asset_reply_token

            signer = signer_from_settings(self.db)
        except AssetTokenError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "资产下载凭据未安全配置",
            ) from exc
        workspace = self._workspace(principal)
        path = str(args.get("path") or "")
        response = self.backend.publish_asset({
            "workspace_id": workspace.id,
            "path": path,
            "media_type": str(
                args.get("media_type") or "application/octet-stream"
            ),
        })
        data = self._data(response)
        asset, link = self.asset_service.register_published(
            principal,
            PublishedAsset(
                sha256=str(data.get("sha256") or ""),
                size_bytes=int(data.get("size_bytes") or 0),
                media_type=str(data.get("media_type") or "application/octet-stream"),
                storage_key=str(data.get("storage_key") or ""),
            ),
            logical_name=path,
        )
        transport_token = signer.issue(
            asset.sha256,
            recipient_type=principal.owner_type,
            recipient_id=principal.owner_id,
        )
        claims = signer.verify(transport_token)
        reply_token = build_asset_reply_token(transport_token)
        return success_result(
            "Workspace 文件已发布为不可变资产",
            data={
                "ref": f"asset://sha256/{asset.sha256}",
                "logical_name": link.logical_name,
                "size_bytes": int(asset.size_bytes),
                "media_type": asset.media_type,
                "transport_token": transport_token,
                "reply_token": reply_token,
                "recipient_type": claims.recipient_type,
                "recipient_id": claims.recipient_id,
                "expires_at": claims.expires_at,
            },
            artifacts=[{
                "type": "asset",
                "ref": f"asset://sha256/{asset.sha256}",
                "logical_name": link.logical_name,
                "size_bytes": int(asset.size_bytes),
                "transport_token": transport_token,
                "reply_token": reply_token,
            }],
        )

    def _authorized_assets(self, workspace_id: str) -> list[dict[str, str]]:
        return [
            {
                "sha256": asset.sha256,
                "storage_key": asset.storage_key,
                "logical_name": link.logical_name,
            }
            for link, asset in self.asset_service.link_repository.list_authorized(
                workspace_id,
            )
        ]

    def _stage_authorized_assets(
        self,
        workspace_id: str,
        run_id: str,
        assets: list[dict[str, str]],
    ) -> None:
        self.backend.stage_assets({
            "workspace_id": workspace_id,
            "run_id": run_id,
            "assets": assets,
        })

    def _record_failed_run(
        self,
        run_id: str | None,
        error: SandboxServiceError,
    ) -> None:
        if run_id is None:
            return
        try:
            self.run_ledger.mark_failed(
                run_id,
                termination_reason=error.code.value,
            )
        except Exception:
            logger.error(
                "Sandbox 运行终态账本写入失败：run_id=%s",
                run_id,
                exc_info=True,
            )

    def sandbox_exec(self, args: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        principal, _runtime = self.authorize("sandbox_exec", context)
        workspace = self._workspace(principal)
        ready = self._data(self.backend.ready())
        image_digest = str(ready.get("image_id") or "").lower()
        if not _IMAGE_ID_RE.fullmatch(image_digest):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 固定镜像身份不可用",
            )

        request_id = _request_id("sbxreq")
        run_id = _request_id("sbxrun")
        workspace_id = str(workspace.id)
        workspace_quota_bytes = int(workspace.quota_bytes)
        authorized_assets = self._authorized_assets(workspace_id)
        trace_id, agent_run_id = get_trace_context()
        tool_call_id = get_tool_trace_context()
        row = SandboxRun(
            run_id=run_id,
            request_id=request_id,
            workspace_id=workspace_id,
            trace_id=str(trace_id or "")[:64],
            agent_run_id=str(agent_run_id or "")[:64],
            tool_call_id=str(tool_call_id or "")[:64],
            image_digest=image_digest,
            status="pending",
        )
        self.run_repository.add(row)
        # 这是显式的阶段事务：Workspace 外键和 pending 账本必须先持久化，
        # 后续 running/终态再由独立短 Session 写入，不能依赖工具外层 UoW。
        self.db.commit()

        try:
            self._stage_authorized_assets(
                workspace_id,
                run_id,
                authorized_assets,
            )
            self.run_ledger.mark_running(run_id)
            payload: dict[str, Any] = {
                "request_id": request_id,
                "run_id": run_id,
                "workspace_id": workspace_id,
                "command": str(args.get("command") or ""),
                "cwd": str(args.get("cwd") or ""),
                "quota_bytes": workspace_quota_bytes,
            }
            if args.get("timeout_seconds") is not None:
                payload["timeout_seconds"] = int(args["timeout_seconds"])
            response = self.backend.run(payload)
            data = self._data(response)
            response_run_id = str(data.get("run_id") or "")
            if response_run_id and response_run_id != run_id:
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "Sandbox 控制面返回了无效运行标识",
                )
            data["run_id"] = run_id
            response = {**dict(response), "data": data}
            reason = str(data.get("termination_reason") or "completed")[:64]
            status = "cancelled" if reason == "cancelled" else (
                "completed" if reason == "completed" else "failed"
            )
            self.run_ledger.mark_terminal(
                run_id,
                status=status,
                termination_reason=reason,
                data=data,
            )
            if data.get("workspace_used_bytes") is not None:
                if data.get("workspace_usage_delta_bytes") is None:
                    raise SandboxServiceError(
                        SandboxErrorCode.RUNTIME_UNAVAILABLE,
                        "Sandbox 控制面返回了无效空间核算结果",
                        retryable=True,
                        stop=False,
                    )
                self.workspace_service.record_usage_delta(
                    workspace_id,
                    delta_bytes=int(data["workspace_usage_delta_bytes"]),
                    observed_used_bytes=int(data["workspace_used_bytes"]),
                )
            return response
        except SandboxServiceError as error:
            self._record_failed_run(run_id, error)
            raise
        except Exception as exc:
            error = SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 运行账本更新失败",
                retryable=True,
                stop=False,
            )
            self._record_failed_run(run_id, error)
            raise error from exc
