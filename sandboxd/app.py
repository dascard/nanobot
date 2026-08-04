"""只监听 Unix Domain Socket 的 sandboxd FastAPI 控制面。"""

import hashlib
import logging
import os
import secrets
import socket
import stat
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.sandbox.contracts import (
    SandboxErrorCode,
    SandboxServiceError,
    success_result,
)
from sandboxd.auth import TokenAuthenticator
from sandboxd.config import SandboxdConfig
from sandboxd.docker_backend import LocalDockerBackend
from sandboxd.filesystem import AssetFileService, WorkspaceFileService
from sandboxd.lease_backend import LeaseBackend
from sandboxd.lease_reconciler import LeaseReconciler
from sandboxd.lease_store import LeaseStore
from sandboxd.process_manager import LeaseProcessManager
from sandboxd.quota import ProjectQuotaManager
from sandboxd.reconciler import OrphanReconciler
from core.sandbox.paths import SafeWorkspaceFilesystem, validate_sha256, validate_workspace_id


logger = logging.getLogger("nanobot.sandboxd")


class RangeNotSatisfiable(ValueError):
    pass


def _byte_range(value: str, size_bytes: int) -> tuple[int, int, bool]:
    raw = str(value or "").strip()
    if not raw:
        return 0, size_bytes - 1, False
    if len(raw) > 128 or not raw.startswith("bytes=") or "," in raw:
        raise RangeNotSatisfiable
    spec = raw.removeprefix("bytes=")
    if "-" not in spec:
        raise RangeNotSatisfiable
    start_raw, end_raw = spec.split("-", 1)
    try:
        if not start_raw:
            if not end_raw.isascii() or not end_raw.isdigit():
                raise RangeNotSatisfiable
            suffix = int(end_raw)
            if suffix <= 0 or size_bytes <= 0:
                raise RangeNotSatisfiable
            start = max(0, size_bytes - suffix)
            end = size_bytes - 1
        else:
            if not start_raw.isascii() or not start_raw.isdigit():
                raise RangeNotSatisfiable
            if end_raw and (not end_raw.isascii() or not end_raw.isdigit()):
                raise RangeNotSatisfiable
            start = int(start_raw)
            end = int(end_raw) if end_raw else size_bytes - 1
            if start < 0 or end < start or start >= size_bytes:
                raise RangeNotSatisfiable
            end = min(end, size_bytes - 1)
    except ValueError as exc:
        raise RangeNotSatisfiable from exc
    return start, end, True


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceRequest(StrictModel):
    workspace_id: str = Field(min_length=36, max_length=36)


class FileListRequest(WorkspaceRequest):
    path: str = Field(default="", max_length=4096)
    cwd: str = Field(default="", max_length=4096)
    cursor: str = Field(default="", max_length=64)
    limit: int = Field(default=100, ge=1, le=200)


class FileReadRequest(WorkspaceRequest):
    path: str = Field(min_length=1, max_length=4096)
    cwd: str = Field(default="", max_length=4096)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1, le=2000)


class FileReadTextRequest(WorkspaceRequest):
    path: str = Field(min_length=1, max_length=4096)
    cwd: str = Field(default="", max_length=4096)


class FileSearchRequest(WorkspaceRequest):
    mode: Literal["content", "files", "tree"] = "content"
    pattern: str = Field(default="", max_length=1024)
    # 兼容旧 sandboxd 客户端；新模型 Schema 不暴露 query。
    query: str = Field(default="", max_length=1024)
    path: str = Field(default="", max_length=4096)
    cwd: str = Field(default="", max_length=4096)
    glob: str = Field(default="", max_length=512)
    limit: int = Field(default=50, ge=1, le=200)
    ignore_case: bool = False
    max_depth: int | None = Field(default=None, ge=0, le=100)
    cursor: str = Field(default="", max_length=2048)


class FileWriteRequest(WorkspaceRequest):
    path: str = Field(min_length=1, max_length=4096)
    cwd: str = Field(default="", max_length=4096)
    content: str = Field(max_length=256 * 1024)
    overwrite: bool = False
    expected_sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    quota_bytes: int = Field(gt=0, le=1024 * 1024 * 1024 * 1024)


class FilePatchRequest(WorkspaceRequest):
    path: str = Field(min_length=1, max_length=4096)
    patch: str = Field(min_length=1, max_length=256 * 1024)
    quota_bytes: int = Field(gt=0, le=1024 * 1024 * 1024 * 1024)


class ExactEditOperation(StrictModel):
    path: str = Field(min_length=1, max_length=4096)
    old: str = Field(min_length=1, max_length=256 * 1024)
    new: str = Field(max_length=256 * 1024)
    replace_all: bool = False


class DiffEditOperation(StrictModel):
    diff: str = Field(min_length=1, max_length=256 * 1024)


class FileEditRequest(WorkspaceRequest):
    operations: list[ExactEditOperation | DiffEditOperation] = Field(
        min_length=1,
        max_length=50,
    )
    cwd: str = Field(default="", max_length=4096)
    quota_bytes: int = Field(gt=0, le=1024 * 1024 * 1024 * 1024)


class AssetPublishRequest(WorkspaceRequest):
    path: str = Field(min_length=1, max_length=4096)
    media_type: str = Field(default="application/octet-stream", max_length=255)


class AssetMaterializeRequest(WorkspaceRequest):
    sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    storage_key: str = Field(min_length=1, max_length=255)
    path: str = Field(min_length=1, max_length=4096)
    quota_bytes: int = Field(gt=0, le=1024 * 1024 * 1024 * 1024)
    overwrite: bool = False


class StagedAsset(StrictModel):
    sha256: str = Field(min_length=64, max_length=64)
    storage_key: str = Field(min_length=1, max_length=255)
    logical_name: str = Field(min_length=1, max_length=512)


