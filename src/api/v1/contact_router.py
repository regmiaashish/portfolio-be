from slowapi.util import get_remote_address
from fastapi import HTTPException, APIRouter, status, Request, BackgroundTasks
from loguru import logger
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.schemas.contact_schema import ContactRequest
from src.services.contact_service import  contact_service
from src.core.exceptions import EmailSendException
from src.schemas.common_schema import APIResponse



limiter = Limiter(key_func=get_remote_address) 
router = APIRouter(tags=["Message"], prefix="/api/v1")

@router.post("/contact", response_model=APIResponse, status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")

async def contact(
    request: Request,
    contact_request: ContactRequest,
    background_tasks: BackgroundTasks
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
            message=contact_request.message
        )
        return APIResponse(message="Your message has been sent successfully.")
    except Exception as e:
        logger.error(f"Error sending contact email: {e}")
        raise EmailSendException(
            message="An error occurred while sending your message. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

