from datetime import date

from sqlalchemy import Date, Integer
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base


class PitchUsage(Base):
    """How many paid Pitch Me runs happened on a UTC day (the daily cap is enforced on this)."""

    __tablename__ = "pitch_usage"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    runs: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
