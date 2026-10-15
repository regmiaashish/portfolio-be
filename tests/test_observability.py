"""Optional Sentry error tracking (Phase 7): off by default, never sends visitor data.

Nothing here talks to Sentry: the end-to-end test captures events with an in-memory transport.
"""

import json
from typing import ClassVar

import httpx
import pytest
import sentry_sdk
from loguru import logger
from pydantic import SecretStr
from sentry_sdk.integrations.httpx import HttpxIntegration
from sentry_sdk.transport import Transport

from src.core import observability
from src.core.config import settings
from src.core.observability import init_sentry, redact_text, scrub_breadcrumb, scrub_event

DSN = "https://publickey@o0.ingest.example/1"
VISITOR_IP = "203.0.113.9"
VISITOR_IPV6 = "2001:db8::42"


def _settings(**update):
    """The test settings, switched out of ENV=test (where Sentry is always off)."""
    return settings.model_copy(update={"env": "dev", "sentry_environment": "", **update})


@pytest.fixture
def captured_init(monkeypatch):
    calls = []
    monkeypatch.setattr(observability.sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
    return calls


def test_off_without_a_dsn(captured_init):
    assert init_sentry(_settings(sentry_dsn=SecretStr(""))) is False
    assert captured_init == []


def test_never_on_in_tests_even_with_a_dsn(captured_init):
    assert settings.env == "test"
    assert init_sentry(settings.model_copy(update={"sentry_dsn": SecretStr(DSN)})) is False
    assert captured_init == []


def test_privacy_options_when_on(captured_init):
    assert init_sentry(_settings(sentry_dsn=SecretStr(DSN))) is True

    (options,) = captured_init
    assert options["dsn"] == DSN
    assert options["environment"] == "dev"
    assert options["send_default_pii"] is False
    assert options["include_local_variables"] is False
    assert options["max_request_body_size"] == "never"
    assert options["traces_sample_rate"] == 0
    assert options["before_send"] is scrub_event
    assert options["before_send_transaction"] is scrub_event
    assert options["before_breadcrumb"] is scrub_breadcrumb


def test_a_malformed_dsn_logs_a_warning_and_keeps_the_api_up():
    lines: list[str] = []
    sink = logger.add(lines.append, format="{message}", level="WARNING")
    try:
        started = init_sentry(_settings(sentry_dsn=SecretStr("not-a-dsn-secret-value")))
    finally:
        logger.remove(sink)

    assert started is False
    assert any("Sentry is off" in line for line in lines)
    assert "secret-value" not in "".join(lines)


# --- scrubbing --------------------------------------------------------------------------


def test_scrub_removes_bodies_cookies_ips_and_queries():
    event = {
        "request": {
            "url": "https://api.example/api/v1/contact",
            "method": "POST",
            "data": {"message": "private text"},
            "cookies": {"access_token": "jwt"},
            "query_string": "q=Jane",
            "env": {"REMOTE_ADDR": VISITOR_IP},
            "headers": {
                "Authorization": "Bearer x",
                "Cookie": "access_token=jwt",
                "CF-Connecting-IP": VISITOR_IP,
                "X-Forwarded-For": VISITOR_IP,
                "User-Agent": "test",
            },
        },
        "user": {"ip_address": VISITOR_IP},
    }

    scrubbed = scrub_event(event)

    request = scrubbed["request"]
    assert set(request) == {"url", "method", "headers"}
    assert request["headers"] == {"User-Agent": "test"}
    assert "user" not in scrubbed
    assert VISITOR_IP not in str(scrubbed)


def test_scrub_drops_log_and_http_breadcrumbs_and_redacts_exception_text():
    event = {
        "breadcrumbs": {
            "values": [
                {
                    "type": "http",
                    "category": "httplib",
                    "data": {
                        "url": f"https://ipapi.co/{VISITOR_IP}/json/",
                        "http.query": "latitude=27.7&longitude=85.3",
                    },
                },
                {"type": "log", "category": "src.services", "message": f"failed {VISITOR_IP}"},
                {"type": "default", "category": "query", "message": "SELECT 1"},
            ]
        },
        "exception": {
            "values": [
                {
                    "type": "HTTPStatusError",
                    "value": f"Client error '429' for url 'https://ipapi.co/{VISITOR_IP}/json/'",
                },
                {"type": "ValueError", "value": f"bad address {VISITOR_IPV6}"},
            ]
        },
        "logentry": {"message": "Geolocation failed for {}", "params": [VISITOR_IP]},
        "message": "weather latitude=27.7&longitude=85.3",
        "spans": [
            {
                "description": f"GET https://ipapi.co/{VISITOR_IP}/json/",
                "data": {"url": "https://api.open-meteo.com/v1/forecast", "http.query": "lat=1"},
            }
        ],
    }

    scrubbed = scrub_event(event)

    assert scrubbed["breadcrumbs"]["values"] == [
        {"type": "default", "category": "query", "message": "SELECT 1"}
    ]
    text = json.dumps(scrubbed)
    assert VISITOR_IP not in text
    assert VISITOR_IPV6 not in text
    assert "27.7" not in text and "85.3" not in text
    assert "[ip]" in scrubbed["exception"]["values"][0]["value"]
    assert scrubbed["spans"][0]["data"] == {}


def test_breadcrumb_filter():
    assert scrub_breadcrumb({"type": "http", "category": "httplib"}) is None
    assert scrub_breadcrumb({"type": "log", "category": "x", "message": "m"}) is None
    kept = scrub_breadcrumb({"type": "default", "category": "auth", "message": f"ip {VISITOR_IP}"})
    assert kept == {"type": "default", "category": "auth", "message": "ip [ip]"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("from 203.0.113.9 now", "from [ip] now"),
        ("v6 2001:db8::1 here", "v6 [ip] here"),
        ("version 1.2.3", "version 1.2.3"),
        ("at 12:30:45 today", "at 12:30:45 today"),
        ("not an ip 999.1.1.1", "not an ip 999.1.1.1"),
        ("?latitude=27.7&longitude=85.3", "?latitude=[redacted]&longitude=[redacted]"),
    ],
)
def test_redact_text(text, expected):
    assert redact_text(text) == expected


