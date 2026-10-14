import hashlib

from fastapi import APIRouter, Depends, Request, Response

from src.core.dependencies import get_content_service
from src.core.rate_limit import CONTENT_LIMIT, limiter
from src.schemas.common_schema import ErrorResponse
from src.schemas.content_schema import ContentOut
from src.services.content_service import ContentService

router = APIRouter(tags=["content"])

# Browsers and Cloudflare may reuse a copy for a minute, then revalidate in the background.
CACHE_CONTROL = "public, max-age=60, stale-while-revalidate=600"


def _matches(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    candidates = {tag.strip() for tag in if_none_match.split(",")}
    return "*" in candidates or etag in candidates


@router.get(
    "/content",
    response_model=ContentOut,
    responses={
        304: {"description": "Not modified (If-None-Match matched the ETag)"},
        503: {"model": ErrorResponse, "description": "Content not seeded yet"},
    },
)
@limiter.limit(CONTENT_LIMIT)
async def public_content(
    request: Request, service: ContentService = Depends(get_content_service)
) -> Response:
    """Everything the public site renders: profile, published projects, career and skills."""
    body = (await service.public_content()).model_dump_json().encode()
    etag = f'"{hashlib.sha256(body).hexdigest()}"'
    headers = {"ETag": etag, "Cache-Control": CACHE_CONTROL}
    if _matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)
