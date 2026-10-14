"""Importing this package registers every model with Base.metadata (used by Alembic)."""

from src.models.contact_message_model import ContactMessage, ContactMessageStatus
from src.models.content_model import CareerEntry, Project, SiteProfile, SkillGroup
from src.models.pitch_run_model import PitchRunRecord
from src.models.pitch_usage_model import PitchUsage
from src.models.user_model import User

__all__ = [
    "CareerEntry",
    "ContactMessage",
    "ContactMessageStatus",
    "PitchRunRecord",
    "PitchUsage",
    "Project",
    "SiteProfile",
    "SkillGroup",
    "User",
]
