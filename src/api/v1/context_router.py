from ipaddress import ip_address

from fastapi import APIRouter, HTTPException, Query, Request, status
from httpx import HTTPStatusError
from loguru import logger

from src.core.config import settings
from src.services.context_service import get_visitor_context

router = APIRouter(tags=["context"])


@router.get("/context")
async def visitor_context(
    request: Request,
    ip: str | None = Query(default=None, description="Visitor IPv4 or IPv6 address."),
):
    visitor_ip = ip or (request.client.host if request.client else None)
    if not visitor_ip:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A visitor IP address is required.",
        )

    try:
        parsed_ip = ip_address(visitor_ip)
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
                "Pass a public IP with ?ip=... or set CONTEXT_MOCK=true for local testing."
            ),
        )

    try:
        return await get_visitor_context(visitor_ip)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HTTPStatusError as exc:
        logger.warning("Context provider returned {} for {}", exc.response.status_code, visitor_ip)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "A context provider is temporarily unavailable or rate-limited. "
                "Try again later or use CONTEXT_MOCK=true for local testing."
            ),
        ) from exc
    except Exception as exc:
        logger.warning("Visitor context lookup failed for {}: {}", visitor_ip, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Visitor context is temporarily unavailable.",
        ) from exc
