from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import RepositoryError
from src.models.contact_message_model import ContactMessage, ContactMessageStatus

MAX_ERROR_LENGTH = 500


@dataclass(frozen=True)
class ContactMessageCounts:
    unread: int = 0
    failed: int = 0
    total: int = 0
    recent: int = 0


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

    # --- Admin (Phase 6.3) ---------------------------------------------------------------

    @staticmethod
    def _filtered(statement: Select, q: str | None, status: str | None, unread: bool) -> Select:
        if q:
            needle = q.lower()
            statement = statement.where(
                or_(
                    func.lower(ContactMessage.name).contains(needle, autoescape=True),
                    func.lower(ContactMessage.email).contains(needle, autoescape=True),
                    func.lower(ContactMessage.message).contains(needle, autoescape=True),
                )
            )
        if status:
            statement = statement.where(ContactMessage.status == status)
        if unread:
            statement = statement.where(ContactMessage.read_at.is_(None))
        return statement

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        q: str | None = None,
        status: str | None = None,
        unread: bool = False,
    ) -> tuple[list[ContactMessage], int]:
        """Newest first, plus the total matching the filters (for pagination)."""
        try:
            total = await self.session.scalar(
                self._filtered(select(func.count()).select_from(ContactMessage), q, status, unread)
            )
            rows = await self.session.scalars(
                self._filtered(select(ContactMessage), q, status, unread)
                .order_by(ContactMessage.created_at.desc(), ContactMessage.id.desc())
                .limit(limit)
                .offset(offset)
            )
            return list(rows), int(total or 0)
        except Exception as e:
            raise RepositoryError("Error listing contact messages.") from e

    async def counts(self, recent_since: datetime) -> ContactMessageCounts:
        """Unread, failed and total messages, and how many arrived at or after `recent_since`."""
        statement = select(
            func.count(case((ContactMessage.read_at.is_(None), 1))),
            func.count(case((ContactMessage.status == ContactMessageStatus.FAILED.value, 1))),
            func.count(),
            func.count(case((ContactMessage.created_at >= recent_since, 1))),
        ).select_from(ContactMessage)
        try:
            unread, failed, total, recent = (await self.session.execute(statement)).one()
            return ContactMessageCounts(int(unread), int(failed), int(total), int(recent))
        except Exception as e:
            raise RepositoryError("Error counting contact messages.") from e

    async def get(self, message_id: UUID) -> ContactMessage | None:
        try:
            return await self.session.get(ContactMessage, message_id)
        except Exception as e:
            raise RepositoryError("Error retrieving a contact message.") from e

    async def set_read_at(self, record: ContactMessage, read_at: datetime | None) -> None:
        try:
            record.read_at = read_at
            await self.session.commit()
        except Exception as e:
            await self.session.rollback()
            raise RepositoryError("Error updating contact message.") from e

    async def delete(self, record: ContactMessage) -> None:
        try:
            await self.session.delete(record)
            await self.session.commit()
        except Exception as e:
            await self.session.rollback()
            raise RepositoryError("Error deleting a contact message.") from e

    async def mark_pending_for_retry(self, message_id: UUID) -> bool:
        """failed -> pending, atomically, so two retries can't both send the email.

        Returns False when the message isn't (or is no longer) failed.
        """
        try:
            result = await self.session.execute(
                update(ContactMessage)
                .where(
                    ContactMessage.id == message_id,
                    ContactMessage.status == ContactMessageStatus.FAILED.value,
                )
                .values(status=ContactMessageStatus.PENDING, error=None)
                .execution_options(synchronize_session="fetch")
            )
            await self.session.commit()
            return result.rowcount == 1
        except Exception as e:
            await self.session.rollback()
            raise RepositoryError("Error updating contact message.") from e
