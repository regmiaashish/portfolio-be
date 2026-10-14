from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import RepositoryError
from src.models.pitch_run_model import FAILED_OUTCOMES, PitchOutcome, PitchRunRecord


@dataclass(frozen=True)
class PitchRunCounts:
    runs: int = 0
    cached: int = 0
    failed: int = 0
    estimated_cost_usd: Decimal = Decimal(0)


class PitchRunRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, record: PitchRunRecord) -> PitchRunRecord:
        try:
            self.session.add(record)
            await self.session.commit()
            return record
        except Exception as e:
            await self.session.rollback()
            raise RepositoryError("Error recording a Pitch Me run.") from e

    @staticmethod
    def _filtered(statement: Select, q: str | None, outcome: str | None) -> Select:
        if q:
            needle = q.lower()
            statement = statement.where(
                or_(
                    func.lower(PitchRunRecord.company).contains(needle, autoescape=True),
                    func.lower(PitchRunRecord.role).contains(needle, autoescape=True),
                )
            )
        if outcome:
            statement = statement.where(PitchRunRecord.outcome == outcome)
        return statement

    async def list(
        self, *, limit: int, offset: int, q: str | None = None, outcome: str | None = None
    ) -> tuple[list[PitchRunRecord], int]:
        """Newest first, plus the total matching the filters (for pagination)."""
        try:
            total = await self.session.scalar(
                self._filtered(select(func.count()).select_from(PitchRunRecord), q, outcome)
            )
            rows = await self.session.scalars(
                self._filtered(select(PitchRunRecord), q, outcome)
                .order_by(PitchRunRecord.created_at.desc(), PitchRunRecord.id.desc())
                .limit(limit)
                .offset(offset)
            )
            return list(rows), int(total or 0)
        except Exception as e:
            raise RepositoryError("Error listing Pitch Me runs.") from e

    async def get(self, run_id: UUID) -> PitchRunRecord | None:
        try:
            return await self.session.get(PitchRunRecord, run_id)
        except Exception as e:
            raise RepositoryError("Error retrieving a Pitch Me run.") from e

    async def delete(self, record: PitchRunRecord) -> None:
        try:
            await self.session.delete(record)
            await self.session.commit()
        except Exception as e:
            await self.session.rollback()
            raise RepositoryError("Error deleting a Pitch Me run.") from e

    async def counts_since(self, since: datetime) -> PitchRunCounts:
        """Runs created at or after `since`, split into cached and failed, with total spend."""
        failed = [outcome.value for outcome in FAILED_OUTCOMES]
        statement = select(
            func.count(),
            func.count(case((PitchRunRecord.outcome == PitchOutcome.CACHED.value, 1))),
            func.count(case((PitchRunRecord.outcome.in_(failed), 1))),
            func.coalesce(func.sum(PitchRunRecord.estimated_cost_usd), 0),
        ).where(PitchRunRecord.created_at >= since)
        try:
            runs, cached, failed_count, cost = (await self.session.execute(statement)).one()
            return PitchRunCounts(int(runs), int(cached), int(failed_count), Decimal(cost))
        except Exception as e:
            raise RepositoryError("Error summarising Pitch Me runs.") from e
