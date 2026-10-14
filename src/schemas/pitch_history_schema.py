"""Admin Pitch history responses (docs/ADMIN_CMS_CONTRACT.md, "Pitch history (admin)")."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_serializer

PitchOutcomeName = Literal[
    "completed", "cached", "timeout", "upstream_error", "internal_error", "disconnected"
]


class _CostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    estimated_cost_usd: float | None = None

    @field_serializer("estimated_cost_usd")
    def _cost(self, value: float | Decimal | None) -> float | None:
        return float(value) if value is not None else None


class PitchRunSummary(_CostOut):
    id: UUID
    company: str
    role: str
    outcome: PitchOutcomeName
    duration_ms: int
    created_at: datetime
    # First 160 characters of the pitch text, or null.
    preview: str | None = None


class PitchRunDetail(_CostOut):
    id: UUID
    company: str
    role: str
    outcome: PitchOutcomeName
    pitch_text: str | None
    error_code: str | None
    duration_ms: int
    input_tokens: int | None
    output_tokens: int | None
    searches: int
    created_at: datetime


class PitchRunList(BaseModel):
    items: list[PitchRunSummary]
    total: int
    limit: int
    offset: int


class PitchTodayStats(BaseModel):
    paid_runs: int
    cached: int
    failed: int


class PitchWindowStats(BaseModel):
    runs: int
    cached: int
    failed: int
    estimated_cost_usd: float


class PitchStats(BaseModel):
    enabled: bool
    daily_cap: int
    today: PitchTodayStats
    last_30_days: PitchWindowStats
