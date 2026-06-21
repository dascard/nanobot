_ADMIN_STICKER_ROUTE_SIGNATURES = (
    ("POST", "/api/v1/admin/stickers"),
    ("GET", "/api/v1/admin/stickers"),
    ("GET", "/api/v1/admin/generated-images"),
    ("POST", "/api/v1/admin/generated-images"),
    ("GET", "/api/v1/admin/generated-images/{image_id}/image"),
    ("GET", "/api/v1/admin/stickers/duplicate-groups"),
    ("GET", "/api/v1/admin/stickers/{sticker_id:int}"),
    ("PUT", "/api/v1/admin/stickers/{sticker_id}"),
    ("POST", "/api/v1/admin/stickers/{sticker_id}/enable"),
    ("POST", "/api/v1/admin/stickers/{sticker_id}/disable"),
    ("GET", "/api/v1/admin/stickers/{sticker_id}/preview"),
    ("POST", "/api/v1/admin/stickers/{sticker_id}/redescribe"),
    ("POST", "/api/v1/admin/stickers/{sticker_id}/preview/retry"),
    ("POST", "/api/v1/admin/stickers/dedupe/exact/backfill"),
    ("GET", "/api/v1/admin/stickers/near-duplicate-candidates"),
    ("POST", "/api/v1/admin/stickers/near-duplicate/scan"),
    ("POST", "/api/v1/admin/stickers/phash/backfill"),
    ("POST", "/api/v1/admin/stickers/near-duplicate-candidates/{candidate_id}/{action}"),
    ("POST", "/api/v1/admin/stickers/{sticker_id}/set-canonical"),
    ("POST", "/api/v1/admin/stickers/{sticker_id}/mark-duplicate"),
    ("POST", "/api/v1/admin/stickers/batch-delete"),
    ("DELETE", "/api/v1/admin/stickers/{sticker_id}"),
)


_STICKER_ROUTE_EXPORTS = (
    "StickerCreate",
    "StickerUpdate",
    "GeneratedImageCreate",
    "NearDuplicateAction",
    "SetCanonicalBody",
    "MarkDuplicateBody",
    "_sticker_dict",
    "create_sticker",
    "list_stickers",
    "list_generated_images",
    "create_generated_image",
    "generated_image_file",
    "sticker_duplicate_groups",
    "get_sticker",
    "update_sticker",
    "enable_sticker",
    "disable_sticker",
    "preview_sticker",
    "redescribe_sticker",
    "retry_preview",
    "stickers_dedupe_backfill",
    "list_near_duplicate_candidates",
    "scan_near_duplicates_endpoint",
    "backfill_phash_endpoint",
    "update_near_duplicate_candidate",
    "sticker_set_canonical",
    "sticker_mark_duplicate",
    "batch_delete_stickers",
    "delete_sticker",
)


def _admin_routes_for(path: str, method: str | None = None):
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

    return [
        route
        for route_path, route in _iter_routes(app.routes)
        if route_path == path and (method is None or method in getattr(route, "methods", set()))
    ]


def test_admin_sticker_routes_are_registered_from_split_module():
    for method, path in _ADMIN_STICKER_ROUTE_SIGNATURES:
        routes = _admin_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.sticker_routes"}


def test_legacy_admin_routes_sticker_imports_still_work():
    from api import admin_routes
    from api.admin import sticker_routes

    for name in _STICKER_ROUTE_EXPORTS:
        assert getattr(admin_routes, name) is getattr(sticker_routes, name)


def test_split_sticker_routes_use_legacy_admin_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/admin/stickers",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/stickers",
        headers={"Authorization": "Bearer test-token"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_admin_sticker_routes_are_not_registered_twice():
    for method, path in _ADMIN_STICKER_ROUTE_SIGNATURES:
        assert len(_admin_routes_for(path, method)) == 1, f"{method} {path}"
