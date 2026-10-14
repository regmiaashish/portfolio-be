"""Everything the public site shows, editable from the admin (Phase 4.5).

See docs/ADMIN_CMS_CONTRACT.md. Lists are ordered by `position` (ascending).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base


class _Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SiteProfile(Base):
    """The single profile row (id is always 1)."""

    __tablename__ = "site_profile"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(100))
    location: Mapped[str] = mapped_column(String(60))
    tagline: Mapped[str] = mapped_column(String(160))
    summary: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(String(320))
    phone_display: Mapped[str | None] = mapped_column(String(40))
    phone_href: Mapped[str | None] = mapped_column(String(60))
    socials: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Project(_Timestamps, Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(60))
    summary: Mapped[str] = mapped_column(String(300))
    stack: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    repo_url: Mapped[str | None] = mapped_column(String(300))
    demo_url: Mapped[str | None] = mapped_column(String(300))
    image_key: Mapped[str | None] = mapped_column(String(300))
    image_alt: Mapped[str | None] = mapped_column(String(200))
    image_width: Mapped[int | None] = mapped_column(Integer)
    image_height: Mapped[int | None] = mapped_column(Integer)
    case_study: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    published: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class CareerEntry(_Timestamps, Base):
    __tablename__ = "career_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role: Mapped[str] = mapped_column(String(100))
    company: Mapped[str] = mapped_column(String(100))
    period: Mapped[str] = mapped_column(String(20))
    summary: Mapped[str] = mapped_column(String(600))
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class SkillGroup(_Timestamps, Base):
    __tablename__ = "skill_groups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(300))
    skills: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
