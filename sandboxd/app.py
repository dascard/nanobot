"""只监听 Unix Domain Socket 的 sandboxd FastAPI 控制面。"""

import hashlib
import logging
import os
import secrets
import stat
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from core.sandbox.contracts import (
    SandboxErrorCode,
    SandboxServiceError,
    success_result,
)
from sandboxd.auth import TokenAuthenticator
from sandboxd.config import SandboxdConfig
from sandboxd.docker_backend import LocalDockerBackend
from sandboxd.filesystem import AssetFileService, WorkspaceFileService
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
    cursor: str = Field(default="", max_length=64)
    limit: int = Field(default=100, ge=1, le=200)


class FileReadRequest(WorkspaceRequest):
    path: str = Field(min_length=1, max_length=4096)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=64 * 1024, ge=1, le=256 * 1024)


class FileSearchRequest(WorkspaceRequest):
    query: str = Field(min_length=1, max_length=1024)
    path: str = Field(default="", max_length=4096)
    glob: str = Field(default="", max_length=512)
    limit: int = Field(default=50, ge=1, le=200)


class FileWriteRequest(WorkspaceRequest):
    path: str = Field(min_length=1, max_length=4096)
    content: str = Field(max_length=256 * 1024)
    overwrite: bool = False
    quota_bytes: int = Field(gt=0, le=1024 * 1024 * 1024 * 1024)


class AssetPublishRequest(WorkspaceRequest):
    path: str = Field(min_length=1, max_length=4096)
    media_type: str = Field(default="application/octet-stream", max_length=255)


class StagedAsset(StrictModel):
    sha256: str = Field(min_length=64, max_length=64)
    storage_key: str = Field(min_length=1, max_length=255)
    logical_name: str = Field(min_length=1, max_length=512)


class AssetStageRequest(WorkspaceRequest):
    run_id: str = Field(min_length=8, max_length=64)
    assets: list[StagedAsset] = Field(default_factory=list, max_length=100)


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
    generation: int = Field(ge=1, le=2_147_483_647)


@dataclass
class SandboxRuntime:
    config: SandboxdConfig
    authenticator: TokenAuthenticator
    workspace_files: WorkspaceFileService
    asset_files: AssetFileService
    docker_backend: LocalDockerBackend
    admin_authenticator: TokenAuthenticator | None = None
    quota_manager: ProjectQuotaManager | None = None

    @classmethod
    def build(cls, config: SandboxdConfig) -> "SandboxRuntime":
        workspace_files = WorkspaceFileService(config)
        asset_files = AssetFileService(config)
        return cls(
            config=config,
            authenticator=TokenAuthenticator(
                config.token_file,
                config.client_token_path,
            ),
            workspace_files=workspace_files,
            asset_files=asset_files,
            docker_backend=LocalDockerBackend(
                config,
                workspace_files=workspace_files,
                asset_files=asset_files,
            ),
            admin_authenticator=TokenAuthenticator(
                config.admin_token_file,
                config.admin_client_token_path,
            ),
            quota_manager=ProjectQuotaManager(
                data_root=config.data_root,
                helper_path=config.quota_helper_path,
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
        yield

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
        return success_result(
            "sandboxd 已就绪",
            data=current.docker_backend.ready(),
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
        if request_id != body.request_id or current.quota_manager is None:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "sandboxd quota 请求幂等标识无效",
            )
        data = current.quota_manager.apply(
            workspace_id=body.workspace_id,
            project_id=body.project_id,
            quota_bytes=body.quota_bytes,
            generation=body.generation,
        )
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
        data = current.quota_manager.inspect(
            workspace_id=body.workspace_id,
            project_id=body.project_id,
            quota_bytes=body.quota_bytes,
            generation=body.generation,
        )
        return success_result("Workspace project quota 检查完成", data=data)

    @app.post("/v1/files/list")
    def list_files(body: FileListRequest, current: RuntimeDependency):
        data = current.workspace_files.list_files(
            body.workspace_id,
            path=body.path,
            cursor=body.cursor,
            limit=body.limit,
        )
        return success_result("目录读取完成", data=data)

    @app.post("/v1/files/read")
    def read_file(body: FileReadRequest, current: RuntimeDependency):
        data = current.workspace_files.read_file(
            body.workspace_id,
            path=body.path,
            offset=body.offset,
            limit=body.limit,
        )
        return success_result("文件读取完成", data=data)

    @app.post("/v1/files/search")
    def search_files(body: FileSearchRequest, current: RuntimeDependency):
        data = current.workspace_files.search_files(
            body.workspace_id,
            query=body.query,
            path=body.path,
            glob=body.glob,
            limit=body.limit,
        )
        return success_result("工作区搜索完成", data=data)

    @app.post("/v1/files/write")
    def write_file(body: FileWriteRequest, current: RuntimeDependency):
        data = current.workspace_files.write_file(
            body.workspace_id,
            path=body.path,
            content=body.content,
            overwrite=body.overwrite,
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

    @app.post("/v1/assets/publish")
    def publish_asset(body: AssetPublishRequest, current: RuntimeDependency):
        data = current.asset_files.publish(
            body.workspace_id,
            path=body.path,
            media_type=body.media_type,
        )
        return success_result("资产发布完成", data=data)

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
        if reason in {"workspace_quota_exceeded", "runtime_quota_exceeded"}:
            raise SandboxServiceError(
                SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
                "Sandbox 写入超过空间配额，容器已终止",
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


def main() -> None:
    import uvicorn

    config = SandboxdConfig.from_env()
    config.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    try:
        metadata = config.socket_path.lstat()
    except FileNotFoundError:
        metadata = None
    if metadata is not None:
        if not stat.S_ISSOCK(metadata.st_mode):
            raise RuntimeError("sandboxd Socket 路径已被非 Socket 文件占用")
        config.socket_path.unlink()
    os.umask(0o117)
    uvicorn.run(
        "sandboxd.app:app",
        uds=str(config.socket_path),
        workers=1,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
