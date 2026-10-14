"""Pitch history repository and recorder against a real, throwaway Postgres.

Opt-in, like tests/test_migrations.py: set TEST_DATABASE_URL to an empty local database.
It migrates that database to head and writes to it, so non-local hosts are refused.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.models.pitch_run_model import PitchRunRecord
from src.models.pitch_usage_model import PitchUsage
from src.repositories.pitch_run_repo import PitchRunRepository
from src.repositories.pitch_usage_repo import PitchUsageRepository
from src.services.pitch.history import DbPitchRecorder, PitchRunResult
from tests.test_migrations import TEST_DATABASE_URL, _alembic

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")

NOON = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def _row(minutes_ago: int, company: str, role: str, outcome: str, cost: str) -> PitchRunRecord:
    return PitchRunRecord(
        id=uuid.uuid4(),
        company=company,
        role=role,
        outcome=outcome,
        pitch_text=f"Hi {company}" if outcome in {"completed", "cached"} else None,
        error_code="timeout" if outcome == "timeout" else None,
        duration_ms=1000,
        input_tokens=100,
        output_tokens=50,
        searches=1,
        estimated_cost_usd=Decimal(cost),
        created_at=NOON - timedelta(minutes=minutes_ago),
    )


@pytest.mark.asyncio
async def test_repository_and_recorder(tmp_path):
    migrated = _alembic("upgrade", "head", tmp_path=tmp_path)  # also refuses non-local hosts
    assert migrated.returncode == 0, migrated.stderr

    engine = create_async_engine(TEST_DATABASE_URL)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with sessions() as session:
            await session.execute(delete(PitchRunRecord))
            await session.execute(delete(PitchUsage))
            await session.commit()

        rows = [
            _row(1, "Spotify", "Backend engineer", "completed", "0.010000"),
            _row(2, "Zalando", "Platform Engineer", "cached", "0"),
            _row(3, "Monzo", "SRE", "timeout", "0.002000"),
            _row(4, "50% Off Ltd", "Growth_hacker", "completed", "0.001000"),
            _row(60 * 24 * 2, "Stripe", "API engineer", "upstream_error", "0.003000"),
        ]
        async with sessions() as session:
            repo = PitchRunRepository(session)
            for row in rows:
                await repo.add(row)

        async with sessions() as session:
            repo = PitchRunRepository(session)

            items, total = await repo.list(limit=2, offset=0)
            assert total == 5
            assert [r.company for r in items] == ["Spotify", "Zalando"]  # newest first
            items, _ = await repo.list(limit=2, offset=2)
            assert [r.company for r in items] == ["Monzo", "50% Off Ltd"]

            items, total = await repo.list(limit=20, offset=0, q="engineer")
            assert {r.company for r in items} == {"Spotify", "Zalando", "Stripe"}  # role match
            items, _ = await repo.list(limit=20, offset=0, q="SPOT")
            assert [r.company for r in items] == ["Spotify"]  # case-insensitive
            items, _ = await repo.list(limit=20, offset=0, q="50%")
            assert [r.company for r in items] == ["50% Off Ltd"]  # % is literal, not a wildcard
            items, _ = await repo.list(limit=20, offset=0, q="h_ck")
            assert items == []  # _ is literal too
            items, _ = await repo.list(limit=20, offset=0, outcome="timeout")
            assert [r.company for r in items] == ["Monzo"]

            today = await repo.counts_since(datetime(2026, 10, 14, tzinfo=UTC))
            assert (today.runs, today.cached, today.failed) == (4, 1, 1)
            assert today.estimated_cost_usd == Decimal("0.013")
            window = await repo.counts_since(datetime(2026, 9, 15, tzinfo=UTC))
            assert (window.runs, window.failed) == (5, 2)

            record = await repo.get(rows[2].id)
            assert record is not None and record.outcome == "timeout"
            await repo.delete(record)
            assert await repo.get(rows[2].id) is None

        async with sessions() as session:
            usage = PitchUsageRepository(session)
            assert await usage.runs_on(date(2026, 10, 14)) == 0
            await usage.increment(date(2026, 10, 14))
            await usage.increment(date(2026, 10, 14))
            assert await usage.runs_on(date(2026, 10, 14)) == 2

        recorder = DbPitchRecorder(sessions)
        recorder.record(
            PitchRunResult(
                company="Recorded Co",
                role="Engineer",
                outcome="disconnected",
                pitch_text="Hi",
                error_code=None,
                duration_ms=350,
                input_tokens=10,
                output_tokens=1,
                searches=1,
                estimated_cost_usd=Decimal("0.008"),
            )
        )
        await recorder.drain(wait_seconds=5)
        async with sessions() as session:
            stored = await session.scalar(
                select(PitchRunRecord).where(PitchRunRecord.company == "Recorded Co")
            )
            assert stored is not None
            assert stored.outcome == "disconnected"
            assert stored.created_at is not None
    finally:
        await engine.dispose()
