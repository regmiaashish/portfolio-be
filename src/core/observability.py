"""Optional error tracking with Sentry (Phase 7). Off unless SENTRY_DSN is set.

Nothing about visitors is sent: no request bodies (contact messages, pitch inputs), no
cookies, auth or forwarding headers, no query strings (admin searches can contain names), no
local variable values in stack traces, and no breadcrumbs from log lines or outbound HTTP calls
(the geolocation and weather URLs contain the visitor's IP address and coordinates). As a last
line of defence, IP addresses and coordinates left anywhere in an event are redacted.
"""

import ipaddress
import re
from typing import Any

import sentry_sdk
from loguru import logger
from sentry_sdk.integrations.loguru import LoggingLevels, LoguruIntegration

from src.core.config import Settings

# Headers that carry credentials or the visitor's IP address.
_DROPPED_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-forwarded-for",
    "cf-connecting-ip",
    "x-real-ip",
    "forwarded",
    "true-client-ip",
}

# Breadcrumbs that can quote visitor data: log lines, and outbound HTTP calls (URLs and
# query strings hold the visitor's IP and coordinates).
_DROPPED_BREADCRUMB_TYPES = {"log", "http"}
_DROPPED_BREADCRUMB_CATEGORIES = {"httplib"}

# Candidates only: each match is confirmed with `ipaddress` before it is redacted, so version
# numbers and times such as 12:30:45 are left alone.
_IPV4_CANDIDATE = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?![\w.])")
_IPV6_CANDIDATE = re.compile(r"(?<![\w:])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?![\w:])")
_COORDINATE = re.compile(r"\b(lat|latitude|lon|lng|longitude)=[^&\s'\"]+", re.IGNORECASE)

REDACTED_IP = "[ip]"


def _redact_ip(match: re.Match[str]) -> str:
    try:
        ipaddress.ip_address(match.group())
    except ValueError:
        return match.group()
    return REDACTED_IP


def redact_text(text: str) -> str:
    """`text` with IP addresses and latitude/longitude values replaced."""
    text = _IPV4_CANDIDATE.sub(_redact_ip, text)
    text = _IPV6_CANDIDATE.sub(_redact_ip, text)
    return _COORDINATE.sub(lambda match: f"{match.group(1)}=[redacted]", text)


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_redact(item) for item in value]
    return value


def _keep_breadcrumb(crumb: dict[str, Any]) -> bool:
    return (
        crumb.get("type") not in _DROPPED_BREADCRUMB_TYPES
        and crumb.get("category") not in _DROPPED_BREADCRUMB_CATEGORIES
    )


def scrub_breadcrumb(
    crumb: dict[str, Any], hint: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Sentry `before_breadcrumb`: drop log and HTTP breadcrumbs, redact the rest."""
    if not _keep_breadcrumb(crumb):
        return None
    return _redact(crumb)


def scrub_event(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """Sentry `before_send` / `before_send_transaction`: strip anything that could identify a
    visitor or a session."""
    request = event.get("request")
    if isinstance(request, dict):
        for key in ("data", "cookies", "query_string", "env"):
            request.pop(key, None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                name: value
                for name, value in headers.items()
                if name.lower() not in _DROPPED_HEADERS
            }
    event.pop("user", None)

    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, dict) and isinstance(breadcrumbs.get("values"), list):
        breadcrumbs["values"] = [
            crumb
            for crumb in breadcrumbs["values"]
            if isinstance(crumb, dict) and _keep_breadcrumb(crumb)
        ]

    # Transactions: outbound-call spans record the full URL and query string.
    for span in event.get("spans") or []:
        data = span.get("data") if isinstance(span, dict) else None
        if isinstance(data, dict):
            for key in ("url", "http.query", "http.fragment"):
                data.pop(key, None)

    return _redact(event)


def init_sentry(settings: Settings) -> bool:
    """Start the Sentry SDK when a DSN is configured; returns whether it was started.

    Never in tests, and never fatal: a malformed DSN logs a warning (without the value) and
    leaves Sentry off instead of stopping every worker from booting.
    """
    dsn = settings.sentry_dsn.get_secret_value()
    if not dsn or settings.env == "test":
        return False
    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=settings.sentry_environment or settings.env,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            send_default_pii=False,
            include_local_variables=False,
            max_request_body_size="never",
            before_send=scrub_event,
            before_send_transaction=scrub_event,
            before_breadcrumb=scrub_breadcrumb,
            integrations=[
                # logger.exception / logger.error become Sentry events. No log breadcrumbs
                # (level=None) and no Sentry Logs: log lines can quote visitor data.
                LoguruIntegration(
                    level=None,
                    event_level=LoggingLevels.ERROR.value,
                    sentry_logs_level=None,
                ),
            ],
        )
    except Exception as exc:  # noqa: BLE001 -- error tracking must never stop the API booting
        logger.warning("Sentry is off: SENTRY_DSN was rejected ({})", type(exc).__name__)
        return False
    return True
