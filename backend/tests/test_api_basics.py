from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import __version__


def test_health_needs_no_key(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "version": __version__}


def test_protected_route_without_key_is_401(client: TestClient) -> None:
    assert client.get("/auth/check").status_code == 401


def test_protected_route_with_wrong_key_is_401(client: TestClient) -> None:
    response = client.get("/auth/check", headers={"X-API-Key": "wrong-key-0123456789"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing API key"}


def test_protected_route_with_key_is_200(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/auth/check", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_every_route_except_health_requires_key(app: FastAPI, client: TestClient) -> None:
    """Guards future routers: anything added outside the protected router fails here."""
    public = {"/health", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in public:
            continue
        for method in route.methods:
            # Placeholder values for path params; auth runs before validation.
            path = route.path.replace("{", "").replace("}", "")
            response = client.request(method, path)
            assert response.status_code == 401, f"{method} {route.path} is not protected"
