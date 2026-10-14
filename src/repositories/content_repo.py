from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, RepositoryError
from src.models.content_model import CareerEntry, Project, SiteProfile, SkillGroup

PROFILE_ID = 1


class ProfileRepository:
    """The single site_profile row."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self) -> SiteProfile | None:
        try:
            return await self.session.get(SiteProfile, PROFILE_ID)
        except Exception as e:
            raise RepositoryError("Error loading the profile.") from e

    async def save(self, **fields: Any) -> SiteProfile:
        try:
            profile = await self.session.get(SiteProfile, PROFILE_ID)
            if profile is None:
                profile = SiteProfile(id=PROFILE_ID, **fields)
                self.session.add(profile)
            else:
                for name, value in fields.items():
                    setattr(profile, name, value)
            await self.session.commit()
            await self.session.refresh(profile)
            return profile
        except Exception as e:
            await self.session.rollback()
            raise RepositoryError("Error saving the profile.") from e


class OrderedRepository[M: (Project, CareerEntry, SkillGroup)]:
    """CRUD plus ordering for a table whose rows the admin can reorder (`position`)."""

    model: type[M]
    label: str

    def __init__(self, session: AsyncSession):
        self.session = session

    def _ordered(self):
        return select(self.model).order_by(self.model.position, self.model.created_at)

    async def list_all(self) -> list[M]:
        try:
            return list((await self.session.execute(self._ordered())).scalars().all())
        except Exception as e:
            raise RepositoryError(f"Error loading {self.label}.") from e

    async def get(self, item_id: UUID) -> M | None:
        try:
            return await self.session.get(self.model, item_id)
        except Exception as e:
            raise RepositoryError(f"Error loading {self.label}.") from e

    async def create(self, **fields: Any) -> M:
        try:
            last = await self.session.scalar(select(func.max(self.model.position)))
            item = self.model(position=(last + 1) if last is not None else 0, **fields)
            self.session.add(item)
            await self.session.commit()
            await self.session.refresh(item)
            return item
        except IntegrityError as e:
            await self.session.rollback()
            raise self._conflict() from e
        except Exception as e:
            await self.session.rollback()
            raise RepositoryError(f"Error creating {self.label}.") from e

    async def update(self, item: M, **fields: Any) -> M:
        try:
            for name, value in fields.items():
                setattr(item, name, value)
            await self.session.commit()
            await self.session.refresh(item)
            return item
        except IntegrityError as e:
            await self.session.rollback()
            raise self._conflict() from e
        except Exception as e:
            await self.session.rollback()
            raise RepositoryError(f"Error updating {self.label}.") from e

    async def delete(self, item: M) -> None:
        try:
            await self.session.delete(item)
            await self.session.commit()
        except Exception as e:
            await self.session.rollback()
            raise RepositoryError(f"Error deleting {self.label}.") from e

    async def reorder(self, ids: list[UUID]) -> None:
        """Set positions 0..n-1 in the given order, in one transaction."""
        try:
            for position, item_id in enumerate(ids):
                await self.session.execute(
                    update(self.model).where(self.model.id == item_id).values(position=position)
                )
            await self.session.commit()
        except Exception as e:
            await self.session.rollback()
            raise RepositoryError(f"Error reordering {self.label}.") from e

    def _conflict(self) -> ConflictError:
        return ConflictError(f"That conflicts with an existing {self.label} entry.")


class ProjectRepository(OrderedRepository[Project]):
    model = Project
    label = "projects"

    async def list_published(self) -> list[Project]:
        try:
            statement = self._ordered().where(Project.published.is_(True))
            return list((await self.session.execute(statement)).scalars().all())
        except Exception as e:
            raise RepositoryError("Error loading projects.") from e

    async def get_by_slug(self, slug: str) -> Project | None:
        try:
            return await self.session.scalar(select(Project).where(Project.slug == slug))
        except Exception as e:
            raise RepositoryError("Error loading projects.") from e

    def _conflict(self) -> ConflictError:
        return ConflictError("A project with this slug already exists.")


class CareerRepository(OrderedRepository[CareerEntry]):
    model = CareerEntry
    label = "career"


class SkillGroupRepository(OrderedRepository[SkillGroup]):
    model = SkillGroup
    label = "skills"
