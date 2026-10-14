"""Contact-message admin queries against a real, throwaway Postgres (Phase 6.3).

Opt-in, like tests/test_migrations.py: set TEST_DATABASE_URL to an empty local database.
It migrates that database to head and writes to it, so non-local hosts are refused.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.models.contact_message_model import ContactMessage, ContactMessageStatus
from src.repositories.contact_message_repo import ContactMessageRepository
from tests.test_migrations import TEST_DATABASE_URL, _alembic

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")

NOW = datetime.now(UTC)


def _row(minutes_ago: int, name: str, email: str, message: str, **extra) -> ContactMessage:
    return ContactMessage(
        id=uuid.uuid4(),
        name=name,
        email=email,
        message=message,
        created_at=NOW - timedelta(minutes=minutes_ago),
        **extra,
    )


@pytest.mark.asyncio
async def test_admin_queries(tmp_path):
    migrated = _alembic("upgrade", "head", tmp_path=tmp_path)  # also refuses non-local hosts
    assert migrated.returncode == 0, migrated.stderr

    engine = create_async_engine(TEST_DATABASE_URL)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with sessions() as session:
            await session.execute(delete(ContactMessage))
            await session.commit()

        rows = [
            _row(1, "Ada Lovelace", "ada@example.com", "Hello", status="sent"),
            _row(2, "Grace", "grace@navy.example", "Retry me", status="failed", error="SMTP"),
            _row(3, "Linus", "linus@example.com", "About a KERNEL role", status="sent"),
            _row(4, "Promo", "promo@example.com", "50% off_today", status="pending"),
            _row(60 * 24 * 10, "Old Timer", "old@example.com", "Long ago", status="sent"),
        ]
        async with sessions() as session:
            session.add_all(rows)
            await session.commit()

        async with sessions() as session:
            repo = ContactMessageRepository(session)

            # A new submission starts unread.
            created = await repo.create(name="Fresh", email="fresh@example.com", message="Hi")
            assert created.read_at is None
            await repo.delete(created)

            items, total = await repo.list(limit=2, offset=0)
            assert total == 5
            assert [m.name for m in items] == ["Ada Lovelace", "Grace"]  # newest first
            items, _ = await repo.list(limit=2, offset=2)
            assert [m.name for m in items] == ["Linus", "Promo"]

            items, _ = await repo.list(limit=20, offset=0, q="LOVELACE")
            assert [m.name for m in items] == ["Ada Lovelace"]  # name, case-insensitive
            items, _ = await repo.list(limit=20, offset=0, q="navy.EXAMPLE")
            assert [m.name for m in items] == ["Grace"]  # email
            items, _ = await repo.list(limit=20, offset=0, q="kernel")
            assert [m.name for m in items] == ["Linus"]  # message
            items, _ = await repo.list(limit=20, offset=0, q="50%")
            assert [m.name for m in items] == ["Promo"]  # % is literal, not a wildcard
            items, _ = await repo.list(limit=20, offset=0, q="o_d")
            assert items == []  # _ is literal too ("Old" would match o_d as a wildcard)
            items, _ = await repo.list(limit=20, offset=0, status="failed")
            assert [m.name for m in items] == ["Grace"]

            ada = await repo.get(rows[0].id)
            assert ada is not None and ada.read_at is None
            await repo.set_read_at(ada, NOW)
            await repo.set_read_at(await repo.get(rows[2].id), NOW)

            items, total = await repo.list(limit=20, offset=0, unread=True)
            assert [m.name for m in items] == ["Grace", "Promo", "Old Timer"]
            assert total == 3

            counts = await repo.counts(NOW - timedelta(days=7))
            assert (counts.unread, counts.failed, counts.total, counts.recent) == (3, 1, 5, 4)

            await repo.set_read_at(ada, None)
            assert (await repo.counts(NOW - timedelta(days=7))).unread == 4

            # Retry: only a failed message moves to pending, and only once.
            assert await repo.mark_pending_for_retry(rows[0].id) is False  # sent
            assert await repo.mark_pending_for_retry(rows[1].id) is True
            grace = await repo.get(rows[1].id)
            assert (grace.status, grace.error) == (ContactMessageStatus.PENDING, None)
            assert await repo.mark_pending_for_retry(rows[1].id) is False  # already pending
            assert await repo.mark_pending_for_retry(uuid.uuid4()) is False

            await repo.delete(grace)
            assert await repo.get(rows[1].id) is None
    finally:
        await engine.dispose()
