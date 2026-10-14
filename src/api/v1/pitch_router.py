from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sse_starlette import EventSourceResponse

from src.core.dependencies import ensure_pitch_enabled, get_pitch_service
from src.core.rate_limit import PITCH_LIMIT, client_ip, limiter
from src.schemas.common_schema import ErrorResponse
from src.schemas.pitch_schema import PitchQuery
from src.services.pitch.service import PitchService

router = APIRouter(tags=["pitch"])

# Keep-alive comments so Cloudflare and other proxies don't close a quiet stream.
PING_SECONDS = 15
STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.get(
    "/pitch/stream",
    dependencies=[Depends(ensure_pitch_enabled)],
    response_class=EventSourceResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "Server-Sent Events: agent, log, token, then done or error.",
        },
        400: {"model": ErrorResponse, "description": "Turnstile verification failed"},
        429: {"model": ErrorResponse, "description": "Rate limited or daily cap reached"},
        503: {"model": ErrorResponse, "description": "Pitch Me is switched off"},
    },
)
@limiter.limit(PITCH_LIMIT)
async def pitch_stream(
    request: Request,
    query: Annotated[PitchQuery, Query()],
    service: PitchService = Depends(get_pitch_service),
) -> EventSourceResponse:
    """Research a company, match it to my experience and stream a short pitch."""
    events = await service.start(query, client_ip(request))
    return EventSourceResponse(events, ping=PING_SECONDS, headers=STREAM_HEADERS)
