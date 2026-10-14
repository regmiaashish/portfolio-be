"""Admin contact-message responses (docs/ADMIN_CMS_CONTRACT.md, "Contact messages in the admin")."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictBool, computed_field

MessageStatusName = Literal["pending", "sent", "failed"]


class _ReadState(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    read_at: datetime | None

    @computed_field
    @property
    def read(self) -> bool:
        return self.read_at is not None


class MessageSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    email: str
    # First 160 characters of the message.
    preview: str
    status: MessageStatusName
    read: bool
    created_at: datetime


class MessageDetail(_ReadState):
    id: UUID
    name: str
    email: str
    message: str
    status: MessageStatusName
    error: str | None
    created_at: datetime


class MessageList(BaseModel):
    items: list[MessageSummary]
    total: int
    limit: int
    offset: int


class MessageStats(BaseModel):
    unread: int
    failed: int
    total: int
    last_7_days: int


class MessageReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    read: StrictBool
