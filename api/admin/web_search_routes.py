"""Admin Web Search provider 配置 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.admin.common import audit, client_ip, verify_admin
from core.database import get_db
from core.web_search.provider_catalog import get_provider_catalog, list_provider_catalog
from core.web_search.provider_settings import (
    ProviderResolvedConfig,
    resolve_provider_config,
    update_provider_config,
)
from core.web_search.provider_tests import test_provider
from core.web_search.search_runtime import (
    WebSearchError,
    format_provider_result_for_model,
    search_enabled_providers,
)
from core.web_search.usage_stats import get_provider_usage


router = APIRouter(prefix="/web-search", tags=["admin-web-search"])


class ProviderUpdateRequest(BaseModel):
    enabled: bool | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    base_url: str | None = None


class ProviderTestRequest(BaseModel):
    query: str = "nanobot"


class ProviderPreviewRequest(BaseModel):
    query: str
    limit: int = 5
    provider: str = ""


def _provider_payload(db: Session, provider_id: str) -> dict[str, Any]:
    item = get_provider_catalog(provider_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown web search provider")
    config = resolve_provider_config(db, provider_id)
    data = item.to_dict()
    data.update(config.public_dict())
    data["last_test"] = None
    data["usage"] = get_provider_usage(db, provider_id)
    return data


def _provider_config_payload(config: ProviderResolvedConfig) -> dict[str, Any]:
    return config.public_dict()


@router.get("/providers")
def list_web_search_providers(
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    return {"providers": [_provider_payload(db, item.id) for item in list_provider_catalog()]}


@router.put("/providers/{provider_id}")
def update_web_search_provider(
    provider_id: str,
    body: ProviderUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    if hasattr(body, "model_dump"):
        payload = body.model_dump(exclude_unset=True)
    else:
        payload = body.dict(exclude_unset=True)
    config = update_provider_config(db, provider_id, payload)
    audit(
        db,
        "update_web_search_provider",
        "web_search_provider",
        provider_id,
        {
            "enabled_changed": "enabled" in payload,
            "base_url_changed": "base_url" in payload,
            "api_key_changed": bool(payload.get("api_key")),
            "clear_api_key": bool(payload.get("clear_api_key")),
        },
        ip_address=client_ip(request),
    )
    return {"provider": _provider_config_payload(config)}


@router.post("/providers/{provider_id}/test")
async def test_web_search_provider(
    provider_id: str,
    body: ProviderTestRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    if get_provider_catalog(provider_id) is None:
        raise HTTPException(status_code=404, detail="Unknown web search provider")
    config = resolve_provider_config(db, provider_id)
    query = (body.query or "nanobot").strip() or "nanobot"
    result = await test_provider(provider_id, config, query, db=db)
    return result.to_dict()


@router.post("/preview")
async def preview_web_search(
    body: ProviderPreviewRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(status_code=422, detail="query is required")
    try:
        limit = max(1, min(int(body.limit or 5), 10))
    except (TypeError, ValueError):
        limit = 5
    provider = (body.provider or "").strip()

    try:
        result = await search_enabled_providers(
            db,
            query=query,
            limit=limit,
            provider_id=provider,
        )
    except WebSearchError as exc:
        return {
            "ok": False,
            "provider_id": exc.provider_id,
            "error_code": exc.error_code,
            "message": exc.message,
        }

    data = result.to_dict()
    data["ok"] = True
    data["message"] = format_provider_result_for_model(query, result, limit=limit)
    return data
