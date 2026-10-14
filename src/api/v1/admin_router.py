"""Content admin (Phase 4.5): superuser-only, CSRF-guarded mutations. See ADMIN_CMS_CONTRACT.md."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from loguru import logger

from src.core.dependencies import (
    get_content_service,
    get_pitch_history_service,
    require_csrf,
    require_superuser,
)
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
from src.schemas.pitch_history_schema import (
    PitchOutcomeName,
    PitchRunDetail,
    PitchRunList,
    PitchRunSummary,
    PitchStats,
)
from src.services.content_service import ContentService
from src.services.pitch.history import PitchHistoryService, preview

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
    _audit(
        admin,
        f"requested a {payload.purpose} upload URL for {upload.key} ({payload.content_type})",
    )
    return upload


# --- Pitch history ---------------------------------------------------------------------------
# /pitches/stats is declared before /pitches/{run_id} so "stats" is never parsed as an id.


@router.get("/pitches", response_model=PitchRunList)
async def list_pitch_runs(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(max_length=80)] = None,
    outcome: PitchOutcomeName | None = None,
    service: PitchHistoryService = Depends(get_pitch_history_service),
) -> PitchRunList:
    """Every Pitch Me run that started streaming, newest first."""
    search = q.strip() if q else None
    records, total = await service.list(
        limit=limit, offset=offset, q=search or None, outcome=outcome
    )
    items = [
        PitchRunSummary.model_validate(record).model_copy(
            update={"preview": preview(record.pitch_text)}
        )
        for record in records
    ]
    return PitchRunList(items=items, total=total, limit=limit, offset=offset)


@router.get("/pitches/stats", response_model=PitchStats)
async def pitch_stats(
    service: PitchHistoryService = Depends(get_pitch_history_service),
) -> PitchStats:
    """Today's paid runs against the daily cap, and the last 30 days' runs and spend (UTC)."""
    return PitchStats.model_validate(await service.stats())


@router.get("/pitches/{run_id}", response_model=PitchRunDetail, responses=_NOT_FOUND)
async def get_pitch_run(
    run_id: UUID,
    service: PitchHistoryService = Depends(get_pitch_history_service),
) -> PitchRunDetail:
    return PitchRunDetail.model_validate(await service.get(run_id))


@router.delete(
    "/pitches/{run_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_NOT_FOUND,
    **_MUTATION,
)
async def delete_pitch_run(
    run_id: UUID,
    admin: User = Depends(require_superuser),
    service: PitchHistoryService = Depends(get_pitch_history_service),
) -> Response:
    record = await service.delete(run_id)
    _audit(admin, f"deleted pitch run for {record.company} / {record.role} ({run_id})")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
