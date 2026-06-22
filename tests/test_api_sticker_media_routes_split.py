from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


_STICKER_MEDIA_ROUTE_SIGNATURES = (
    ("POST", "/api/v1/stickers/register"),
    ("GET", "/api/v1/stickers/search"),
    ("GET", "/api/v1/stickers/{sticker_id}/image"),
    ("GET", "/api/v1/generated-images/{image_id}/image"),
    ("POST", "/api/v1/stickers/{sticker_id}/disable"),
)

_STICKER_MEDIA_ROUTE_EXPORTS = (
    "StickerRegisterRequest",
    "register_sticker_endpoint",
    "search_sticker_endpoint",
    "public_sticker_image",
    "public_generated_image",
    "disable_sticker_endpoint",
)


def _api_route_entries():
    from server import app

    def _iter_routes(routes, prefix: str = ""):
        for route in routes:
            endpoint = getattr(route, "endpoint", None)
            route_path = getattr(route, "path", None)
            if endpoint is not None and route_path is not None:
                yield prefix + route_path, route
                continue

            original_router = getattr(route, "original_router", None)
            if original_router is None:
                continue
            include_context = getattr(route, "include_context", None)
            include_prefix = getattr(include_context, "prefix", "")
            yield from _iter_routes(original_router.routes, prefix + include_prefix)

    return list(_iter_routes(app.routes))


def _api_routes_for(path: str, method: str | None = None):
    return [
        route
        for route_path, route in _api_route_entries()
        if route_path == path and (method is None or method in getattr(route, "methods", set()))
    ]


def _route_index(path: str, method: str) -> int:
    for index, (route_path, route) in enumerate(_api_route_entries()):
        if route_path == path and method in getattr(route, "methods", set()):
            return index
    raise AssertionError(f"missing route: {method} {path}")


def test_api_sticker_media_routes_are_registered_from_split_module():
    for method, path in _STICKER_MEDIA_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.sticker_media_routes"}


def test_legacy_api_routes_sticker_media_imports_still_work():
    from api import routes
    from api import sticker_media_routes

    for name in _STICKER_MEDIA_ROUTE_EXPORTS:
        assert getattr(routes, name) is getattr(sticker_media_routes, name)

    body = routes.StickerRegisterRequest(
        chat_stream_id="123",
        file_ref="https://example.com/a.png",
        sticker_hash="hash-a",
    )
    assert body.chat_stream_id == "123"
    assert body.source_type == "manual"
    assert body.status == "active"


def test_split_sticker_media_routes_use_legacy_api_token_monkeypatch(monkeypatch):
    from server import app

    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")

    with TestClient(app) as test_client:
        ok = test_client.get(
            "/api/v1/stickers/search?query=hi",
            headers={"Authorization": "Bearer split-token"},
        )
        wrong = test_client.get(
            "/api/v1/stickers/search?query=hi",
            headers={"Authorization": "Bearer wrong"},
        )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_api_sticker_media_routes_are_not_registered_twice():
    for method, path in _STICKER_MEDIA_ROUTE_SIGNATURES:
        assert len(_api_routes_for(path, method)) == 1, f"{method} {path}"


def test_api_sticker_media_routes_do_not_import_parent_routes_or_sync_awaitable():
    source = Path("api/sticker_media_routes.py").read_text(encoding="utf-8")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable():
    for path in (
        "api/chat_content_helpers.py",
        "api/chat_response_contract.py",
        "api/chat_persistence.py",
        "api/chat_request_contract.py",
    ):
        source = Path(path).read_text(encoding="utf-8")

        assert "from api.routes" not in source
        assert "import api.routes" not in source
        assert "asyncio.run" not in source
        assert "run_awaitable_sync" not in source


def test_sticker_collection_routes_precede_dynamic_sticker_routes():
    register_index = _route_index("/api/v1/stickers/register", "POST")
    search_index = _route_index("/api/v1/stickers/search", "GET")
    image_index = _route_index("/api/v1/stickers/{sticker_id}/image", "GET")
    disable_index = _route_index("/api/v1/stickers/{sticker_id}/disable", "POST")

    assert register_index < image_index
    assert search_index < image_index
    assert register_index < disable_index
    assert search_index < disable_index


def test_public_sticker_image_keeps_env_token_boundary(monkeypatch):
    from server import app

    monkeypatch.setenv("NANOBOT_STICKER_IMAGE_TOKEN", "image-token")

    with TestClient(app) as test_client:
        wrong = test_client.get("/api/v1/stickers/999999/image?token=wrong")
        ok_missing = test_client.get("/api/v1/stickers/999999/image?token=image-token")

    assert wrong.status_code == 403
    assert ok_missing.status_code == 404


def test_public_generated_image_keeps_env_token_boundary(monkeypatch):
    from server import app

    monkeypatch.setenv("NANOBOT_GENERATED_IMAGE_TOKEN", "image-token")

    with TestClient(app) as test_client:
        wrong = test_client.get("/api/v1/generated-images/not-present/image?token=wrong")
        ok_missing = test_client.get(
            "/api/v1/generated-images/not-present/image?token=image-token"
        )

    assert wrong.status_code == 403
    assert ok_missing.status_code == 404


def test_chat_and_group_boundaries_stay_in_parent_routes():
    from api import group_message_routes
    from api import routes

    assert routes.proxy_chat.__module__ == "api.routes"
    assert routes.group_message is group_message_routes.group_message
    assert routes._persist_chat_turn.__module__ == "api.routes"
    assert routes._safe_meta.__module__ == "api.routes"
    assert routes._normalize_files.__module__ == "api.routes"
    assert routes._schedule_image_precache.__module__ == "api.routes"
    assert routes._build_multimodal_user_input_text.__module__ == "api.routes"
    assert routes._build_chatlog_user_content.__module__ == "api.routes"
    assert routes._build_conversation_user_content.__module__ == "api.routes"


def test_group_sticker_helpers_stay_group_ingress_facades():
    from api import routes
    from app.group_ingress import helpers

    assert routes._group_sticker_payloads is helpers.group_sticker_payloads
    assert (
        routes._register_group_stickers_from_message
        is helpers.register_group_stickers_from_message
    )
