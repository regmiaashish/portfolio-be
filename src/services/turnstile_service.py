import httpx
from loguru import logger

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


class TurnstileVerifier:
    """Server-side check of a Cloudflare Turnstile token. Disabled when no secret is set."""

    def __init__(self, client: httpx.AsyncClient, secret: str):
        self.client = client
        self.secret = secret

    @property
    def enabled(self) -> bool:
        return bool(self.secret)

    async def verify(self, token: str | None, remote_ip: str | None) -> bool:
        if not token:
            return False
        data = {"secret": self.secret, "response": token}
        if remote_ip:
            data["remoteip"] = remote_ip
        try:
            response = await self.client.post(TURNSTILE_VERIFY_URL, data=data, timeout=5.0)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Fail closed: if we can't confirm the visitor is human, don't accept the message.
            logger.warning("Turnstile verification unavailable: {!r}", exc)
            return False
        if not payload.get("success"):
            logger.info("Turnstile rejected a token: {}", payload.get("error-codes"))
            return False
        return True
