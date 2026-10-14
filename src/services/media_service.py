"""Uploaded images and résumés served through the API, so the S3 bucket can stay private."""

from datetime import UTC
from email.utils import format_datetime

from loguru import logger

from src.core.exceptions import NotFoundError, ServiceUnavailableError, StorageError
from src.schemas.content_schema import is_issued_key
from src.services.storage_service import StorageService, StoredObject

# Upload keys are unique UUIDs, so a served file never changes.
MEDIA_CACHE_CONTROL = "public, max-age=31536000, immutable"

# The served type comes from the key's extension, which is_issued_key() has already limited to
# these. Never the stored metadata: nothing tampered with in the bucket can be served as HTML.
MEDIA_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "webp": "image/webp",
    "avif": "image/avif",
    "pdf": "application/pdf",
}


def media_not_found() -> NotFoundError:
    # One answer for "not ours" and "doesn't exist": never reveal what else is in the bucket.
    return NotFoundError("Not found.", errors={"code": "media_not_found"})


def media_type(key: str) -> str:
    return MEDIA_TYPES[key.rsplit(".", 1)[-1]]


def quoted_etag(etag: str | None) -> str | None:
    if not etag:
        return None
    return etag if etag.startswith(('"', 'W/"')) else f'"{etag}"'


def etag_matches(if_none_match: str | None, etag: str | None) -> bool:
    if not if_none_match or not etag:
        return False
    if if_none_match.strip() == "*":
        return True
    wanted = etag.removeprefix("W/")
    return any(tag.strip().removeprefix("W/") == wanted for tag in if_none_match.split(","))


def media_headers(key: str, stored: StoredObject) -> dict[str, str]:
    headers = {
        "Cache-Control": MEDIA_CACHE_CONTROL,
        "Content-Disposition": "inline",
        "X-Content-Type-Options": "nosniff",
    }
    if etag := quoted_etag(stored.etag):
        headers["ETag"] = etag
    if stored.last_modified is not None:
        headers["Last-Modified"] = format_datetime(
            stored.last_modified.astimezone(UTC), usegmt=True
        )
    return headers


class MediaService:
    def __init__(self, storage: StorageService | None):
        self.storage = storage

    async def get(self, key: str) -> StoredObject:
        # Validate before touching storage: only keys the upload endpoints issue can be read.
        if not is_issued_key(key):
            raise media_not_found()
        if self.storage is None:
            raise ServiceUnavailableError(
                "Uploads are not configured.", errors={"code": "uploads_disabled"}
            )
        try:
            stored = await self.storage.get_object(key)
        except Exception as exc:
            response = getattr(exc, "response", None)
            code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
            logger.warning("S3 get_object failed for {}: {}", key, code or type(exc).__name__)
            raise StorageError("Couldn't load the file. Please try again.") from exc
        if stored is None:
            raise media_not_found()
        return stored
