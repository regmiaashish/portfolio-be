import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base


class PitchOutcome(StrEnum):
    COMPLETED = "completed"
    CACHED = "cached"
    TIMEOUT = "timeout"
    UPSTREAM_ERROR = "upstream_error"
    INTERNAL_ERROR = "internal_error"
    DISCONNECTED = "disconnected"


FAILED_OUTCOMES = (PitchOutcome.TIMEOUT, PitchOutcome.UPSTREAM_ERROR, PitchOutcome.INTERNAL_ERROR)


class PitchRunRecord(Base):
    """One Pitch Me run that started streaming, for the admin's Pitch history.

    Deliberately holds no IP address or other visitor data: only what the visitor typed
    (company, role) and what the pipeline produced.
    """

    __tablename__ = "pitch_runs"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('completed', 'cached', 'timeout', 'upstream_error', "
            "'internal_error', 'disconnected')",
            name="outcome",
        ),
        Index("ix_pitch_runs_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(80))
    outcome: Mapped[str] = mapped_column(String(20))
    # Full text when completed or cached; whatever was streamed so far otherwise.
    pitch_text: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(20))
    duration_ms: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    searches: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
