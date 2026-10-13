"""Shared limiter and trusted client IP (plan 1.3, audit B-OPS-3, B-SEC-4)."""

from ipaddress import ip_network

import pytest
from starlette.requests import Request

from src.core.config import settings
from src.core.rate_limit import client_ip
from src.services import contact_service as contact_service_module


def make_request(peer: str | None, headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": (peer, 50000) if peer else None,
    }
    return Request(scope)


@pytest.fixture
def trust(monkeypatch):
    def _trust(*networks: str, header: str = "x-forwarded-for") -> None:
        monkeypatch.setattr(settings, "trusted_proxy_ips", [ip_network(n) for n in networks])
        monkeypatch.setattr(settings, "client_ip_header", header)

    _trust()  # default for every test: trust nobody
    return _trust


# --- client_ip -------------------------------------------------------------------


def test_untrusted_peer_headers_are_ignored(trust):
    request = make_request(
        "203.0.113.7", {"X-Forwarded-For": "6.6.6.6", "CF-Connecting-IP": "7.7.7.7"}
    )

    assert client_ip(request) == "203.0.113.7"


def test_peer_used_when_no_proxies_configured(trust):
    assert client_ip(make_request("198.51.100.1")) == "198.51.100.1"


def test_trusted_proxy_xff_gives_client(trust):
    trust("10.0.0.0/8")
    request = make_request("10.0.0.2", {"X-Forwarded-For": "198.51.100.9"})

    assert client_ip(request) == "198.51.100.9"


def test_forged_left_side_of_xff_is_ignored(trust):
    # The client sent "X-Forwarded-For: 6.6.6.6"; our proxy appended the real address.
    trust("10.0.0.0/8")
    request = make_request("10.0.0.2", {"X-Forwarded-For": "6.6.6.6, 198.51.100.9"})

    assert client_ip(request) == "198.51.100.9"


def test_chain_of_trusted_proxies_is_skipped(trust):
    trust("10.0.0.0/8", "172.16.0.0/12")
    request = make_request("10.0.0.2", {"X-Forwarded-For": "198.51.100.9, 172.17.0.1, 10.0.0.5"})

    assert client_ip(request) == "198.51.100.9"


def test_garbage_xff_falls_back_to_peer(trust):
    trust("10.0.0.0/8")
    request = make_request("10.0.0.2", {"X-Forwarded-For": "not-an-ip"})

    assert client_ip(request) == "10.0.0.2"


def test_cloudflare_header_used_from_trusted_proxy(trust):
    trust("173.245.48.0/20", header="cf-connecting-ip")
    request = make_request(
        "173.245.48.10", {"CF-Connecting-IP": "2001:db8::1", "X-Forwarded-For": "6.6.6.6"}
    )

    assert client_ip(request) == "2001:db8::1"


def test_invalid_cloudflare_header_falls_back_to_peer(trust):
    trust("173.245.48.0/20", header="cf-connecting-ip")
    request = make_request("173.245.48.10", {"CF-Connecting-IP": "<script>"})

    assert client_ip(request) == "173.245.48.10"


def test_ipv6_peer_is_normalised(trust):
    assert client_ip(make_request("2001:DB8:0:0::1")) == "2001:db8::1"


def test_missing_client_does_not_crash(trust):
    assert client_ip(make_request(None)) == "unknown"


# --- limits through the app ------------------------------------------------------


def _login(client, headers=None):
    return client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "wrong-password"},
        headers=headers or {},
    )


def test_login_is_limited_on_the_sixth_attempt(client):
    statuses = [_login(client).status_code for _ in range(6)]

    assert statuses == [401] * 5 + [429]


def test_429_uses_error_shape_and_retry_after(client):
    for _ in range(5):
        _login(client)

    response = _login(client)

    assert response.status_code == 429
    body = response.json()
    assert body["success"] is False
    assert body["message"].startswith("Too many requests.")
    assert 1 <= int(response.headers["Retry-After"]) <= 60


def test_spoofed_xff_does_not_bypass_limit(client, trust):
    statuses = [
        _login(client, {"X-Forwarded-For": f"198.51.100.{i}"}).status_code for i in range(6)
    ]

    assert statuses[-1] == 429


def test_contact_is_limited(client, monkeypatch):
    sent = []
    monkeypatch.setattr(
        contact_service_module.contact_service, "send_contact_email", lambda **kw: sent.append(kw)
    )
    payload = {"name": "Visitor", "email": "visitor@example.com", "message": "Hello"}

    statuses = [client.post("/api/v1/contact", json=payload).status_code for _ in range(6)]

    assert statuses == [200] * 5 + [429]
    assert len(sent) == 5


def test_health_is_never_limited(client):
    assert {client.get("/health").status_code for _ in range(20)} == {200}
