from ipaddress import ip_address

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.core.config import settings
from src.core.dependencies import get_context_service
from src.core.rate_limit import CONTEXT_LIMIT, client_ip, limiter
from src.schemas.common_schema import ErrorResponse
from src.schemas.context_schema import VisitorContext
from src.services.context_service import ContextService

router = APIRouter(tags=["context"])


@router.get(
    "/context",
    response_model=VisitorContext,
    responses={
        400: {"model": ErrorResponse, "description": "IP cannot be geolocated"},
        429: {"model": ErrorResponse, "description": "Too many requests"},
        503: {"model": ErrorResponse, "description": "Geolocation unavailable"},
    },
)
@limiter.limit(CONTEXT_LIMIT)
async def visitor_context(
    request: Request,
    ip: str | None = Query(
        default=None, description="Override the visitor IP for testing. Ignored in production."
    ),
    service: ContextService = Depends(get_context_service),
) -> VisitorContext:
    # In production the IP always comes from the connection (via trusted proxies), so
    # nobody can use this endpoint to look up arbitrary addresses.
    visitor_ip = ip if ip and not settings.is_prod else client_ip(request)

    try:
        parsed_ip = ip_address(visitor_ip.strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid IPv4 or IPv6 address is required.",
        ) from exc

    if not parsed_ip.is_global and not settings.context_mock:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Local/private IP addresses cannot be geolocated. "
                "Pass a public IP with ?ip=... (outside production) or set CONTEXT_MOCK=true."
            ),
        )

    return await service.get_context(str(parsed_ip))
