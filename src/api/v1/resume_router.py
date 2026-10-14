"""GET /api/v1/resume/download: a real download of the résumé (Phase 6).

Browsers ignore the `download` attribute on cross-origin links, so the frontend's
"Download PDF" button points here; the redirect target carries the attachment disposition.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from src.core.dependencies import get_content_service
from src.core.rate_limit import RESUME_LIMIT, limiter
from src.schemas.common_schema import ErrorResponse
from src.services.content_service import ContentService

router = APIRouter(tags=["content"])


@router.get(
    "/resume/download",
    status_code=302,
    response_class=RedirectResponse,
    responses={
        302: {"description": "Redirect to the PDF (a short-lived attachment link for uploads)"},
        404: {"model": ErrorResponse, "description": "No résumé is set"},
    },
)
@limiter.limit(RESUME_LIMIT)
async def download_resume(
    request: Request, service: ContentService = Depends(get_content_service)
) -> RedirectResponse:
    """Send the visitor to the résumé PDF so the browser saves it."""
    target = await service.resume_download_url()
    # Presigned links expire in minutes: never let a browser or CDN cache the redirect.
    return RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store"})
