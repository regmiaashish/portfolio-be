"""GET/HEAD /api/v1/media/{key}: uploaded images and résumés, served from a private bucket.

Set S3_PUBLIC_BASE_URL to <API>/api/v1/media and every public image/résumé URL points here.
Responses are immutable and cacheable, so Cloudflare serves repeat requests from its edge.
"""

from fastapi import APIRouter, Depends, Request, Response

from src.core.dependencies import get_media_service
from src.core.rate_limit import MEDIA_LIMIT, limiter
from src.schemas.common_schema import ErrorResponse
from src.services.media_service import (
    MEDIA_TYPES,
    MediaService,
    etag_matches,
    media_headers,
    media_not_found,
    media_type,
)

router = APIRouter(tags=["media"])


@router.get(
    "/media/{key:path}",
    response_class=Response,
    responses={
        200: {
            "description": "The file (inline)",
            "content": {media: {} for media in MEDIA_TYPES.values()},
        },
        304: {"description": "Not modified (If-None-Match)"},
        404: {"model": ErrorResponse, "description": "Not an uploaded file, or it doesn't exist"},
        502: {"model": ErrorResponse, "description": "Storage failed"},
        503: {"model": ErrorResponse, "description": "Uploads are not configured"},
    },
)
# Same handler for HEAD, left out of the schema (one operation per path is enough to document).
@router.head("/media/{key:path}", include_in_schema=False)
@limiter.limit(MEDIA_LIMIT)
async def get_media(
    request: Request, key: str, service: MediaService = Depends(get_media_service)
) -> Response:
    """An uploaded project image or résumé PDF. Only keys the upload endpoints issue exist."""
    # Issued keys never need percent-encoding, so an encoded path (%2F, %2E...) is not canonical:
    # refuse it rather than serve one file under many URLs (and many Cloudflare cache entries).
    if b"%" in request.scope.get("raw_path", b"").split(b"?", 1)[0]:
        raise media_not_found()
    stored = await service.get(key)
    headers = media_headers(key, stored)
    if etag_matches(request.headers.get("if-none-match"), headers.get("ETag")):
        return Response(status_code=304, headers=headers)
    if request.method == "HEAD":
        # No body, but the real length: Starlette keeps an explicit Content-Length.
        headers["Content-Length"] = str(stored.content_length)
        return Response(status_code=200, headers=headers, media_type=media_type(key))
    return Response(content=stored.body, headers=headers, media_type=media_type(key))
