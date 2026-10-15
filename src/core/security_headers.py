"""Security headers on every response (Phase 7).

The API returns JSON, XML and uploaded media, never HTML pages, so its responses get the
strictest policy: nothing may load from them and nothing may frame them. Two exceptions:

- Media (`/api/v1/media/...`): the site embeds the résumé PDF in an iframe, so only our own
  origins may frame it.
- Interactive docs (`/docs`, `/redoc`, `/openapi.json`), only while docs are enabled (never in
  production): Swagger UI loads its scripts from a CDN, so they get no Content-Security-Policy.

A pure ASGI middleware (not BaseHTTPMiddleware), so streamed responses such as the Pitch Me
SSE stream pass straight through. Headers a route already set are never overwritten.
"""

from collections.abc import Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MEDIA_PREFIX = "/api/v1/media/"
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

API_CSP = "default-src 'none'; frame-ancestors 'none'"
HSTS = "max-age=31536000"

_COMMON = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class SecurityHeaderPolicy:
    """Which security headers a response for a given path gets."""

    def __init__(self, frame_origins: Iterable[str], hsts: bool, docs_enabled: bool = False):
        # Origins are validated at settings load (`Settings.check_origins`).
        ancestors = " ".join(["'self'", *frame_origins])
        self.media_csp = f"frame-ancestors {ancestors}"
        self.hsts = hsts
        self.docs_enabled = docs_enabled

    def for_path(self, path: str) -> dict[str, str]:
        headers = dict(_COMMON)
        if path.startswith(MEDIA_PREFIX):
            headers["Content-Security-Policy"] = self.media_csp
        elif self.docs_enabled and _is_docs(path):
            headers["X-Frame-Options"] = "DENY"
        else:
            headers["Content-Security-Policy"] = API_CSP
            headers["X-Frame-Options"] = "DENY"
        if self.hsts:
            headers["Strict-Transport-Security"] = HSTS
        return headers


def _is_docs(path: str) -> bool:
    return any(path == docs or path.startswith(f"{docs}/") for docs in DOCS_PATHS)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, policy: SecurityHeaderPolicy):
        self.app = app
        self.policy = policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        extra = self.policy.for_path(scope.get("path", ""))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = with_defaults(message.get("headers", []), extra)
            await send(message)

        await self.app(scope, receive, send_with_headers)


def with_defaults(raw: Iterable[tuple[bytes, bytes]], extra: dict[str, str]) -> list:
    """`raw` plus each header in `extra` that isn't already present (case-insensitive)."""
    headers = list(raw)
    present = {name.lower() for name, _ in headers}
    for name, value in extra.items():
        encoded = name.lower().encode("latin-1")
        if encoded not in present:
            headers.append((encoded, value.encode("latin-1")))
    return headers