# --- end to end, offline ------------------------------------------------------------------


class _MemoryTransport(Transport):
    events: ClassVar[list[dict]] = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            payload = item.payload.json
            if payload is not None:
                self.events.append(payload)

    def flush(self, *args, **kwargs):
        return None

    def kill(self):
        return None


def test_a_real_sentry_event_carries_no_visitor_data(monkeypatch):
    """The geolocation failure path through the real SDK: an httpx call to a URL holding the
    visitor's IP (an httplib breadcrumb), a warning log line, then a chained exception."""
    real_init = sentry_sdk.init
    _MemoryTransport.events = []

    def init_offline(**options):
        options["integrations"] = [*options["integrations"], HttpxIntegration()]
        return real_init(transport=_MemoryTransport, auto_enabling_integrations=False, **options)

    monkeypatch.setattr(observability.sentry_sdk, "init", init_offline)
    assert init_sentry(_settings(sentry_dsn=SecretStr(DSN))) is True
    try:
        upstream = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429)))
        response = upstream.get(f"https://ipapi.co/{VISITOR_IP}/json/?latitude=27.7")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as cause:
            logger.warning("Geolocation failed for {}", VISITOR_IP)
            try:
                raise RuntimeError(f"context unavailable for {VISITOR_IP}") from cause
            except RuntimeError as error:
                sentry_sdk.capture_exception(error)
        sentry_sdk.flush()
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.get_global_scope().set_client(None)

    (event,) = [event for event in _MemoryTransport.events if event.get("exception")]
    text = json.dumps(event)
    assert VISITOR_IP not in text
    assert "27.7" not in text
    assert not event.get("breadcrumbs", {}).get("values")
    assert {value["type"] for value in event["exception"]["values"]} == {
        "HTTPStatusError",
        "RuntimeError",
    }
