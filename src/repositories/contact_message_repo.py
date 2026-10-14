from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import RepositoryError
from src.models.contact_message_model import ContactMessage, ContactMessageStatus

MAX_ERROR_LENGTH = 500


class ContactMessageRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, *, name: str, email: str, message: str) -> ContactMessage:
        record = ContactMessage(
            name=name, email=email, message=message, status=ContactMessageStatus.PENDING
        )
        try:
            self.session.add(record)
            await self.session.commit()
            await self.session.refresh(record)
            return record
        except Exception as e:
            await self.session.rollback()
            raise RepositoryError("Error saving contact message.") from e

    async def mark_sent(self, message_id: UUID) -> None:
        await self._set_status(message_id, ContactMessageStatus.SENT, None)

    async def mark_failed(self, message_id: UUID, error: str) -> None:
        await self._set_status(message_id, ContactMessageStatus.FAILED, error[:MAX_ERROR_LENGTH])

    async def _set_status(
        self, message_id: UUID, status: ContactMessageStatus, error: str | None
    ) -> None:
        try:
            await self.session.execute(
                update(ContactMessage)
                .where(ContactMessage.id == message_id)
                .values(status=status, error=error)
            )
            await self.session.commit()
        except Exception as e:
            await self.session.rollback()
            raise RepositoryError("Error updating contact message.") from e
