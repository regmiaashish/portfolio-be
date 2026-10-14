"""Content repositories and the seed script against a real, throwaway Postgres (Phase 4.5).

Opt-in, like tests/test_migrations.py: set TEST_DATABASE_URL to an empty local database.
It migrates that database to head and writes to it, so non-local hosts are refused.
"""

import sys
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.exceptions import ConflictError
from src.models.content_model import CareerEntry, Project, SiteProfile, SkillGroup
from src.repositories.content_repo import (
    CareerRepository,
    ProfileRepository,
    ProjectRepository,
    SkillGroupRepository,
)
from src.services.content_service import ContentService
from tests.test_migrations import TEST_DATABASE_URL, _alembic

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from seed_content import load_seed, seed_content

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")


def _service(session: AsyncSession) -> ContentService:
    return ContentService(
        ProfileRepository(session),
        ProjectRepository(session),
        CareerRepository(session),
        SkillGroupRepository(session),
        storage=None,
    )


@pytest.mark.asyncio
async def test_seed_is_idempotent_and_repositories_work(tmp_path):
    migrated = _alembic("upgrade", "head", tmp_path=tmp_path)  # also refuses non-local hosts
    assert migrated.returncode == 0, migrated.stderr

    engine = create_async_engine(TEST_DATABASE_URL)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with sessions() as session:
            for model in (Project, CareerEntry, SkillGroup, SiteProfile):
                await session.execute(delete(model))
            await session.commit()

        seed = load_seed()
        async with sessions() as session:
            first = await seed_content(_service(session), seed)
        async with sessions() as session:
            second = await seed_content(_service(session), seed)

        assert first == [
            "profile: created",
            "projects: created 5",
            "career: created 3",
            "skills: created 3",
        ]
        assert all(line.endswith("left unchanged") for line in second)

        async with sessions() as session:
            counts = {
                model.__tablename__: await session.scalar(select(func.count()).select_from(model))
                for model in (SiteProfile, Project, CareerEntry, SkillGroup)
            }
            assert counts == {
                "site_profile": 1,
                "projects": 5,
                "career_entries": 3,
                "skill_groups": 3,
            }

            service = _service(session)
            content = await service.public_content()
            assert content.projects[0].slug == "fagoon-upgrade"
            assert content.profile.phone is not None

            # The unique index is the last line of defence against a duplicate slug.
            with pytest.raises(ConflictError):
                await service.projects.create(
                    **{"slug": "jarvis", "name": "x", "category": "x", "summary": "x"}
                )

            projects = await service.projects.list_all()
            reversed_ids = [p.id for p in reversed(projects)]
            await service.reorder_projects(reversed_ids)

        async with sessions() as session:
            reordered = await ProjectRepository(session).list_all()
            assert [p.id for p in reordered] == reversed_ids
            assert [p.position for p in reordered] == list(range(5))

        # Phase 5: the resume_url column (migration 0005) round-trips, and the sitemap reads
        # only published projects.
        async with sessions() as session:
            service = _service(session)
            profile_input = seed["profile"].model_copy(
                update={"resume_url": "https://example.com/cv.pdf"}
            )
            saved = await service.update_profile(profile_input)
            assert saved.resume_url == "https://example.com/cv.pdf"
            home, entries = await service.sitemap_entries()
            assert home is not None
            reordered_slugs = [p.slug for p in await service.projects.list_published()]
            assert [slug for slug, _ in entries] == reordered_slugs
            assert len(entries) == 5
    finally:
        await engine.dispose()
