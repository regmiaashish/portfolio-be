"""Pitch history: recording finished runs, and the admin's read/delete/stats views.

Recording must never break or delay the visitor's stream. `PitchRecorder.record()` is a plain,
non-blocking call: the database recorder schedules its own write task (with its own session) and
logs failures, so the stream's `finally` never awaits I/O and a disconnect can't cancel the write.
"""

import asyncio
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.config import settings
from src.core.exceptions import NotFoundError
from src.models.pitch_run_model import PitchRunRecord
from src.repositories.pitch_run_repo import PitchRunCounts, PitchRunRepository
from src.repositories.pitch_usage_repo import PitchUsageRepository

PREVIEW_CHARS = 160
STATS_WINDOW_DAYS = 30


@dataclass(frozen=True)
class PitchRunResult:
    company: str
    role: str
    outcome: str
    pitch_text: str | None
    error_code: str | None
    duration_ms: int
    input_tokens: int | None
    output_tokens: int | None
    searches: int
    estimated_cost_usd: Decimal | None


class PitchRecorder(Protocol):
    def record(self, result: PitchRunResult) -> None: ...


class DbPitchRecorder:
    """Writes each result in a background task with its own session."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory
        self._pending: set[asyncio.Task[None]] = set()

    def record(self, result: PitchRunResult) -> None:
        try:
            task = asyncio.get_running_loop().create_task(self._write(result))
        except RuntimeError:  # no running loop (never the case inside a request)
            logger.warning("Pitch history not recorded: no event loop")
            return
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _write(self, result: PitchRunResult) -> None:
        try:
            async with self.session_factory() as session:
                await PitchRunRepository(session).add(PitchRunRecord(**asdict(result)))
        except Exception:  # noqa: BLE001 -- history is best effort; the visitor already has their answer
            logger.exception("Could not record a Pitch Me run in the history")

    async def drain(self, wait_seconds: float = 5.0) -> None:
        """Wait (bounded) for writes still in flight, e.g. on shutdown."""
        if self._pending:
            await asyncio.wait(set(self._pending), timeout=wait_seconds)


def utc_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


class PitchHistoryService:
    def __init__(
        self,
        runs: PitchRunRepository,
        usage: PitchUsageRepository,
        today: Callable[[], date] | None = None,
    ):
        self.runs = runs
        self.usage = usage
        self.today = today or (lambda: datetime.now(UTC).date())

    async def list(
        self, *, limit: int, offset: int, q: str | None, outcome: str | None
    ) -> tuple[list[PitchRunRecord], int]:
        return await self.runs.list(limit=limit, offset=offset, q=q, outcome=outcome)

    async def get(self, run_id: UUID) -> PitchRunRecord:
        record = await self.runs.get(run_id)
        if record is None:
            raise NotFoundError("Pitch run not found.")
        return record

    async def delete(self, run_id: UUID) -> PitchRunRecord:
        record = await self.get(run_id)
        await self.runs.delete(record)
        return record

    async def stats(self) -> dict:
        today = self.today()
        today_counts: PitchRunCounts = await self.runs.counts_since(utc_midnight(today))
        window = await self.runs.counts_since(
            utc_midnight(today - timedelta(days=STATS_WINDOW_DAYS - 1))
        )
        return {
            "enabled": settings.pitch_enabled,
            "daily_cap": settings.pitch_daily_cap,
            "today": {
                # The same counter the daily cap is enforced on (paid runs, cache hits excluded).
                "paid_runs": await self.usage.runs_on(today),
                "cached": today_counts.cached,
                "failed": today_counts.failed,
            },
            "last_30_days": {
                "runs": window.runs,
                "cached": window.cached,
                "failed": window.failed,
                "estimated_cost_usd": float(window.estimated_cost_usd),
            },
        }


def preview(text: str | None) -> str | None:
    return text[:PREVIEW_CHARS] if text else None
