"""Security headers on every API response (Phase 7)."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.core.config import settings
from src.core.dependencies import get_storage
from src.core.security_headers import API_CSP, HSTS, SecurityHeaderPolicy
from src.main import app, create_app
from tests.content_fakes import FakeStorage
from tests.test_pitch import get_events, install_pitch_fakes

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 32


def assert_api_headers(response) -> None:
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"] == API_CSP


def test_json_responses_get_the_strict_policy(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert_api_headers(response)
    # HSTS only in production (the test environment is ENV=test).
    assert "strict-transport-security" not in response.headers


@pytest.mark.parametrize(
    ("method", "path", "kwargs", "expected"),
    [
        ("get", "/api/v1/does-not-exist", {}, 404),
        ("post", "/api/v1/contact", {"json": {}}, 422),
        ("get", "/api/v1/auth/me", {}, 401),
    ],
)
def test_error_responses_get_the_headers(client, method, path, kwargs, expected):
    response = getattr(client, method)(path, **kwargs)

    assert response.status_code == expected
    assert_api_headers(response)


def test_rate_limited_responses_get_the_headers(client):
    for _ in range(6):
        response = client.post(
            "/api/v1/auth/login", json={"email": "a@example.com", "password": "wrong-password"}
        )

    assert response.status_code == 429
    assert_api_headers(response)


def test_unhandled_errors_get_the_headers():
    # Starlette sends 500s from outside the middleware stack; the handler adds them itself.
    broken = create_app()

    @broken.get("/boom")
    async def boom():
        raise RuntimeError("kaboom")

    with TestClient(broken, raise_server_exceptions=False) as test_client:
        response = test_client.get("/boom")

    assert response.status_code == 500
    assert "kaboom" not in response.text
    assert_api_headers(response)


def test_cors_preflight_gets_the_headers(client):
    response = client.options(
        "/api/v1/contact",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"


def test_media_may_be_framed_by_our_own_origins_only(client):
    storage = FakeStorage()
    key = f"{settings.s3_upload_prefix}projects/0b9a3f9e-8a54-4c47-9a39-5c0f0c7b2c11.png"
    asyncio.run(storage.put_object(key, PNG, "image/png", "public, max-age=31536000, immutable"))
    app.dependency_overrides[get_storage] = lambda: storage

    response = client.get(f"/api/v1/media/{key}")

    assert response.status_code == 200
    assert "x-frame-options" not in response.headers
    csp = response.headers["content-security-policy"]
    assert csp.startswith("frame-ancestors 'self'")
    for origin in settings.allowed_origins:
        assert origin in csp
    assert response.headers["x-content-type-options"] == "nosniff"


def test_pitch_stream_still_streams_with_the_headers(client, monkeypatch):
    install_pitch_fakes(monkeypatch)

    response, events = get_events(client)

    assert response.headers["content-type"].startswith("text/event-stream")
    assert [name for name, _ in events][-1] == "done"
    assert_api_headers(response)


def test_route_headers_are_not_overwritten():
    custom = create_app()

    @custom.get("/framed")
    async def framed():
        from fastapi.responses import JSONResponse

        return JSONResponse({}, headers={"X-Frame-Options": "SAMEORIGIN"})

    with TestClient(custom) as test_client:
        response = test_client.get("/framed")

    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["content-security-policy"] == API_CSP


DOC_PATHS = ["/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"]


@pytest.mark.parametrize("path", DOC_PATHS)
def test_docs_have_no_csp_so_swagger_can_load(path):
    policy = SecurityHeaderPolicy(frame_origins=[], hsts=False, docs_enabled=True)

    headers = policy.for_path(path)

    assert "Content-Security-Policy" not in headers
    assert headers["X-Frame-Options"] == "DENY"


@pytest.mark.parametrize("path", DOC_PATHS)
def test_docs_paths_get_the_strict_policy_when_docs_are_off(path):
    # Production: docs are disabled, so these paths are ordinary 404s with the full policy.
    policy = SecurityHeaderPolicy(frame_origins=[], hsts=True, docs_enabled=False)

    assert policy.for_path(path)["Content-Security-Policy"] == API_CSP


def test_prod_app_sends_csp_on_docs_paths():
    prod_app = create_app(settings.model_copy(update={"env": "prod"}))

    with TestClient(prod_app) as test_client:
        response = test_client.get("/docs")

    assert response.status_code == 404
    assert response.headers["content-security-policy"] == API_CSP


def test_docs_prefix_does_not_leak_to_other_paths():
    policy = SecurityHeaderPolicy(frame_origins=[], hsts=False, docs_enabled=True)

    assert policy.for_path("/docsearch")["Content-Security-Policy"] == API_CSP


def test_hsts_only_in_production():
    assert SecurityHeaderPolicy([], hsts=True).for_path("/health")["Strict-Transport-Security"] == (
        HSTS
    )
    assert "Strict-Transport-Security" not in SecurityHeaderPolicy([], hsts=False).for_path("/")
