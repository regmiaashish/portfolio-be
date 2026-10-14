from datetime import date

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import RepositoryError
from src.models.pitch_usage_model import PitchUsage


class PitchUsageRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def increment(self, day: date) -> int:
        """Count one more run for `day` and return the new total.

        A single INSERT ... ON CONFLICT DO UPDATE ... RETURNING, so concurrent requests on
        different Gunicorn workers can never both read the same count.
        """
        statement = (
            insert(PitchUsage)
            .values(day=day, runs=1)
            .on_conflict_do_update(
                index_elements=[PitchUsage.day], set_={"runs": PitchUsage.runs + 1}
            )
            .returning(PitchUsage.runs)
        )
        try:
            runs = (await self.session.execute(statement)).scalar_one()
            await self.session.commit()
            return runs
        except Exception as e:
            await self.session.rollback()
            raise RepositoryError("Error recording Pitch Me usage.") from e
