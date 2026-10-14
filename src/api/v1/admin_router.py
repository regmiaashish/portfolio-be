"""Content admin (Phase 4.5): superuser-only, CSRF-guarded mutations. See ADMIN_CMS_CONTRACT.md."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, status
from loguru import logger
from starlette.datastructures import UploadFile

from src.core.config import settings
from src.core.dependencies import (
    get_contact_admin_service,
    get_contact_delivery,
    get_content_service,
    get_pitch_history_service,
    require_csrf,
    require_superuser,
)
from src.core.exceptions import UnprocessableError
from src.core.rate_limit import UPLOAD_FILE_LIMIT, limiter
from src.models.user_model import User
from src.schemas.common_schema import ErrorResponse
from src.schemas.contact_message_schema import (
    MessageDetail,
    MessageList,
    MessageReadInput,
    MessageStats,
    MessageStatusName,
    MessageSummary,
)
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
    StoredUploadOut,
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
from src.services.contact_admin_service import ContactAdminService, message_preview
from src.services.contact_service import ContactDelivery
from src.services.content_service import ContentService
from src.services.pitch.history import PitchHistoryService, preview
from src.services.upload_files import UploadTooLargeError, read_limited

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


# Multipart framing (boundaries, part headers, the purpose field) on top of the file itself.
_MULTIPART_OVERHEAD_BYTES = 64 * 1024
_UPLOAD_PURPOSES = ("project_image", "resume")

_UPLOAD_FILE_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "purpose": {
                            "type": "string",
                            "enum": list(_UPLOAD_PURPOSES),
                            "default": "project_image",
                        },
                        "file": {"type": "string", "format": "binary"},
                    },
                }
            }
        },
    }
}


def _too_large() -> UnprocessableError:
    return UnprocessableError(
        f"The file is too large: at most {settings.s3_max_upload_mb} MB.", loc=["body", "file"]
    )


@router.post(
    "/uploads/file",
    status_code=status.HTTP_201_CREATED,
    response_model=StoredUploadOut,
    openapi_extra=_UPLOAD_FILE_BODY,
    responses={
        422: {"model": ErrorResponse, "description": "Wrong file type, empty, or too large"},
        502: {"model": ErrorResponse, "description": "Storage (S3) rejected the write"},
        503: {"model": ErrorResponse, "description": "Uploads not configured"},
    },
    **_MUTATION,
)
@limiter.limit(UPLOAD_FILE_LIMIT)
async def upload_file(
    request: Request,
    admin: User = Depends(require_superuser),
    service: ContentService = Depends(get_content_service),
) -> StoredUploadOut:
    """Upload through the API: the server checks the file's real type and writes it to S3.

    The body is parsed here rather than by FastAPI so an oversized request is refused from its
    Content-Length before any of it is read.
    """
    service.ensure_uploads_enabled()
    max_bytes = settings.s3_max_upload_mb * 1024 * 1024
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes + _MULTIPART_OVERHEAD_BYTES:
        raise _too_large()

    form = await request.form(max_files=1, max_fields=1)
    try:
        purpose = form.get("purpose") or "project_image"
        if purpose not in _UPLOAD_PURPOSES:
            raise UnprocessableError(
                "purpose must be project_image or resume.", loc=["body", "purpose"]
            )
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise UnprocessableError("A file is required.", loc=["body", "file"])
        try:
            data = await read_limited(upload, max_bytes)
        except UploadTooLargeError:
            raise _too_large() from None
        stored = await service.store_upload(str(purpose), data)
    finally:
        await form.close()

    _audit(
        admin,
        f"uploaded a {purpose} file to {stored.key} ({stored.content_type}, {stored.size} bytes)",
    )
    return stored


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


# --- Contact messages ------------------------------------------------------------------------
# /messages/stats is declared before /messages/{message_id} for the same reason.

_NOT_FAILED = {409: {"model": ErrorResponse, "description": "The message's email didn't fail"}}


@router.get("/messages", response_model=MessageList)
async def list_messages(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(max_length=100)] = None,
    status_filter: Annotated[MessageStatusName | None, Query(alias="status")] = None,
    unread: bool = False,
    service: ContactAdminService = Depends(get_contact_admin_service),
) -> MessageList:
    """Contact-form messages, newest first."""
    search = q.strip() if q else None
    records, total = await service.list(
        limit=limit, offset=offset, q=search or None, status=status_filter, unread=unread
    )
    items = [
        MessageSummary(
            id=record.id,
            name=record.name,
            email=record.email,
            preview=message_preview(record.message),
            status=record.status,
            read=record.read_at is not None,
            created_at=record.created_at,
        )
        for record in records
    ]
    return MessageList(items=items, total=total, limit=limit, offset=offset)


@router.get("/messages/stats", response_model=MessageStats)
async def message_stats(
    service: ContactAdminService = Depends(get_contact_admin_service),
) -> MessageStats:
    """Unread and failed counts (for the nav badge), the total, and the last 7 days."""
    return MessageStats.model_validate(await service.stats())


@router.get("/messages/{message_id}", response_model=MessageDetail, responses=_NOT_FOUND)
async def get_message(
    message_id: UUID,
    service: ContactAdminService = Depends(get_contact_admin_service),
) -> MessageDetail:
    """The full message. Reading it doesn't mark it read; PATCH does."""
    return MessageDetail.model_validate(await service.get(message_id))


@router.patch(
    "/messages/{message_id}", response_model=MessageDetail, responses=_NOT_FOUND, **_MUTATION
)
async def set_message_read(
    message_id: UUID,
    payload: MessageReadInput,
    admin: User = Depends(require_superuser),
    service: ContactAdminService = Depends(get_contact_admin_service),
) -> MessageDetail:
    record = await service.set_read(message_id, payload.read)
    _audit(admin, f"marked message {message_id} as {'read' if payload.read else 'unread'}")
    return MessageDetail.model_validate(record)


@router.post(
    "/messages/{message_id}/retry",
    response_model=MessageDetail,
    status_code=status.HTTP_202_ACCEPTED,
    responses={**_NOT_FOUND, **_NOT_FAILED},
    **_MUTATION,
)
async def retry_message(
    message_id: UUID,
    background_tasks: BackgroundTasks,
    admin: User = Depends(require_superuser),
    service: ContactAdminService = Depends(get_contact_admin_service),
    delivery: ContactDelivery = Depends(get_contact_delivery),
) -> MessageDetail:
    """Re-send the notification email of a failed message (202: back to pending)."""
    record = await service.retry(message_id)
    # The same background delivery as the contact form: pending -> sent | failed.
    background_tasks.add_task(
        delivery.deliver,
        record.id,
        name=record.name,
        email=record.email,
        message=record.message,
    )
    _audit(admin, f"retried the email for message {message_id}")
    return MessageDetail.model_validate(record)


@router.delete(
    "/messages/{message_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_NOT_FOUND,
    **_MUTATION,
)
async def delete_message(
    message_id: UUID,
    admin: User = Depends(require_superuser),
    service: ContactAdminService = Depends(get_contact_admin_service),
) -> Response:
    await service.delete(message_id)
    _audit(admin, f"deleted message {message_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
