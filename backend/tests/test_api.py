"""Test API routes are properly defined."""

import pytest


def test_app_creation():
    from app.main import app
    assert app is not None
    assert "RAG" in app.title


def test_app_version():
    from app.main import app
    assert app.version == "2.0.0"


def test_health_routes_exist():
    from app.api.health import router
    routes = [r.path for r in router.routes]
    assert any("health" in r for r in routes)


def test_chat_routes_exist():
    from app.api.chat import router
    routes = [r.path for r in router.routes]
    assert any("query" in r for r in routes)


def test_document_routes_exist():
    from app.api.documents import router
    routes = [r.path for r in router.routes]
    assert any("upload" in r for r in routes)


def test_all_routers_registered():
    from app.main import app
    # Collect all leaf routes
    all_paths = set()
    for route in app.routes:
        if hasattr(route, "path") and hasattr(route, "methods"):
            all_paths.add(route.path)
        if hasattr(route, "routes"):
            for sub in route.routes:
                if hasattr(sub, "path"):
                    prefix = route.path if hasattr(route, "path") else ""
                    all_paths.add(prefix + sub.path)
    # At least health and some API endpoints should be registered
    assert len(all_paths) > 5