class AssetStageRequest(WorkspaceRequest):
    run_id: str = Field(min_length=8, max_length=64)
    assets: list[StagedAsset] = Field(default_factory=list, max_length=100)


class LeaseEnsureRequest(WorkspaceRequest):
    request_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    lease_id: str = Field(
        min_length=10,
        max_length=64,
        pattern=r"^sbxlease_[A-Za-z0-9_-]+$",
    )
    profile_id: str = Field(min_length=1, max_length=32)
    catalog_generation: str = Field(min_length=1, max_length=64)
    policy_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    quota_generation: int = Field(ge=1, le=2_147_483_647)


class LeaseAssetStageRequest(WorkspaceRequest):
    request_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    assets: list[StagedAsset] = Field(default_factory=list, max_length=100)


class LeaseActionRequest(StrictModel):
    request_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class ProcessStartRequest(StrictModel):
    request_id: str = Field(
        min_length=8,
        max_length=64,
        pattern=r"^sbxrun_[A-Za-z0-9_-]+$",
    )
    command: str = Field(min_length=1, max_length=16 * 1024)
    cwd: str = Field(default="", max_length=4096)
    yield_time_ms: int = Field(default=10_000, ge=0, le=30_000)
    timeout_seconds: int | None = Field(default=None, ge=1, le=3600)


class ProcessStdinRequest(LeaseActionRequest):
    chars: str = Field(min_length=1, max_length=64 * 1024)


class TerminateAllRequest(LeaseActionRequest):
    reason: str = Field(
        default="admin_terminated",
        pattern=(
            r"^(admin_terminated|kill_switch|controller_restarted|"
            r"lease_recycled)$"
        ),
    )


class RunRequest(WorkspaceRequest):
    request_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=8, max_length=64)
    command: str = Field(min_length=1, max_length=16 * 1024)
    cwd: str = Field(default="", max_length=4096)
    timeout_seconds: int | None = Field(default=None, ge=1, le=120)
    quota_bytes: int = Field(gt=0, le=1024 * 1024 * 1024 * 1024)


class EmptyRequest(StrictModel):
    pass


class WorkspaceQuotaRequest(WorkspaceRequest):
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[!-~]+$")
    project_id: int = Field(ge=10000, le=2_147_483_647)
    quota_bytes: int = Field(ge=1024 * 1024, le=1024 * 1024 * 1024 * 1024)
    runtime_project_id: int = Field(ge=10000, le=2_147_483_647)
    runtime_quota_bytes: int = Field(
        ge=1024 * 1024,
        le=1024 * 1024 * 1024 * 1024,
    )
    generation: int = Field(ge=1, le=2_147_483_647)

    @model_validator(mode="after")
    def validate_distinct_project_ids(self) -> "WorkspaceQuotaRequest":
        if self.project_id == self.runtime_project_id:
            raise ValueError("Workspace 与 Runtime project ID 必须不同")
        return self


@dataclass
class SandboxRuntime:
    config: SandboxdConfig
    authenticator: TokenAuthenticator
    workspace_files: WorkspaceFileService
    asset_files: AssetFileService
    docker_backend: LocalDockerBackend
    admin_authenticator: TokenAuthenticator | None = None
    quota_manager: ProjectQuotaManager | None = None
    lease_store: LeaseStore | None = None
    lease_backend: LeaseBackend | None = None
    lease_reconciler: LeaseReconciler | None = None
    process_manager: LeaseProcessManager | None = None

    @classmethod
    def build(cls, config: SandboxdConfig) -> "SandboxRuntime":
        workspace_files = WorkspaceFileService(config)
        asset_files = AssetFileService(config)
        quota_manager = ProjectQuotaManager(
            data_root=config.data_root,
            helper_path=config.quota_helper_path,
        )
        docker_backend = LocalDockerBackend(
            config,
            workspace_files=workspace_files,
            asset_files=asset_files,
            quota_manager=quota_manager,
        )
        lease_store = LeaseStore(
            config.data_root / "runtime" / ".sandboxd-leases"
        )
        lease_backend = LeaseBackend(
            config,
            docker_client=docker_backend.client,
            workspace_files=workspace_files,
            asset_files=asset_files,
            lease_store=lease_store,
            profile_image_resolver=docker_backend.require_profile_image,
            network_policy=docker_backend.network_policy,
        )
        process_manager = LeaseProcessManager(
            config,
            lease_backend=lease_backend,
            lease_store=lease_store,
            concurrency_limiter=docker_backend.concurrency_limiter,
        )
        return cls(
            config=config,
            authenticator=TokenAuthenticator(
                config.token_file,
                config.client_token_path,
            ),
            workspace_files=workspace_files,
            asset_files=asset_files,
            docker_backend=docker_backend,
            admin_authenticator=TokenAuthenticator(
                config.admin_token_file,
                config.admin_client_token_path,
            ),
            quota_manager=quota_manager,
            lease_store=lease_store,
            lease_backend=lease_backend,
            lease_reconciler=LeaseReconciler(
                lease_backend,
                lease_store,
                interval_seconds=config.lease_reconcile_interval_seconds,
            ),
            process_manager=process_manager,
        )

    def ensure_lease_components(self) -> None:
        if self.lease_store is None:
            self.lease_store = LeaseStore(
                self.config.data_root / "runtime" / ".sandboxd-leases"
            )
        if self.lease_backend is None:
            resolver = getattr(
                self.docker_backend,
                "require_profile_image",
                None,
            )
            if not callable(resolver):
                def resolver(_profile_id: str):
                    raise SandboxServiceError(
                        SandboxErrorCode.RUNTIME_UNAVAILABLE,
                        "Sandbox Lease Profile 镜像校验器不可用",
                    )
            self.lease_backend = LeaseBackend(
                self.config,
                docker_client=self.docker_backend.client,
                workspace_files=self.workspace_files,
                asset_files=self.asset_files,
                lease_store=self.lease_store,
                profile_image_resolver=resolver,
                network_policy=getattr(
                    self.docker_backend,
                    "network_policy",
                    None,
                ),
            )
        if self.lease_reconciler is None:
            self.lease_reconciler = LeaseReconciler(
                self.lease_backend,
                self.lease_store,
                interval_seconds=(
                    self.config.lease_reconcile_interval_seconds
                ),
            )
        if self.process_manager is None:
            self.process_manager = LeaseProcessManager(
                self.config,
                lease_backend=self.lease_backend,
                lease_store=self.lease_store,
                concurrency_limiter=getattr(
                    self.docker_backend,
                    "concurrency_limiter",
                    None,
                ),
            )


