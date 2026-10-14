"""In-memory stand-ins for the content repositories and S3 storage (no database, no AWS)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from src.models.content_model import CareerEntry, Project, SiteProfile, SkillGroup
from src.repositories.content_repo import PROFILE_ID
from src.services.content_service import ContentService
from src.services.storage_service import PresignedUpload

_clock = [datetime(2026, 10, 14, 12, 0, tzinfo=UTC)]


def _now() -> datetime:
    # Strictly increasing timestamps, so "latest update" assertions are deterministic.
    _clock[0] += timedelta(seconds=1)
    return _clock[0]


class FakeProfileRepository:
    def __init__(self):
        self.profile: SiteProfile | None = None

    async def get(self) -> SiteProfile | None:
        return self.profile

    async def save(self, **fields: Any) -> SiteProfile:
        if self.profile is None:
            self.profile = SiteProfile(id=PROFILE_ID, **fields)
        else:
            for name, value in fields.items():
                setattr(self.profile, name, value)
        self.profile.updated_at = _now()
        return self.profile


class FakeOrderedRepository:
    model: type

    def __init__(self):
        self.items: dict[uuid.UUID, Any] = {}

    async def list_all(self) -> list:
        return sorted(self.items.values(), key=lambda item: (item.position, item.created_at))

    async def get(self, item_id):
        return self.items.get(item_id)

    async def create(self, **fields: Any):
        position = max((item.position for item in self.items.values()), default=-1) + 1
        stamp = _now()
        item = self.model(
            id=uuid.uuid4(), position=position, created_at=stamp, updated_at=stamp, **fields
        )
        self.items[item.id] = item
        return item

    async def update(self, item, **fields: Any):
        for name, value in fields.items():
            setattr(item, name, value)
        item.updated_at = _now()
        return item

    async def delete(self, item) -> None:
        del self.items[item.id]

    async def reorder(self, ids: list[uuid.UUID]) -> None:
        for position, item_id in enumerate(ids):
            self.items[item_id].position = position


class FakeProjectRepository(FakeOrderedRepository):
    model = Project

    async def list_published(self) -> list[Project]:
        return [project for project in await self.list_all() if project.published]

    async def get_by_slug(self, slug: str) -> Project | None:
        return next((p for p in self.items.values() if p.slug == slug), None)


class FakeCareerRepository(FakeOrderedRepository):
    model = CareerEntry


class FakeSkillGroupRepository(FakeOrderedRepository):
    model = SkillGroup


class FakeStorage:
    """Records presign and delete calls; set `fail_delete` to simulate an S3 outage."""

    def __init__(self):
        self.presigned: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.fail_delete = False

    def presign_upload(self, key: str, content_type: str, max_bytes: int) -> PresignedUpload:
        self.presigned.append({"key": key, "content_type": content_type, "max_bytes": max_bytes})
        return PresignedUpload(
            url="https://fake-bucket.s3.amazonaws.com/",
            fields={"key": key, "Content-Type": content_type, "policy": "fake"},
        )

    async def delete(self, key: str) -> None:
        if self.fail_delete:
            raise ConnectionError("S3 is down")
        self.deleted.append(key)


class ContentFakes:
    def __init__(self, storage: FakeStorage | None):
        self.profiles = FakeProfileRepository()
        self.projects = FakeProjectRepository()
        self.career = FakeCareerRepository()
        self.skills = FakeSkillGroupRepository()
        self.storage = storage

    def service(self) -> ContentService:
        return ContentService(self.profiles, self.projects, self.career, self.skills, self.storage)
