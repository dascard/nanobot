"""FastAPI 应用入口点。"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.admin.endpoint_registry import ADMIN_ENDPOINT_CONTRACT_REGISTRY
from api.admin_routes import router as admin_router
from api.endpoint_contracts import (
    install_openapi_contracts,
    stable_operation_id,
)
from api.routes import router as api_router
from api.telemetry_middleware import TelemetryHttpMiddleware
from bootstrap.lifespan import lifespan
from bootstrap.logging_config import configure_logging
from config import get_cors_origins


configure_logging()


app = FastAPI(
    title="Nanobot Self-Evolution Gateway",
    version="1.0.0",
    lifespan=lifespan,
    generate_unique_id_function=stable_operation_id,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(TelemetryHttpMiddleware)

app.include_router(api_router)
app.include_router(admin_router)
install_openapi_contracts(
    app,
    ADMIN_ENDPOINT_CONTRACT_REGISTRY.registry_snapshot,
)


class SPAStaticFiles(StaticFiles):
    """为 WebUI 的前端路由提供 index.html 回退，同时保留 API 404。"""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or self._should_keep_not_found(path):
                raise
            return await super().get_response("index.html", scope)

    @staticmethod
    def _should_keep_not_found(path: str) -> bool:
        if path.startswith(("api/", "docs", "redoc", "openapi.json")):
            return True
        return bool(Path(path).suffix)


_webui_dist = Path(__file__).parent / "webui" / "dist"
if _webui_dist.exists():
    app.mount("/", SPAStaticFiles(directory=str(_webui_dist), html=True), name="webui")
