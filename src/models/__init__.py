"""Importing this package registers every model with Base.metadata (used by Alembic)."""

from src.models.contact_message_model import ContactMessage, ContactMessageStatus
from src.models.user_model import User

__all__ = ["ContactMessage", "ContactMessageStatus", "User"]
