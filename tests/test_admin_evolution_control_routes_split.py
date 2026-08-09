from __future__ import annotations

from pathlib import Path


_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/admin/evals/evolution/catalog"),
    ("POST", "/api/v1/admin/evals/evolution/datasets/import"),
    ("POST", "/api/v1/admin/evals/evolution/candidates/import"),
    ("GET", "/api/v1/admin/evals/evolution/candidates/{candidate_sha256}"),
    ("POST", "/api/v1/admin/evals/evolution/gates"),
    ("POST", "/api/v1/admin/evals/evolution/approvals"),
    ("POST", "/api/v1/admin/evals/evolution/canary/activate"),
    ("GET", "/api/v1/admin/evals/evolution/canary/resolve"),
    ("POST", "/api/v1/admin/evals/evolution/canary/{release_id}/rollback"),
    ("GET", "/api/v1/admin/evals/evolution/state"),
)


def _routes_for(path: str, method: str):
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
            yield from _iter_routes(
                original_router.routes,
                prefix + include_prefix,
            )

    return [
        route
        for route_path, route in _iter_routes(app.routes)
        if route_path == path
        and method in getattr(route, "methods", set())
    ]


def test_evolution_control_routes_are_registered_once_from_split_module():
    for method, path in _ROUTE_SIGNATURES:
        routes = _routes_for(path, method)

        assert len(routes) == 1, f"{method} {path}"
        assert routes[0].endpoint.__module__ == (
            "api.admin.evolution_control_routes"
        )


def test_evolution_control_routes_do_not_import_legacy_parent_or_public_routes():
    source = Path("api/admin/evolution_control_routes.py").read_text(
        encoding="utf-8"
    )

    assert "from api.admin_routes" not in source
    assert "import api.admin_routes" not in source
    assert "from api.routes" not in source
