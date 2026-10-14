import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from uuid import UUID

from loguru import logger

from src.core.config import settings
from src.core.database import AsyncSessionLocal
from src.core.exceptions import VerificationFailedError
from src.models.contact_message_model import ContactMessage
from src.repositories.contact_message_repo import ContactMessageRepository
from src.schemas.contact_schema import ContactRequest
from src.services.email_service import EmailSender
from src.services.turnstile_service import TurnstileVerifier
from src.templates.contact_template import confirmation_email_template, contact_email_template

RepositoryScope = Callable[[], AbstractAsyncContextManager[ContactMessageRepository]]


@asynccontextmanager
async def own_session_repository() -> AsyncIterator[ContactMessageRepository]:
    """A repository on a fresh session: background jobs outlive the request's session."""
    async with AsyncSessionLocal() as session:
        yield ContactMessageRepository(session)


class ContactService:
    """Accepts a submission: spam checks, then store it as pending."""

    def __init__(self, repository: ContactMessageRepository, verifier: TurnstileVerifier):
        self.repository = repository
        self.verifier = verifier

    async def submit(self, payload: ContactRequest, client_ip: str | None) -> ContactMessage | None:
        """Returns the stored message, or None when it was silently dropped as spam."""
        if payload.website:
            logger.info("Contact form honeypot filled; dropping the submission")
            return None

        if self.verifier.enabled and not await self.verifier.verify(
            payload.turnstile_token, client_ip
        ):
            raise VerificationFailedError()

        return await self.repository.create(
            name=payload.name, email=payload.email, message=payload.message
        )


_warned_confirmation_disabled = False


def confirmation_allowed() -> bool:
    """Confirmation emails go to an address the visitor typed, so they need Turnstile."""
    global _warned_confirmation_disabled
    if not settings.contact_confirmation_enabled:
        return False
    if not settings.turnstile_secret.get_secret_value():
        if not _warned_confirmation_disabled:
            logger.warning(
                "CONTACT_CONFIRMATION_ENABLED is set but TURNSTILE_SECRET is not; "
                "confirmation emails stay off so the form can't be used to mail third parties"
            )
            _warned_confirmation_disabled = True
        return False
    return True


class ContactDelivery:
    """Runs after the response: send the email, then record whether it worked."""

    def __init__(self, sender: EmailSender, repository_scope: RepositoryScope):
        self.sender = sender
        self.repository_scope = repository_scope

    async def deliver(self, message_id: UUID, *, name: str, email: str, message: str) -> None:
        subject, html = contact_email_template.generate_email_template(
            name=name, email=email, message=message
        )
        try:
            await asyncio.to_thread(
                self.sender.send_email,
                recipient=settings.super_admin_email,
                subject=subject,
                html=html,
            )
        except Exception as exc:  # noqa: BLE001 -- any send failure is recorded on the row
            logger.exception("Contact message {} could not be emailed", message_id)
            async with self.repository_scope() as repository:
                await repository.mark_failed(message_id, f"{type(exc).__name__}: {exc}")
            return

        async with self.repository_scope() as repository:
            await repository.mark_sent(message_id)

        if confirmation_allowed():
            await self.send_confirmation(name, email)

    async def send_confirmation(self, name: str, email: str) -> None:
        subject, html = confirmation_email_template(name)
        try:
            await asyncio.to_thread(
                self.sender.send_email, recipient=email, subject=subject, html=html
            )
        except Exception:  # noqa: BLE001 -- best effort; never fail the delivery job
            # The owner already has the message; a failed courtesy email is only logged.
            logger.exception("Confirmation email could not be sent")
