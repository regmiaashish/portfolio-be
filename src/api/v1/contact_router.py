from fastapi import APIRouter, BackgroundTasks, Request, status
from loguru import logger

from src.core.exceptions import EmailSendException
from src.core.rate_limit import CONTACT_LIMIT, limiter
from src.schemas.common_schema import APIResponse
from src.schemas.contact_schema import ContactRequest
from src.services.contact_service import contact_service

router = APIRouter(tags=["Message"])


@router.post("/contact", response_model=APIResponse, status_code=status.HTTP_200_OK)
@limiter.limit(CONTACT_LIMIT)
async def contact(
    request: Request, contact_request: ContactRequest, background_tasks: BackgroundTasks
):
    """
    Endpoint to handle contact form submissions.
    """
    try:
        # Send the contact email in the background
        background_tasks.add_task(
            contact_service.send_contact_email,
            name=contact_request.name,
            email=contact_request.email,
            message=contact_request.message,
        )
        return APIResponse(message="Your message has been sent successfully.")
    except Exception as e:
        logger.error(f"Error sending contact email: {e}")
        raise EmailSendException(
            message="An error occurred while sending your message. Please try again later.",
        ) from e
