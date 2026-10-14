"""Site content: the public read model and every admin mutation (Phase 4.5).

Rules from docs/ADMIN_CMS_CONTRACT.md: public reads only see published projects; lists are in
`position` order; replacing or removing a project image deletes the old S3 object best-effort.
"""

import re
import unicodedata
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from loguru import logger

from src.core.config import settings
from src.core.exceptions import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    UnprocessableError,
)
from src.models.content_model import CareerEntry, Project, SiteProfile, SkillGroup
from src.repositories.content_repo import (
    CareerRepository,
    OrderedRepository,
    ProfileRepository,
    ProjectRepository,
    SkillGroupRepository,
)
from src.schemas.content_schema import (
    UPLOAD_RULES,
    AdminCareerOut,
    AdminContentOut,
    AdminImageOut,
    AdminProjectOut,
    AdminSkillGroupOut,
    CareerInput,
    CareerOut,
    ContentOut,
    ImageOut,
    PhoneOut,
    ProfileInput,
    ProfileOut,
    ProjectInput,
    ProjectOut,
    SkillGroupInput,
    SkillGroupOut,
    SocialOut,
    UploadOut,
    UploadRequest,
)
from src.services.storage_service import StorageService


def public_url(key: str) -> str:
    base = settings.s3_base_url
    return f"{base}/{key}" if base else key


def own_resume_key(url: str | None) -> str | None:
    """The S3 key if `url` is a résumé we uploaded ourselves; None for any other URL."""
    base = settings.s3_base_url
    if not url or not base:
        return None
    folder_url = f"{base}/{settings.s3_upload_prefix}resume/"
    if not url.startswith(folder_url) or len(url) == len(folder_url):
        return None
    return url[len(base) + 1 :]


def resume_filename(name: str | None) -> str:
    """ASCII-safe download name, e.g. "Aashish Regmi" -> "Aashish-Regmi-Resume.pdf"."""
    ascii_name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_name).strip("-")
    return f"{slug}-Resume.pdf" if slug else "Resume.pdf"


# --- Model -> schema ---------------------------------------------------------------------


def profile_out(profile: SiteProfile) -> ProfileOut:
    phone = None
    if profile.phone_display and profile.phone_href:
        phone = PhoneOut(display=profile.phone_display, href=profile.phone_href)
    return ProfileOut(
        name=profile.name,
        role=profile.role,
        location=profile.location,
        tagline=profile.tagline,
        summary=profile.summary,
        email=profile.email,
        phone=phone,
        socials=[SocialOut(**social) for social in profile.socials or []],
        resume_url=profile.resume_url,
    )


def _image_fields(project: Project) -> dict[str, Any] | None:
    if not project.image_key:
        return None
    return {
        "url": public_url(project.image_key),
        "alt": project.image_alt or "",
        "width": project.image_width or 1,
        "height": project.image_height or 1,
    }


def _project_fields(project: Project) -> dict[str, Any]:
    return {
        "id": project.id,
        "slug": project.slug,
        "name": project.name,
        "category": project.category,
        "summary": project.summary,
        "stack": list(project.stack or []),
        "repo_url": project.repo_url,
        "demo_url": project.demo_url,
        "case_study": project.case_study,
    }


def project_out(project: Project) -> ProjectOut:
    image = _image_fields(project)
    return ProjectOut(**_project_fields(project), image=ImageOut(**image) if image else None)


def admin_project_out(project: Project) -> AdminProjectOut:
    image = _image_fields(project)
    return AdminProjectOut(
        **_project_fields(project),
        image=AdminImageOut(**image, key=project.image_key) if image else None,
        published=project.published,
        position=project.position,
    )


def career_out(entry: CareerEntry) -> CareerOut:
    return CareerOut(
        id=entry.id,
        role=entry.role,
        company=entry.company,
        period=entry.period,
        summary=entry.summary,
    )


def admin_career_out(entry: CareerEntry) -> AdminCareerOut:
    return AdminCareerOut(**career_out(entry).model_dump(), position=entry.position)


def skill_out(group: SkillGroup) -> SkillGroupOut:
    return SkillGroupOut(
        id=group.id, title=group.title, description=group.description, skills=list(group.skills)
    )


def admin_skill_out(group: SkillGroup) -> AdminSkillGroupOut:
    return AdminSkillGroupOut(**skill_out(group).model_dump(), position=group.position)


def _latest(stamps: Sequence[datetime | None]) -> datetime | None:
    present = [stamp for stamp in stamps if stamp is not None]
    return max(present) if present else None