def _error_status(error: SandboxServiceError) -> int:
    if error.code is SandboxErrorCode.AUTHORIZATION_FAILED:
        return 403
    if error.code in {
        SandboxErrorCode.SANDBOX_BUSY,
        SandboxErrorCode.DISK_PRESSURE,
    }:
        return 429
    if error.code in {
        SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
        SandboxErrorCode.RUNTIME_QUOTA_EXCEEDED,
    }:
        return 507
    if error.code is SandboxErrorCode.ASSET_TOO_LARGE:
        return 413
    if error.code is SandboxErrorCode.ASSET_NOT_FOUND:
        return 404
    if error.code is SandboxErrorCode.RUNTIME_UNAVAILABLE:
        return 503
    return 400


def _safe_run_status(value: dict[str, Any]) -> dict[str, Any]:
    sanitized = {
        key: value.get(key)
        for key in (
            "request_id",
            "run_id",
            "workspace_id",
            "image_digest",
            "status",
            "created_at_unix",
            "started_at_unix",
            "finished_at_unix",
            "error_code",
        )
        if key in value
    }
    data = value.get("data") if isinstance(value.get("data"), dict) else {}
    if data:
        stdout = str(data.get("stdout") or "").encode("utf-8", errors="replace")
        stderr = str(data.get("stderr") or "").encode("utf-8", errors="replace")
        sanitized["data"] = {
            key: data.get(key)
            for key in (
                "exit_code",
                "termination_reason",
                "oom_killed",
                "cpu_time_ms",
                "peak_memory_bytes",
                "stdout_bytes",
                "stderr_bytes",
                "stdout_truncated",
                "stderr_truncated",
            )
        }
        sanitized["data"]["stdout_sha256"] = hashlib.sha256(stdout).hexdigest()
        sanitized["data"]["stderr_sha256"] = hashlib.sha256(stderr).hexdigest()
    return sanitized


