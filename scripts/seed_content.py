"""Load the site content (src/data/seed_content.json) into an EMPTY database.

Idempotent: each table is only filled when it has no rows, so running it again changes
nothing and never overwrites edits made in the admin. Run after migrations:

    uv run python scripts/seed_content.py     (or: make seed-content)
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.database import AsyncSessionLocal, engine
from src.repositories.content_repo import (
    CareerRepository,
    ProfileRepository,
    ProjectRepository,
    SkillGroupRepository,
)
from src.schemas.content_schema import (
    CareerInput,
    ProfileInput,
    ProjectInput,
    SkillGroupInput,
)
from src.services.content_service import ContentService

SEED_FILE = Path(__file__).resolve().parents[1] / "src" / "data" / "seed_content.json"


def load_seed(path: Path = SEED_FILE) -> dict:
    """Read and validate the seed with the same rules the admin API uses."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        "profile": ProfileInput.model_validate(raw["profile"]),
        "projects": [ProjectInput.model_validate(item) for item in raw["projects"]],
        "career": [CareerInput.model_validate(item) for item in raw["career"]],
        "skills": [SkillGroupInput.model_validate(item) for item in raw["skills"]],
    }


async def seed_content(service: ContentService, seed: dict) -> list[str]:
    """Fill each empty table; return a line per table describing what happened."""
    report = []

    if await service.profiles.get() is None:
        await service.update_profile(seed["profile"])
        report.append("profile: created")
    else:
        report.append("profile: already present, left unchanged")

    for label, repository, items, create in (
        ("projects", service.projects, seed["projects"], service.create_project),
        ("career", service.career, seed["career"], service.create_career),
        ("skills", service.skills, seed["skills"], service.create_skill_group),
    ):
        if await repository.list_all():
            report.append(f"{label}: already present, left unchanged")
            continue
        for item in items:
            await create(item)
        report.append(f"{label}: created {len(items)}")

    return report


async def main() -> None:
    seed = load_seed()
    async with AsyncSessionLocal() as session:
        service = ContentService(
            ProfileRepository(session),
            ProjectRepository(session),
            CareerRepository(session),
            SkillGroupRepository(session),
            storage=None,
        )
        for line in await seed_content(service, seed):
            print(line)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
