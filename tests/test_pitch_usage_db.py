"""The daily-cap counter against a real, throwaway Postgres (plan 4.2).

Opt-in, like tests/test_migrations.py: set TEST_DATABASE_URL to an empty local database.
It migrates that database to head and then writes to it, so non-local hosts are refused.
"""

import asyncio
from datetime import date

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.models.pitch_usage_model import PitchUsage
from src.repositories.pitch_usage_repo import PitchUsageRepository
from tests.test_migrations import TEST_DATABASE_URL, _alembic

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")

DAY = date(2026, 10, 14)


@pytest.mark.asyncio
async def test_increment_is_atomic_across_concurrent_sessions(tmp_path):
    migrated = _alembic("upgrade", "head", tmp_path=tmp_path)  # also refuses non-local hosts
    assert migrated.returncode == 0, migrated.stderr

    engine = create_async_engine(TEST_DATABASE_URL, pool_size=20)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with sessions() as session:
            await session.execute(delete(PitchUsage))
            await session.commit()

        async def one_run() -> int:
            async with sessions() as session:
                return await PitchUsageRepository(session).increment(DAY)

        counts = await asyncio.gather(*(one_run() for _ in range(20)))

        # Every concurrent request saw a distinct count: no two runs shared a slot under the cap.
        assert sorted(counts) == list(range(1, 21))

        async with sessions() as session:
            assert await PitchUsageRepository(session).increment(date(2026, 10, 15)) == 1
    finally:
        await engine.dispose()
