from fastapi import APIRouter, BackgroundTasks, Depends, Request, status

from src.core.dependencies import get_contact_delivery, get_contact_service
from src.core.rate_limit import CONTACT_LIMIT, client_ip, limiter
from src.schemas.common_schema import APIResponse, ErrorResponse
from src.schemas.contact_schema import ContactRequest
from src.services.contact_service import ContactDelivery, ContactService

router = APIRouter(tags=["Message"])

SUCCESS_MESSAGE = "Thanks! Your message is on its way."


@router.post(
    "/contact",
    response_model=APIResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        400: {"model": ErrorResponse, "description": "Turnstile verification failed"},
        429: {"model": ErrorResponse, "description": "Too many messages"},
    },
)
@limiter.limit(CONTACT_LIMIT)
async def contact(
    request: Request,
    payload: ContactRequest,
    background_tasks: BackgroundTasks,
    service: ContactService = Depends(get_contact_service),
    delivery: ContactDelivery = Depends(get_contact_delivery),
) -> APIResponse:
    """Store the message, then email it after responding (202: accepted, not yet sent)."""
    stored = await service.submit(payload, client_ip(request))
    if stored is not None:
        background_tasks.add_task(
            delivery.deliver,
            stored.id,
            name=payload.name,
            email=payload.email,
            message=payload.message,
        )
    # Spam dropped by the honeypot gets the same answer, so bots learn nothing.
    return APIResponse(message=SUCCESS_MESSAGE)
