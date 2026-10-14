"""The admin's view of contact-form messages (Phase 6.3): read state, retry, delete."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from src.core.exceptions import ConflictError, NotFoundError
from src.models.contact_message_model import ContactMessage, ContactMessageStatus
from src.repositories.contact_message_repo import ContactMessageRepository

PREVIEW_CHARS = 160
RECENT_DAYS = 7


def message_preview(text: str) -> str:
    return text[:PREVIEW_CHARS]


class ContactAdminService:
    def __init__(
        self,
        repository: ContactMessageRepository,
        now: Callable[[], datetime] | None = None,
    ):
        self.repository = repository
        self.now = now or (lambda: datetime.now(UTC))

    async def list(
        self, *, limit: int, offset: int, q: str | None, status: str | None, unread: bool
    ) -> tuple[list[ContactMessage], int]:
        return await self.repository.list(
            limit=limit, offset=offset, q=q, status=status, unread=unread
        )

    async def stats(self) -> dict[str, int]:
        counts = await self.repository.counts(self.now() - timedelta(days=RECENT_DAYS))
        return {
            "unread": counts.unread,
            "failed": counts.failed,
            "total": counts.total,
            "last_7_days": counts.recent,
        }

    async def get(self, message_id: UUID) -> ContactMessage:
        record = await self.repository.get(message_id)
        if record is None:
            raise NotFoundError("Message not found.")
        return record

    async def set_read(self, message_id: UUID, read: bool) -> ContactMessage:
        record = await self.get(message_id)
        if read and record.read_at is None:
            await self.repository.set_read_at(record, self.now())
        elif not read and record.read_at is not None:
            await self.repository.set_read_at(record, None)
        return record

    async def delete(self, message_id: UUID) -> ContactMessage:
        record = await self.get(message_id)
        await self.repository.delete(record)
        return record

    async def retry(self, message_id: UUID) -> ContactMessage:
        """failed -> pending; the caller then schedules the same delivery as the form."""
        record = await self.get(message_id)
        if record.status != ContactMessageStatus.FAILED or not (
            await self.repository.mark_pending_for_retry(message_id)
        ):
            raise ConflictError(
                "Only a message whose email failed can be retried.",
                errors={"code": "not_failed"},
            )
        return record
