"""Liveness and readiness probes (plan 1.1)."""

import asyncio
import time

from src.api import health_router
from src.api.health_router import get_db_probe
from src.main import app


def _use_probe(probe) -> None:
    app.dependency_overrides[get_db_probe] = lambda: probe


def test_liveness_is_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_ok_when_database_answers(client):
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_503_when_database_fails(client):
    async def failing_probe():
        raise ConnectionError("db down at password=secret")

    _use_probe(failing_probe)
    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "secret" not in response.text


def test_readiness_503_when_database_is_slow(client, monkeypatch):
    monkeypatch.setattr(health_router, "READY_TIMEOUT_SECONDS", 0.05)

    async def slow_probe():
        await asyncio.sleep(5)

    _use_probe(slow_probe)
    started = time.monotonic()
    response = client.get("/health/ready")

    assert response.status_code == 503
    assert time.monotonic() - started < 1.0


def test_readiness_does_not_wait_for_a_probe_that_is_slow_to_cancel(client, monkeypatch):
    # Regression: asyncpg needs a server reply to cancel a query, so against a hung
    # database a cancelled probe took ~147 s to unwind and asyncio.wait_for blocked on it.
    monkeypatch.setattr(health_router, "READY_TIMEOUT_SECONDS", 0.05)

    async def stubborn_probe():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(3)  # slow to unwind, like asyncpg's cancel round-trip
            raise

    _use_probe(stubborn_probe)
    started = time.monotonic()
    response = client.get("/health/ready")

    assert response.status_code == 503
    assert time.monotonic() - started < 1.0


def test_root_route_is_removed(client):
    assert client.get("/").status_code == 404
