"""普通 API 共享鉴权依赖。"""

from __future__ import annotations

import sys
from hmac import compare_digest

from fastapi import Header, HTTPException

from config import NANOBOT_API_TOKEN as CONFIG_API_TOKEN


def _current_api_token() -> str:
    routes = sys.modules.get("api.routes")
    if routes is not None and hasattr(routes, "NANOBOT_API_TOKEN"):
        return str(getattr(routes, "NANOBOT_API_TOKEN") or "")
    return str(CONFIG_API_TOKEN or "")


def verify_token(authorization: str = Header(default="")) -> None:
    token_config = _current_api_token()
    if not token_config:
        raise HTTPException(status_code=503, detail="API token not configured")
    token = authorization.replace("Bearer ", "").strip()
    if not token or not compare_digest(token, token_config):
        raise HTTPException(status_code=401, detail="Invalid or missing API token")