def create_app(runtime: SandboxRuntime | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        current = runtime
        if current is None:
            current = SandboxRuntime.build(SandboxdConfig.from_env())
        if (
            current.admin_authenticator is not None
            and secrets.compare_digest(
                current.authenticator.read_token(),
                current.admin_authenticator.read_token(),
            )
        ):
            raise RuntimeError("sandboxd 普通 Token 与管理 Token 必须不同")
        current.authenticator.prepare_client_token()
        if current.admin_authenticator is not None:
            current.admin_authenticator.prepare_client_token()
        current.workspace_files.layout.ensure_roots()
        current.ensure_lease_components()
        if (
            current.lease_store is None
            or current.lease_backend is None
            or current.lease_reconciler is None
            or current.process_manager is None
        ):
            raise RuntimeError("sandboxd Lease 控制面初始化失败")
        current.lease_store.start_controller(now_unix=time.time())
        recovery = current.lease_reconciler.recover_previous_controller()
        logger.info(
            "sandboxd controller epoch 已启动 epoch=%s recovered=%d failed=%d",
            recovery["controller_epoch"],
            len(recovery["recovered_lease_ids"]),
            len(recovery["failed_lease_ids"]),
        )
        try:
            result = OrphanReconciler(current.docker_backend.client).reconcile()
            logger.info(
                "sandboxd 启动孤儿回收完成 inspected=%d removed=%d",
                result["inspected"],
                result["removed"],
            )
        except Exception as exc:
            logger.error("sandboxd 启动孤儿回收失败 type=%s", type(exc).__name__)
        app.state.runtime = current
        current.workspace_files.start_usage_reconciler()
        current.lease_reconciler.start()
        try:
            yield
        finally:
            current.lease_reconciler.stop()
            try:
                current.lease_backend.terminate_all(
                    reason="controller_restarted",
                )
            except Exception:
                logger.error(
                    "sandboxd 停止时回收 Lease 失败",
                    exc_info=True,
                )
            current.process_manager.close()
            current.workspace_files.stop_usage_reconciler()

    app = FastAPI(
        title="Nanobot sandboxd",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.exception_handler(SandboxServiceError)
    async def sandbox_error_handler(_request, exc: SandboxServiceError):
        return JSONResponse(status_code=_error_status(exc), content=exc.to_result())

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request, _exc: RequestValidationError):
        error = SandboxServiceError(
            SandboxErrorCode.AUTHORIZATION_FAILED,
            "sandboxd 请求字段无效或包含未允许字段",
        )
        return JSONResponse(status_code=400, content=error.to_result())

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_request, exc: Exception):
        logger.error("sandboxd 未处理异常 type=%s", type(exc).__name__)
        error = SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "Sandbox 控制面暂时不可用",
            retryable=True,
            stop=False,
        )
        return JSONResponse(status_code=500, content=error.to_result())

    def authorized(
        authorization: Annotated[str | None, Header()] = None,
    ) -> SandboxRuntime:
        current: SandboxRuntime = app.state.runtime
        if not current.authenticator.verify_authorization(authorization):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "sandboxd 内部鉴权失败",
            )
        return current

    RuntimeDependency = Annotated[SandboxRuntime, Depends(authorized)]

    def admin_authorized(
        authorization: Annotated[str | None, Header()] = None,
    ) -> SandboxRuntime:
        current: SandboxRuntime = app.state.runtime
        authenticator = current.admin_authenticator
        if (
            authenticator is None
            or not authenticator.verify_authorization(authorization)
        ):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "sandboxd 管理接口鉴权失败",
            )
        return current

    AdminRuntimeDependency = Annotated[SandboxRuntime, Depends(admin_authorized)]

    @app.get("/v1/healthz")
    def healthz(_runtime: RuntimeDependency):
        return success_result("sandboxd 进程健康", data={"service": "sandboxd"})

    @app.get("/v1/readyz")
    def readyz(current: RuntimeDependency):
        if current.lease_store is None:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Lease 控制面不可用",
            )
        data = current.docker_backend.ready()
        data["controller_epoch"] = current.lease_store.controller_epoch
        return success_result(
            "sandboxd 已就绪",
            data=data,
        )

    @app.post("/v1/leases/ensure")
    def ensure_lease(
        body: LeaseEnsureRequest,
        current: RuntimeDependency,
        request_id: Annotated[
            str | None,
            Header(alias="X-Nanobot-Request-ID"),
        ] = None,
    ):
        if request_id != body.request_id or current.lease_backend is None:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "sandboxd Lease 请求幂等标识无效",
            )
        data = current.lease_backend.ensure(
            request_id=body.request_id,
            lease_id=body.lease_id,
            workspace_id=body.workspace_id,
            profile_id=body.profile_id,
            catalog_generation=body.catalog_generation,
            policy_sha256=body.policy_sha256,
            quota_generation=body.quota_generation,
        )
        return success_result("Sandbox Lease 已就绪", data=data)

    @app.get("/v1/leases/{lease_id}")
    def get_lease(lease_id: str, current: RuntimeDependency):
        if current.lease_backend is None:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Lease 控制面不可用",
            )
        return success_result(
            "Sandbox Lease 状态读取完成",
            data=current.lease_backend.get(lease_id),
        )

    @app.put("/v1/leases/{lease_id}/assets")
    def sync_lease_assets(
        lease_id: str,
        body: LeaseAssetStageRequest,
        current: RuntimeDependency,
        request_id: Annotated[
            str | None,
            Header(alias="X-Nanobot-Request-ID"),
        ] = None,
    ):
        if request_id != body.request_id or current.lease_backend is None:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "sandboxd Lease 资产请求幂等标识无效",
            )
        with current.workspace_files.maintenance.execution(
            body.workspace_id,
        ):
            fact = current.lease_backend.get(lease_id)
            if (
                fact.get("present") is not True
                or fact.get("running") is not True
                or str(fact.get("workspace_id") or "")
                != body.workspace_id
                or str(fact.get("controller_epoch") or "")
                != (
                    current.lease_store.controller_epoch
                    if current.lease_store is not None
                    else ""
                )
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.AUTHORIZATION_FAILED,
                    "Sandbox Lease 资产归属校验失败",
                )
            data = current.asset_files.sync_lease(
                body.workspace_id,
                lease_id,
                [asset.model_dump() for asset in body.assets],
            )
        return success_result("Sandbox Lease 授权资产已同步", data=data)

    @app.post("/v1/leases/{lease_id}/processes")
    def start_lease_process(
        lease_id: str,
        body: ProcessStartRequest,
        current: RuntimeDependency,
        request_id: Annotated[
            str | None,
            Header(alias="X-Nanobot-Request-ID"),
        ] = None,
    ):
        if (
            request_id != body.request_id
            or current.process_manager is None
        ):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "sandboxd 进程启动请求幂等标识无效",
            )
        data = current.process_manager.start(
            lease_id=lease_id,
            request_id=body.request_id,
            command=body.command,
            cwd=body.cwd,
            yield_time_ms=body.yield_time_ms,
            timeout_seconds=body.timeout_seconds,
        )
        return success_result("Sandbox Lease 进程已启动", data=data)

    @app.get("/v1/processes/{process_id}")
    def get_lease_process(
        process_id: str,
        current: RuntimeDependency,
        cursor: Annotated[str, Query(max_length=96)] = "",
    ):
        if current.process_manager is None:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 进程控制面不可用",
            )
        return success_result(
            "Sandbox 进程状态读取完成",
            data=current.process_manager.get(
                process_id,
                cursor=cursor,
            ),
        )

    @app.post("/v1/processes/{process_id}/stdin")
    def write_lease_process_stdin(
        process_id: str,
        body: ProcessStdinRequest,
        current: RuntimeDependency,
        request_id: Annotated[
            str | None,
            Header(alias="X-Nanobot-Request-ID"),
        ] = None,
    ):
        if (
            request_id != body.request_id
            or current.process_manager is None
        ):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "sandboxd stdin 请求幂等标识无效",
            )
        return success_result(
            "Sandbox stdin 已写入",
            data=current.process_manager.write_stdin(
                process_id,
                request_id=body.request_id,
                chars=body.chars,
            ),
        )

    @app.post("/v1/processes/{process_id}/terminate")
    def terminate_lease_process(
        process_id: str,
        body: LeaseActionRequest,
        current: RuntimeDependency,
        request_id: Annotated[
            str | None,
            Header(alias="X-Nanobot-Request-ID"),
        ] = None,
    ):
        if (
            request_id != body.request_id
            or current.process_manager is None
        ):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "sandboxd 进程终止请求幂等标识无效",
            )
        return success_result(
            "Sandbox Lease 进程终止请求已处理",
            data=current.process_manager.terminate(
                process_id,
                request_id=body.request_id,
            ),
        )

    @app.post("/v1/leases/{lease_id}/stop")
    def stop_lease(
        lease_id: str,
        body: LeaseActionRequest,
        current: RuntimeDependency,
        request_id: Annotated[
            str | None,
            Header(alias="X-Nanobot-Request-ID"),
        ] = None,
    ):
        if request_id != body.request_id or current.lease_backend is None:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "sandboxd Lease 停止请求幂等标识无效",
            )
        return success_result(
            "Sandbox Lease 已停止",
            data=current.lease_backend.recycle(
                lease_id,
                reason="lease_recycled",
            ),
        )

    @app.delete("/v1/leases/{lease_id}")
    def destroy_lease(
        lease_id: str,
        body: LeaseActionRequest,
        current: RuntimeDependency,
        request_id: Annotated[
            str | None,
            Header(alias="X-Nanobot-Request-ID"),
        ] = None,
    ):
        if request_id != body.request_id or current.lease_backend is None:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "sandboxd Lease 销毁请求幂等标识无效",
            )
        return success_result(
            "Sandbox Lease 已销毁",
            data=current.lease_backend.recycle(
                lease_id,
                reason="lease_recycled",
            ),
        )

    @app.get("/v1/admin/controller-state")
    def controller_state(current: AdminRuntimeDependency):
        if (
            current.lease_store is None
            or current.lease_backend is None
        ):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Lease 控制面不可用",
            )
        recovery = current.lease_store.startup_recovery()
        leases = current.lease_backend.list()
        return success_result(
            "sandboxd controller 状态读取完成",
            data={
                **recovery,
                "lease_count": len(
                    [item for item in leases if item.get("present")]
                ),
            },
        )

    @app.get("/v1/admin/leases")
    def list_leases(current: AdminRuntimeDependency):
        if current.lease_backend is None:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Lease 控制面不可用",
            )
        return success_result(
            "Sandbox Lease 列表读取完成",
            data={"leases": current.lease_backend.list()},
        )

    @app.post("/v1/admin/leases/{lease_id}/stop")
    def admin_stop_lease(
        lease_id: str,
        body: LeaseActionRequest,
        current: AdminRuntimeDependency,
        request_id: Annotated[
            str | None,
            Header(alias="X-Nanobot-Request-ID"),
        ] = None,
    ):
        if request_id != body.request_id or current.lease_backend is None:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "sandboxd 管理端 Lease 停止请求幂等标识无效",
            )
        return success_result(
            "Sandbox Lease 已由管理员停止",
            data=current.lease_backend.admin_recycle(
                lease_id,
                reason="admin_lease_stop",
            ),
        )

    @app.delete("/v1/admin/leases/{lease_id}")
    def admin_destroy_lease(
        lease_id: str,
        body: LeaseActionRequest,
        current: AdminRuntimeDependency,
        request_id: Annotated[
            str | None,
            Header(alias="X-Nanobot-Request-ID"),
        ] = None,
    ):
        if request_id != body.request_id or current.lease_backend is None:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "sandboxd 管理端 Lease 销毁请求幂等标识无效",
            )
        return success_result(
            "Sandbox Lease 已由管理员销毁",
            data=current.lease_backend.admin_recycle(
                lease_id,
                reason="admin_lease_destroy",
            ),
        )

    @app.post("/v1/admin/leases/{lease_id}/recreate")
    def admin_recreate_lease(
        lease_id: str,
        body: LeaseActionRequest,
        current: AdminRuntimeDependency,
        request_id: Annotated[
            str | None,
            Header(alias="X-Nanobot-Request-ID"),
        ] = None,
    ):
        if request_id != body.request_id or current.lease_backend is None:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "sandboxd 管理端 Lease 重建请求幂等标识无效",
            )
        return success_result(
            "Sandbox Lease 已由管理员重建",
            data=current.lease_backend.recreate(
                lease_id,
                request_id=body.request_id,
            ),
        )

    @app.get("/v1/admin/processes")
    def list_processes(current: AdminRuntimeDependency):
        if current.process_manager is None:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 进程控制面不可用",
            )
        return success_result(
            "Sandbox 进程事实读取完成",
            data={
                "controller_epoch": (
                    current.lease_store.controller_epoch
                    if current.lease_store is not None
                    else ""
                ),
                "processes": current.process_manager.list_facts(),
            },
        )

    @app.get("/v1/admin/workspace-usage")
    def admin_workspace_usage(current: AdminRuntimeDependency):
        ledger = current.workspace_files.usage_ledger
        facts: list[dict[str, Any]] = []
        for workspace_id in ledger.discover_workspace_ids():
            try:
                snapshot = ledger.snapshot(workspace_id)
            except SandboxServiceError:
                continue
            facts.append({
                "workspace_id": snapshot.workspace_id,
                "workspace_bytes": snapshot.workspace_bytes,
                "runtime_bytes": snapshot.runtime_bytes,
                "dirty": snapshot.dirty,
            })
        return success_result(
            "Workspace 用量事实读取完成",
            data={"workspaces": facts},
        )

    @app.post("/v1/admin/leases/reconcile")
    def reconcile_leases(
        body: LeaseActionRequest,
        current: AdminRuntimeDependency,
        request_id: Annotated[
            str | None,
            Header(alias="X-Nanobot-Request-ID"),
        ] = None,
    ):
        if (
            request_id != body.request_id
            or current.lease_reconciler is None
        ):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "sandboxd Lease 对账请求幂等标识无效",
            )
        return success_result(
            "Sandbox Lease 对账完成",
            data=current.lease_reconciler.reconcile(),
        )

    @app.post("/v1/admin/leases/terminate-all")
    def terminate_all_leases(
        body: TerminateAllRequest,
        current: AdminRuntimeDependency,
        request_id: Annotated[
            str | None,
            Header(alias="X-Nanobot-Request-ID"),
        ] = None,
    ):
        if request_id != body.request_id or current.lease_backend is None:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "sandboxd Lease 全量终止请求幂等标识无效",
            )
        return success_result(
            "Sandbox Lease 全量终止完成",
            data=current.lease_backend.terminate_all(reason=body.reason),
        )

    @app.post("/v1/workspaces/ensure")
    def ensure_workspace(body: WorkspaceRequest, current: RuntimeDependency):
        data = current.workspace_files.ensure_workspace(body.workspace_id)
        return success_result("Workspace 目录已就绪", data=data)

    @app.post("/v1/admin/workspaces/ensure")
    def admin_ensure_workspace(
        body: WorkspaceRequest,
        current: AdminRuntimeDependency,
    ):
        data = current.workspace_files.ensure_workspace(body.workspace_id)
        return success_result("Workspace 目录已就绪", data=data)

    @app.post("/v1/admin/workspaces/quota/apply")
    def apply_workspace_quota(
        body: WorkspaceQuotaRequest,
        current: AdminRuntimeDependency,
        request_id: Annotated[
            str | None,
            Header(alias="X-Nanobot-Request-ID"),
        ] = None,
    ):
        if (
            request_id != body.request_id
            or current.quota_manager is None
            or current.lease_backend is None
        ):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "sandboxd quota 请求幂等标识无效",
            )
        with current.workspace_files.maintenance.quota_maintenance(
            body.workspace_id,
            generation=body.generation,
        ):
            terminated = current.lease_backend.terminate_workspace(
                body.workspace_id,
                reason="quota_reconfigured",
            )
            workspace_data = current.quota_manager.apply(
                workspace_id=body.workspace_id,
                project_id=body.project_id,
                quota_bytes=body.quota_bytes,
                generation=body.generation,
                scope="workspace",
            )
            workspace_check = current.quota_manager.inspect(
                workspace_id=body.workspace_id,
                project_id=body.project_id,
                quota_bytes=body.quota_bytes,
                generation=body.generation,
                scope="workspace",
            )
            runtime_data = current.quota_manager.apply(
                workspace_id=body.workspace_id,
                project_id=body.runtime_project_id,
                quota_bytes=body.runtime_quota_bytes,
                generation=body.generation,
                scope="runtime",
            )
            runtime_check = current.quota_manager.inspect(
                workspace_id=body.workspace_id,
                project_id=body.runtime_project_id,
                quota_bytes=body.runtime_quota_bytes,
                generation=body.generation,
                scope="runtime",
            )
            if not (
                workspace_data.get("applied") is True
                and workspace_check.get("project_id_matches") is True
                and workspace_check.get("quota_bytes_matches") is True
                and runtime_data.get("applied") is True
                and runtime_check.get("project_id_matches") is True
                and runtime_check.get("quota_bytes_matches") is True
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "Workspace 或 Runtime 硬配额验证失败",
                    retryable=True,
                    stop=False,
                )
        data = {
            "workspace_id": body.workspace_id,
            "project_id": body.project_id,
            "quota_bytes": body.quota_bytes,
            "runtime_project_id": body.runtime_project_id,
            "runtime_quota_bytes": body.runtime_quota_bytes,
            "generation": body.generation,
            "workspace_used_bytes": int(
                workspace_data.get("used_bytes") or 0
            ),
            "runtime_used_bytes": int(runtime_data.get("used_bytes") or 0),
            "terminated_lease_ids": list(
                terminated["terminated_lease_ids"]
            ),
            "affected_process_ids": list(
                terminated["affected_process_ids"]
            ),
            "failed_lease_ids": [],
            "workspace_project_id_matches": True,
            "workspace_quota_bytes_matches": True,
            "runtime_project_id_matches": True,
            "runtime_quota_bytes_matches": True,
            "quota_verified": True,
            "applied": True,
        }
        return success_result("Workspace project quota 已应用", data=data)

    @app.post("/v1/admin/workspaces/quota/inspect")
    def inspect_workspace_quota(
        body: WorkspaceQuotaRequest,
        current: AdminRuntimeDependency,
        request_id: Annotated[
            str | None,
            Header(alias="X-Nanobot-Request-ID"),
        ] = None,
    ):
        if request_id != body.request_id or current.quota_manager is None:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "sandboxd quota 请求幂等标识无效",
            )
        workspace_data = current.quota_manager.inspect(
            workspace_id=body.workspace_id,
            project_id=body.project_id,
            quota_bytes=body.quota_bytes,
            generation=body.generation,
            scope="workspace",
        )
        runtime_data = current.quota_manager.inspect(
            workspace_id=body.workspace_id,
            project_id=body.runtime_project_id,
            quota_bytes=body.runtime_quota_bytes,
            generation=body.generation,
            scope="runtime",
        )
        data = {
            "workspace_id": body.workspace_id,
            "project_id": body.project_id,
            "quota_bytes": body.quota_bytes,
            "runtime_project_id": body.runtime_project_id,
            "runtime_quota_bytes": body.runtime_quota_bytes,
            "generation": body.generation,
            "workspace_used_bytes": int(
                workspace_data.get("used_bytes") or 0
            ),
            "runtime_used_bytes": int(runtime_data.get("used_bytes") or 0),
            "workspace_project_id_matches": (
                workspace_data.get("project_id_matches") is True
            ),
            "workspace_quota_bytes_matches": (
                workspace_data.get("quota_bytes_matches") is True
            ),
            "runtime_project_id_matches": (
                runtime_data.get("project_id_matches") is True
            ),
            "runtime_quota_bytes_matches": (
                runtime_data.get("quota_bytes_matches") is True
            ),
            "quota_verified": (
                workspace_data.get("verified") is True
                and runtime_data.get("verified") is True
            ),
        }
        return success_result("Workspace project quota 检查完成", data=data)

    @app.post("/v1/files/list")
    def list_files(body: FileListRequest, current: RuntimeDependency):
        data = current.workspace_files.list_files(
            body.workspace_id,
            path=body.path,
            cwd=body.cwd,
            cursor=body.cursor,
            limit=body.limit,
        )
        return success_result("目录读取完成", data=data)

    @app.post("/v1/files/read")
    def read_file(body: FileReadRequest, current: RuntimeDependency):
        data = current.workspace_files.read_file(
            body.workspace_id,
            path=body.path,
            cwd=body.cwd,
            offset=body.offset,
            limit=body.limit,
        )
        return success_result("文件读取完成", data=data)

    @app.post("/v1/files/read-text")
    def read_text_file(body: FileReadTextRequest, current: RuntimeDependency):
        data = current.workspace_files.read_text_file(
            body.workspace_id,
            path=body.path,
            cwd=body.cwd,
        )
        return success_result("文本文件读取完成", data=data)

    @app.post("/v1/files/search")
    def search_files(body: FileSearchRequest, current: RuntimeDependency):
        data = current.workspace_files.search_files(
            body.workspace_id,
            mode=body.mode,
            pattern=body.pattern or body.query,
            path=body.path,
            glob=body.glob,
            limit=body.limit,
            ignore_case=body.ignore_case,
            max_depth=body.max_depth,
            cursor=body.cursor,
            cwd=body.cwd,
        )
        return success_result("工作区搜索完成", data=data)

    @app.post("/v1/files/write")
    def write_file(body: FileWriteRequest, current: RuntimeDependency):
        data = current.workspace_files.write_file(
            body.workspace_id,
            path=body.path,
            cwd=body.cwd,
            content=body.content,
            overwrite=body.overwrite,
            expected_sha256=body.expected_sha256,
            quota_bytes=body.quota_bytes,
        )
        return success_result(
            "文件写入完成",
            data=data,
            artifacts=[{
                "type": "workspace_file",
                "ref": f"workspace://current/{data['path']}",
                "path": data["path"],
                "size_bytes": data["size_bytes"],
            }],
        )

    @app.post("/v1/files/edit")
    def edit_files(body: FileEditRequest, current: RuntimeDependency):
        data = current.workspace_files.edit_files(
            body.workspace_id,
            operations=[
                operation.model_dump(exclude_none=True)
                for operation in body.operations
            ],
            cwd=body.cwd,
            quota_bytes=body.quota_bytes,
        )
        return success_result(
            "Workspace 编辑完成",
            data=data,
            artifacts=[
                {
                    "type": "workspace_file",
                    "ref": f"workspace://current/{item['path']}",
                    "path": item["path"],
                    "size_bytes": item["size_bytes"],
                }
                for item in data["files"]
            ],
        )

    @app.post("/v1/files/apply-patch")
    def apply_patch(body: FilePatchRequest, current: RuntimeDependency):
        data = current.workspace_files.apply_patch(
            body.workspace_id,
            path=body.path,
            patch=body.patch,
            quota_bytes=body.quota_bytes,
        )
        return success_result(
            "Workspace 补丁应用完成",
            data=data,
            artifacts=[{
                "type": "workspace_file",
                "ref": f"workspace://current/{data['path']}",
                "path": data["path"],
                "size_bytes": data["size_bytes"],
            }],
        )

    @app.post("/v1/assets/publish")
    def publish_asset(body: AssetPublishRequest, current: RuntimeDependency):
        data = current.asset_files.publish(
            body.workspace_id,
            path=body.path,
            media_type=body.media_type,
        )
        return success_result("资产发布完成", data=data)

    @app.post("/v1/assets/materialize")
    def materialize_asset(
        body: AssetMaterializeRequest,
        current: RuntimeDependency,
    ):
        data = current.workspace_files.materialize_asset(
            body.workspace_id,
            sha256=body.sha256,
            storage_key=body.storage_key,
            path=body.path,
            quota_bytes=body.quota_bytes,
            overwrite=body.overwrite,
        )
        return success_result(
            "暂存内容已写入 owner Workspace",
            data=data,
            artifacts=[{
                "type": "workspace_file",
                "ref": f"workspace://current/{data['path']}",
                "path": data["path"],
                "size_bytes": data["size_bytes"],
            }],
        )

    @app.post("/v1/assets/upload")
    async def upload_asset(
        request: Request,
        current: RuntimeDependency,
        workspace_id: Annotated[str, Query(min_length=36, max_length=36)],
        media_type: Annotated[str, Query(max_length=255)] = "application/octet-stream",
        content_length: Annotated[int | None, Header(ge=0)] = None,
    ):
        validate_workspace_id(workspace_id)
        if content_length is not None and content_length > current.config.asset_max_bytes:
            raise SandboxServiceError(
                SandboxErrorCode.ASSET_TOO_LARGE,
                "资产超过允许的单文件大小上限",
            )
        writer = current.asset_files.open_upload(media_type=media_type)
        next_disk_check = 8 * 1024 * 1024
        try:
            async for chunk in request.stream():
                if writer.total + len(chunk) >= next_disk_check:
                    current.asset_files.disk_guard.ensure_available(
                        additional_bytes=len(chunk),
                    )
                writer.write(bytes(chunk))
                if writer.total >= next_disk_check:
                    next_disk_check = writer.total + 8 * 1024 * 1024
            if content_length is not None and writer.total != content_length:
                raise SandboxServiceError(
                    SandboxErrorCode.AUTHORIZATION_FAILED,
                    "资产上传长度与请求声明不一致",
                )
            published = writer.finish()
        except BaseException:
            writer.abort()
            raise
        return success_result(
            "资产流式上传完成",
            data={"workspace_id": workspace_id, **asdict(published)},
        )

    @app.post("/v1/assets/stage")
    def stage_assets(body: AssetStageRequest, current: RuntimeDependency):
        data = current.asset_files.stage(
            body.workspace_id,
            body.run_id,
            [asset.model_dump() for asset in body.assets],
        )
        return success_result("运行输入资产已准备", data=data)

    @app.get("/v1/assets/{sha256}")
    def download_asset(
        sha256: str,
        current: RuntimeDependency,
        range_header: Annotated[str, Header(alias="Range")] = "",
    ):
        digest = validate_sha256(sha256)
        storage_key = current.asset_files.layout.asset_storage_key(digest)
        filesystem = SafeWorkspaceFilesystem(current.asset_files.layout.assets_root)
        try:
            size_bytes = filesystem.regular_file_size(storage_key)
        except SandboxServiceError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.ASSET_NOT_FOUND,
                "资产不存在",
            ) from exc
        try:
            start, end, partial = _byte_range(range_header, size_bytes)
        except RangeNotSatisfiable:
            return StreamingResponse(
                iter(()),
                status_code=416,
                headers={
                    "Accept-Ranges": "bytes",
                    "Content-Range": f"bytes */{size_bytes}",
                    "Content-Length": "0",
                },
                media_type="application/octet-stream",
            )

        def body_iterator():
            with filesystem.open_regular_file(storage_key) as (file_fd, current_size):
                if current_size != size_bytes:
                    raise SandboxServiceError(
                        SandboxErrorCode.RUNTIME_UNAVAILABLE,
                        "资产存储暂时不可用",
                    )
                position = start
                remaining = max(0, end - start + 1)
                while remaining:
                    chunk = os.pread(file_fd, min(1024 * 1024, remaining), position)
                    if not chunk:
                        break
                    position += len(chunk)
                    remaining -= len(chunk)
                    yield chunk

        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(max(0, end - start + 1)),
        }
        if partial:
            headers["Content-Range"] = f"bytes {start}-{end}/{size_bytes}"
        return StreamingResponse(
            body_iterator(),
            status_code=206 if partial else 200,
            headers=headers,
            media_type="application/octet-stream",
        )

    @app.post("/v1/runs")
    def create_run(body: RunRequest, current: RuntimeDependency):
        with current.workspace_files.maintenance.execution(
            body.workspace_id,
        ):
            result = current.docker_backend.execute(
                request_id=body.request_id,
                run_id=body.run_id,
                workspace_id=body.workspace_id,
                command=body.command,
                cwd=body.cwd,
                timeout_seconds=body.timeout_seconds,
                quota_bytes=body.quota_bytes,
            )
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        reason = str(data.get("termination_reason") or result.get("error_code") or "")
        if reason == "execution_timeout":
            raise SandboxServiceError(
                SandboxErrorCode.EXECUTION_TIMEOUT,
                "Sandbox 执行超时，整个容器已终止",
            )
        if reason == "output_limit_exceeded":
            raise SandboxServiceError(
                SandboxErrorCode.OUTPUT_LIMIT_EXCEEDED,
                "Sandbox 输出超过硬上限，容器已终止",
            )
        if reason == "process_oom_killed":
            raise SandboxServiceError(
                SandboxErrorCode.PROCESS_OOM_KILLED,
                "Sandbox 进程超过内存上限并被终止",
            )
        if reason == "workspace_quota_exceeded":
            raise SandboxServiceError(
                SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
                "Sandbox 写入超过空间配额，容器已终止",
            )
        if reason == "runtime_quota_exceeded":
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_QUOTA_EXCEEDED,
                "Sandbox Runtime 缓存已达到硬配额，容器已终止",
            )
        if result.get("status") == "failed" and not data:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 运行失败",
                retryable=True,
                stop=False,
            )
        return success_result(
            "Sandbox 执行完成",
            data={
                "run_id": body.run_id,
                "image_digest": str(result.get("image_digest") or ""),
                **data,
            },
        )

    @app.post("/v1/runs/{run_id}/cancel")
    def cancel_run(
        run_id: str,
        _body: EmptyRequest,
        current: RuntimeDependency,
    ):
        return success_result(
            "Sandbox 取消请求已处理",
            data=_safe_run_status(current.docker_backend.cancel(run_id)),
        )

    @app.get("/v1/runs/{run_id}")
    def get_run(run_id: str, current: RuntimeDependency):
        return success_result(
            "Sandbox 运行状态读取完成",
            data=_safe_run_status(current.docker_backend.get(run_id)),
        )

    return app


