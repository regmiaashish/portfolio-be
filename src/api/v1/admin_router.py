"""Content admin (Phase 4.5): superuser-only, CSRF-guarded mutations. See ADMIN_CMS_CONTRACT.md."""

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from loguru import logger

from src.core.dependencies import get_content_service, require_csrf, require_superuser
from src.models.user_model import User
from src.schemas.common_schema import ErrorResponse
from src.schemas.content_schema import (
    AdminCareerOut,
    AdminContentOut,
    AdminProjectOut,
    AdminSkillGroupOut,
    CareerInput,
    OrderInput,
    ProfileInput,
    ProfileOut,
    ProjectInput,
    SkillGroupInput,
    UploadOut,
    UploadRequest,
)
from src.services.content_service import ContentService

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    # Runs before anything else on every admin route: anonymous 401, non-superuser 403.
    dependencies=[Depends(require_superuser)],
    responses={
        401: {"model": ErrorResponse, "description": "Not signed in"},
        403: {"model": ErrorResponse, "description": "Not a superuser, or CSRF check failed"},
    },
)

_MUTATION = {"dependencies": [Depends(require_csrf)]}
_NOT_FOUND = {404: {"model": ErrorResponse, "description": "Unknown id"}}


def _audit(admin: User, action: str) -> None:
    logger.info("Admin {} {}", admin.email, action)


@router.get("/content", response_model=AdminContentOut)
async def admin_content(service: ContentService = Depends(get_content_service)) -> AdminContentOut:
    """Everything, including unpublished projects, with positions and image keys."""
    return await service.admin_content()


@router.put("/profile", response_model=ProfileOut, **_MUTATION)
async def update_profile(
    payload: ProfileInput,
    admin: User = Depends(require_superuser),
    service: ContentService = Depends(get_content_service),
) -> ProfileOut:
    profile = await service.update_profile(payload)
    _audit(admin, "updated the profile")
    return profile


# --- Projects ----------------------------------------------------------------------------


@router.post(
    "/projects",
    response_model=AdminProjectOut,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": ErrorResponse, "description": "Slug already used"}},
    **_MUTATION,
)
async def create_project(
    payload: ProjectInput,
    admin: User = Depends(require_superuser),
    service: ContentService = Depends(get_content_service),
) -> AdminProjectOut:
    project = await service.create_project(payload)
    _audit(admin, f"created project {project.slug} ({project.id})")
    return project


# Declared before /projects/{project_id} so "order" is never parsed as an id.
@router.put("/projects/order", status_code=status.HTTP_204_NO_CONTENT, **_MUTATION)
async def reorder_projects(
    payload: OrderInput,
    admin: User = Depends(require_superuser),
    service: ContentService = Depends(get_content_service),
) -> Response:
    await service.reorder_projects(payload.ids)
    _audit(admin, "reordered projects")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/projects/{project_id}",
    response_model=AdminProjectOut,
    responses={**_NOT_FOUND, 409: {"model": ErrorResponse, "description": "Slug already used"}},
    **_MUTATION,
)
async def update_project(
    project_id: UUID,
    payload: ProjectInput,
    admin: User = Depends(require_superuser),
    service: ContentService = Depends(get_content_service),
) -> AdminProjectOut:
    project = await service.update_project(project_id, payload)
    _audit(admin, f"updated project {project.slug} ({project.id})")
    return project


@router.delete(
    "/projects/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_NOT_FOUND,
    **_MUTATION,
)
async def delete_project(
    project_id: UUID,
    admin: User = Depends(require_superuser),
    service: ContentService = Depends(get_content_service),
) -> Response:
    project = await service.delete_project(project_id)
    _audit(admin, f"deleted project {project.slug} ({project_id})")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Career ------------------------------------------------------------------------------


@router.post(
    "/career", response_model=AdminCareerOut, status_code=status.HTTP_201_CREATED, **_MUTATION
)
async def create_career(
    payload: CareerInput,
    admin: User = Depends(require_superuser),
    service: ContentService = Depends(get_content_service),
) -> AdminCareerOut:
    entry = await service.create_career(payload)
    _audit(admin, f"created career entry {entry.id}")
    return entry


@router.put("/career/order", status_code=status.HTTP_204_NO_CONTENT, **_MUTATION)
async def reorder_career(
    payload: OrderInput,
    admin: User = Depends(require_superuser),
    service: ContentService = Depends(get_content_service),
) -> Response:
    await service.reorder_career(payload.ids)
    _audit(admin, "reordered career entries")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/career/{entry_id}", response_model=AdminCareerOut, responses=_NOT_FOUND, **_MUTATION)
async def update_career(
    entry_id: UUID,
    payload: CareerInput,
    admin: User = Depends(require_superuser),
    service: ContentService = Depends(get_content_service),
) -> AdminCareerOut:
    entry = await service.update_career(entry_id, payload)
    _audit(admin, f"updated career entry {entry_id}")
    return entry


@router.delete(
    "/career/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_NOT_FOUND,
    **_MUTATION,
)
async def delete_career(
    entry_id: UUID,
    admin: User = Depends(require_superuser),
    service: ContentService = Depends(get_content_service),
) -> Response:
    await service.delete_career(entry_id)
    _audit(admin, f"deleted career entry {entry_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Skills ------------------------------------------------------------------------------


@router.post(
    "/skills",
    response_model=AdminSkillGroupOut,
    status_code=status.HTTP_201_CREATED,
    **_MUTATION,
)
async def create_skill_group(
    payload: SkillGroupInput,
    admin: User = Depends(require_superuser),
    service: ContentService = Depends(get_content_service),
) -> AdminSkillGroupOut:
    group = await service.create_skill_group(payload)
    _audit(admin, f"created skill group {group.id}")
    return group


@router.put("/skills/order", status_code=status.HTTP_204_NO_CONTENT, **_MUTATION)
async def reorder_skills(
    payload: OrderInput,
    admin: User = Depends(require_superuser),
    service: ContentService = Depends(get_content_service),
) -> Response:
    await service.reorder_skills(payload.ids)
    _audit(admin, "reordered skill groups")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/skills/{group_id}", response_model=AdminSkillGroupOut, responses=_NOT_FOUND, **_MUTATION
)
async def update_skill_group(
    group_id: UUID,
    payload: SkillGroupInput,
    admin: User = Depends(require_superuser),
    service: ContentService = Depends(get_content_service),
) -> AdminSkillGroupOut:
    group = await service.update_skill_group(group_id, payload)
    _audit(admin, f"updated skill group {group_id}")
    return group


@router.delete(
    "/skills/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_NOT_FOUND,
    **_MUTATION,
)
async def delete_skill_group(
    group_id: UUID,
    admin: User = Depends(require_superuser),
    service: ContentService = Depends(get_content_service),
) -> Response:
    await service.delete_skill_group(group_id)
    _audit(admin, f"deleted skill group {group_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Uploads -----------------------------------------------------------------------------


@router.post(
    "/uploads",
    response_model=UploadOut,
    responses={503: {"model": ErrorResponse, "description": "Uploads not configured"}},
    **_MUTATION,
)
async def create_upload(
    payload: UploadRequest,
    admin: User = Depends(require_superuser),
    service: ContentService = Depends(get_content_service),
) -> UploadOut:
    upload = service.create_upload(payload)
    _audit(admin, f"requested an upload URL for {upload.key} ({payload.content_type})")
    return upload