# --- Input -> columns --------------------------------------------------------------------


def _profile_columns(data: ProfileInput) -> dict[str, Any]:
    return {
        "name": data.name,
        "role": data.role,
        "location": data.location,
        "tagline": data.tagline,
        "summary": data.summary,
        "email": str(data.email),
        "phone_display": data.phone.display if data.phone else None,
        "phone_href": data.phone.href if data.phone else None,
        "socials": [social.model_dump() for social in data.socials],
        "resume_url": data.resume_url,
    }


def _project_columns(data: ProjectInput) -> dict[str, Any]:
    return {
        "slug": data.slug,
        "name": data.name,
        "category": data.category,
        "summary": data.summary,
        "stack": list(data.stack),
        "repo_url": data.repo_url,
        "demo_url": data.demo_url,
        "image_key": data.image.key if data.image else None,
        "image_alt": data.image.alt if data.image else None,
        "image_width": data.image.width if data.image else None,
        "image_height": data.image.height if data.image else None,
        "case_study": data.case_study.model_dump() if data.case_study else None,
        "published": data.published,
    }


class ContentService:
    def __init__(
        self,
        profiles: ProfileRepository,
        projects: ProjectRepository,
        career: CareerRepository,
        skills: SkillGroupRepository,
        storage: StorageService | None,
    ):
        self.profiles = profiles
        self.projects = projects
        self.career = career
        self.skills = skills
        self.storage = storage

    # --- Reads ---------------------------------------------------------------------------

    async def public_content(self) -> ContentOut:
        profile = await self.profiles.get()
        if profile is None:
            raise ServiceUnavailableError(
                "Site content is not available yet.", errors={"code": "content_unavailable"}
            )
        projects = await self.projects.list_published()
        career = await self.career.list_all()
        skills = await self.skills.list_all()
        updated = _latest(
            [profile.updated_at, *(item.updated_at for item in [*projects, *career, *skills])]
        )
        return ContentOut(
            profile=profile_out(profile),
            projects=[project_out(project) for project in projects],
            career=[career_out(entry) for entry in career],
            skills=[skill_out(group) for group in skills],
            updated_at=updated or profile.updated_at,
        )

    async def admin_content(self) -> AdminContentOut:
        profile = await self.profiles.get()
        projects = await self.projects.list_all()
        career = await self.career.list_all()
        skills = await self.skills.list_all()
        return AdminContentOut(
            profile=profile_out(profile) if profile else None,
            projects=[admin_project_out(project) for project in projects],
            career=[admin_career_out(entry) for entry in career],
            skills=[admin_skill_out(group) for group in skills],
            updated_at=_latest(
                [
                    profile.updated_at if profile else None,
                    *(item.updated_at for item in [*projects, *career, *skills]),
                ]
            ),
        )

    async def resume_download_url(self) -> str:
        """Where GET /resume/download redirects: a presigned attachment link for our own
        uploads, the URL itself otherwise. NotFoundError when no résumé is set."""
        profile = await self.profiles.get()
        url = profile.resume_url if profile else None
        if not url:
            raise NotFoundError(
                "No résumé is available yet.", errors={"code": "resume_unavailable"}
            )
        key = own_resume_key(url)
        if key and self.storage is not None:
            return self.storage.presign_get(key, resume_filename(profile.name), "application/pdf")
        # External link (e.g. Google Drive), or our upload with S3 not configured: best effort.
        return url

    async def has_resume(self) -> bool:
        profile = await self.profiles.get()
        return bool(profile and profile.resume_url)

    async def sitemap_entries(self) -> tuple[datetime | None, list[tuple[str, datetime | None]]]:
        """(home page lastmod, [(slug, lastmod)] for published projects). Works on an empty DB."""
        profile = await self.profiles.get()
        projects = await self.projects.list_published()
        career = await self.career.list_all()
        skills = await self.skills.list_all()
        home = _latest(
            [
                profile.updated_at if profile else None,
                *(item.updated_at for item in [*projects, *career, *skills]),
            ]
        )
        return home, [(project.slug, project.updated_at) for project in projects]

    # --- Profile -------------------------------------------------------------------------

    async def update_profile(self, data: ProfileInput) -> ProfileOut:
        current = await self.profiles.get()
        old_resume = current.resume_url if current else None
        saved = await self.profiles.save(**_profile_columns(data))
        # Replacing or removing an uploaded résumé deletes the old PDF; external links are
        # never touched.
        old_key = own_resume_key(old_resume)
        if old_key and old_resume != saved.resume_url:
            await self._delete_object(old_key)
        return profile_out(saved)

    # --- Projects ------------------------------------------------------------------------

    async def create_project(self, data: ProjectInput) -> AdminProjectOut:
        await self._ensure_slug_free(data.slug)
        return admin_project_out(await self.projects.create(**_project_columns(data)))

    async def update_project(self, project_id: UUID, data: ProjectInput) -> AdminProjectOut:
        project = await self._get(self.projects, project_id, "Project")
        await self._ensure_slug_free(data.slug, exclude=project.id)
        old_key = project.image_key
        updated = await self.projects.update(project, **_project_columns(data))
        if old_key and old_key != updated.image_key:
            await self._delete_object(old_key)
        return admin_project_out(updated)

    async def delete_project(self, project_id: UUID) -> Project:
        project = await self._get(self.projects, project_id, "Project")
        key = project.image_key
        await self.projects.delete(project)
        if key:
            await self._delete_object(key)
        return project

    async def reorder_projects(self, ids: list[UUID]) -> None:
        await self._reorder(self.projects, ids)

    # --- Career --------------------------------------------------------------------------

    async def create_career(self, data: CareerInput) -> AdminCareerOut:
        return admin_career_out(await self.career.create(**data.model_dump()))

    async def update_career(self, entry_id: UUID, data: CareerInput) -> AdminCareerOut:
        entry = await self._get(self.career, entry_id, "Career entry")
        return admin_career_out(await self.career.update(entry, **data.model_dump()))

    async def delete_career(self, entry_id: UUID) -> None:
        await self.career.delete(await self._get(self.career, entry_id, "Career entry"))

    async def reorder_career(self, ids: list[UUID]) -> None:
        await self._reorder(self.career, ids)

    # --- Skills --------------------------------------------------------------------------

    async def create_skill_group(self, data: SkillGroupInput) -> AdminSkillGroupOut:
        return admin_skill_out(await self.skills.create(**data.model_dump()))

    async def update_skill_group(self, group_id: UUID, data: SkillGroupInput) -> AdminSkillGroupOut:
        group = await self._get(self.skills, group_id, "Skill group")
        return admin_skill_out(await self.skills.update(group, **data.model_dump()))

    async def delete_skill_group(self, group_id: UUID) -> None:
        await self.skills.delete(await self._get(self.skills, group_id, "Skill group"))

    async def reorder_skills(self, ids: list[UUID]) -> None:
        await self._reorder(self.skills, ids)

    # --- Uploads -------------------------------------------------------------------------

    def create_upload(self, request: UploadRequest) -> UploadOut:
        if self.storage is None:
            raise ServiceUnavailableError(
                "Uploads are not configured.", errors={"code": "uploads_disabled"}
            )
        # The extension comes from the declared content type, never from the filename, and the
        # folder from the purpose, so a résumé key can never pass as a project image key.
        extensions, folder = UPLOAD_RULES[request.purpose]
        key = f"{settings.s3_upload_prefix}{folder}{uuid4()}.{extensions[request.content_type]}"
        max_bytes = settings.s3_max_upload_mb * 1024 * 1024
        presigned = self.storage.presign_upload(key, request.content_type, max_bytes)
        return UploadOut(
            url=presigned.url,
            fields=presigned.fields,
            key=key,
            public_url=public_url(key),
            max_bytes=max_bytes,
        )

    # --- Helpers -------------------------------------------------------------------------

    async def _get[M](self, repository: OrderedRepository, item_id: UUID, label: str) -> M:
        item = await repository.get(item_id)
        if item is None:
            raise NotFoundError(f"{label} not found.")
        return item

    async def _ensure_slug_free(self, slug: str, exclude: UUID | None = None) -> None:
        existing = await self.projects.get_by_slug(slug)
        if existing is not None and existing.id != exclude:
            raise ConflictError("A project with this slug already exists.")

    async def _reorder(self, repository: OrderedRepository, ids: list[UUID]) -> None:
        current = {item.id for item in await repository.list_all()}
        if len(ids) != len(set(ids)) or set(ids) != current:
            raise UnprocessableError(
                "ids must list every existing item exactly once.", loc=["body", "ids"]
            )
        await repository.reorder(ids)

    async def _delete_object(self, key: str) -> None:
        if self.storage is None:
            logger.warning("Not deleting {}: uploads are disabled", key)
            return
        try:
            await self.storage.delete(key)
        except Exception as exc:  # noqa: BLE001 -- best effort: never fail the admin's request
            logger.warning("Could not delete {}: {!r}", key, exc)