app = create_app()


def _open_secure_uds(socket_path: Path) -> socket.socket:
    socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    try:
        metadata = socket_path.lstat()
    except FileNotFoundError:
        metadata = None
    if metadata is not None:
        if not stat.S_ISSOCK(metadata.st_mode):
            raise RuntimeError("sandboxd Socket 路径已被非 Socket 文件占用")
        socket_path.unlink()

    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    previous_umask = os.umask(0o117)
    try:
        listener.bind(str(socket_path))
    except Exception:
        listener.close()
        raise
    finally:
        os.umask(previous_umask)

    os.chmod(socket_path, 0o660)
    metadata = socket_path.lstat()
    if (
        not stat.S_ISSOCK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o660
    ):
        listener.close()
        raise RuntimeError("sandboxd Socket 权限初始化失败")
    return listener


def main() -> None:
    import uvicorn

    config = SandboxdConfig.from_env()
    listener = _open_secure_uds(config.socket_path)
    try:
        uvicorn.run(
            "sandboxd.app:app",
            fd=listener.fileno(),
            workers=1,
            log_level=os.environ.get("LOG_LEVEL", "info").lower(),
            access_log=False,
        )
    finally:
        listener.close()
        try:
            metadata = config.socket_path.lstat()
        except FileNotFoundError:
            metadata = None
        if metadata is not None and stat.S_ISSOCK(metadata.st_mode):
            config.socket_path.unlink()


if __name__ == "__main__":
    main()
