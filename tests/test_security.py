"""JWT handling after the python-jose -> PyJWT swap (plan 1.5, audit B-SEC-3)."""

import base64
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from src.core.security import create_access_token, create_refresh_token, verify_token
from tests import TEST_ENV

SECRET = TEST_ENV["JWT_SECRET_KEY"]
SUB = "5b0d7f8e-2c3a-4e55-9a5b-0f1e2d3c4b5a"

# Minted with python-jose 3.5 (the previous library) from the same secret, with an expiry in
# 2100. Proves cookies issued before the swap keep working, so nobody is logged out.
LEGACY_JOSE_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiI1YjBkN2Y4ZS0yYzNhLTRlNTUtOWE1Yi0wZjFlMmQzYzRiNWEiLCJlbWFpbCI6ImFkbWluQGV4YW1wbGUu"
    "Y29tIiwiaXNfc3VwZXJ1c2VyIjp0cnVlLCJleHAiOjQxMDI0NDQ4MDAsImlhdCI6MTc5MTkwMDAwMCwidHlwZSI6ImFj"
    "Y2VzcyJ9."
    "Km6cQnK2st93ckaxZBbHq2KMB6rfxithWpGkfoSYiOI"
)


def _token(**claims) -> str:
    now = datetime.now(UTC)
    payload = {"sub": SUB, "iat": now, "exp": now + timedelta(minutes=5), "type": "access"}
    payload.update(claims)
    return jwt.encode({k: v for k, v in payload.items() if v is not None}, SECRET, "HS256")


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def test_access_token_round_trip():
    payload = verify_token(create_access_token({"sub": SUB, "email": "a@example.com"}))

    assert payload is not None
    assert payload["sub"] == SUB
    assert payload["type"] == "access"


def test_refresh_token_is_not_accepted_as_access():
    token = create_refresh_token({"sub": SUB})

    assert verify_token(token, "access") is None
    assert verify_token(token, "refresh") is not None


def test_legacy_python_jose_token_still_valid():
    payload = verify_token(LEGACY_JOSE_TOKEN)

    assert payload is not None
    assert payload["sub"] == SUB


def test_expired_token_rejected():
    past = datetime.now(UTC) - timedelta(hours=1)

    assert verify_token(_token(iat=past - timedelta(hours=1), exp=past)) is None


@pytest.mark.parametrize("missing", ["exp", "iat", "sub", "type"])
def test_token_missing_required_claim_rejected(missing):
    assert verify_token(_token(**{missing: None})) is None


def test_wrong_secret_rejected():
    now = datetime.now(UTC)
    payload = {"sub": SUB, "iat": now, "exp": now + timedelta(minutes=5), "type": "access"}

    assert verify_token(jwt.encode(payload, "x" * 48, "HS256")) is None


def test_alg_none_token_rejected():
    now = int(datetime.now(UTC).timestamp())
    claims = {"sub": SUB, "iat": now, "exp": now + 300, "type": "access"}
    unsigned = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(claims)}."

    assert verify_token(unsigned) is None


def test_tampered_payload_rejected():
    header, _, signature = create_access_token({"sub": SUB}).split(".")
    now = int(datetime.now(UTC).timestamp())
    forged = _b64(
        {"sub": SUB, "iat": now, "exp": now + 300, "type": "access", "is_superuser": True}
    )

    assert verify_token(f"{header}.{forged}.{signature}") is None


@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "Bearer x.y.z"])
def test_garbage_rejected(garbage):
    assert verify_token(garbage) is None
