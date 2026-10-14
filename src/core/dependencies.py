from collections.abc import AsyncIterator
from uuid import UUID

import httpx
from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.database import AsyncSessionLocal
from src.core.exceptions import CsrfError, PitchDisabledError
from src.core.security import verify_token
from src.models.user_model import User
from src.repositories.contact_message_repo import ContactMessageRepository
from src.repositories.content_repo import (
    CareerRepository,
    ProfileRepository,
    ProjectRepository,
    SkillGroupRepository,
)
from src.repositories.pitch_run_repo import PitchRunRepository
from src.repositories.pitch_usage_repo import PitchUsageRepository
from src.repositories.user_repo import UserRepository
from src.services.auth_service import AuthService
from src.services.contact_admin_service import ContactAdminService
from src.services.contact_service import ContactDelivery, ContactService, own_session_repository
from src.services.content_service import ContentService
from src.services.context_service import ContextService
from src.services.email_service import EmailSender, email_service
from src.services.media_service import MediaService
from src.services.pitch.graph import PitchPipeline
from src.services.pitch.history import PitchHistoryService, PitchRecorder
from src.services.pitch.llm import create_llm
from src.services.pitch.research import create_research
from src.services.pitch.service import PipelineFactory, PitchService
from src.services.pitch.stream import PitchCache
from src.services.storage_service import StorageService
from src.services.turnstile_service import TurnstileVerifier


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session


def get_user_repository(session: AsyncSession = Depends(get_db)) -> UserRepository:
    return UserRepository(session)


def get_auth_service(
    user_repository: UserRepository = Depends(get_user_repository),
) -> AuthService:
    return AuthService(user_repository)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


async def _user_from_token(token: str | None, token_type: str, repo: UserRepository) -> User:
    if not token:
        raise _unauthorized("Not authenticated.")

    payload = verify_token(token, token_type=token_type)
    if not payload:
        raise _unauthorized("Invalid or expired token.")

    try:
        user_id = UUID(str(payload.get("sub")))
    except ValueError:
        raise _unauthorized("Invalid token payload.") from None

    user = await repo.get_user_by_id(user_id)
    if user is None or not user.is_active:
        raise _unauthorized("User not found or inactive.")

    return user


async def get_current_user(
    repo: UserRepository = Depends(get_user_repository),
    access_token: str | None = Cookie(default=None, alias=settings.access_token_cookie_name),
) -> User:
    return await _user_from_token(access_token, "access", repo)


async def get_current_user_from_refresh(
    repo: UserRepository = Depends(get_user_repository),
    refresh_token: str | None = Cookie(default=None, alias=settings.refresh_token_cookie_name),
) -> User:
    return await _user_from_token(refresh_token, "refresh", repo)


async def require_superuser(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Superuser privileges required."
        )
    return current_user


# --- Shared clients (created once per process in the app lifespan) ---


def get_http_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


def get_context_service(request: Request) -> ContextService:
    return request.app.state.context_service


# --- Contact form ---


def get_email_sender() -> EmailSender:
    return email_service


def get_contact_repository(session: AsyncSession = Depends(get_db)) -> ContactMessageRepository:
    return ContactMessageRepository(session)


def get_turnstile_verifier(
    client: httpx.AsyncClient = Depends(get_http_client),
) -> TurnstileVerifier:
    return TurnstileVerifier(client, settings.turnstile_secret.get_secret_value())


def get_contact_service(
    repository: ContactMessageRepository = Depends(get_contact_repository),
    verifier: TurnstileVerifier = Depends(get_turnstile_verifier),
) -> ContactService:
    return ContactService(repository, verifier)


def get_contact_delivery(sender: EmailSender = Depends(get_email_sender)) -> ContactDelivery:
    return ContactDelivery(sender, own_session_repository)


def get_contact_admin_service(
    repository: ContactMessageRepository = Depends(get_contact_repository),
) -> ContactAdminService:
    return ContactAdminService(repository)


# --- Pitch Me ---


def ensure_pitch_enabled() -> None:
    """Kill switch. Runs before input validation, so a switched-off demo always says so."""
    if not settings.pitch_enabled:
        raise PitchDisabledError()


def get_pitch_cache(request: Request) -> PitchCache:
    return request.app.state.pitch_cache


def get_pitch_usage_repository(session: AsyncSession = Depends(get_db)) -> PitchUsageRepository:
    return PitchUsageRepository(session)


def get_pitch_pipeline_factory() -> PipelineFactory:
    # A fresh LLM/research client per run, so token usage and search counts are per run.
    return lambda: PitchPipeline(create_llm(), create_research())


def get_pitch_recorder(request: Request) -> PitchRecorder | None:
    """Writes finished runs to Pitch history (created in the app lifespan)."""
    return getattr(request.app.state, "pitch_recorder", None)


def get_pitch_service(
    verifier: TurnstileVerifier = Depends(get_turnstile_verifier),
    usage: PitchUsageRepository = Depends(get_pitch_usage_repository),
    cache: PitchCache = Depends(get_pitch_cache),
    pipeline_factory: PipelineFactory = Depends(get_pitch_pipeline_factory),
    recorder: PitchRecorder | None = Depends(get_pitch_recorder),
) -> PitchService:
    return PitchService(verifier, usage, cache, pipeline_factory, recorder=recorder)


def get_pitch_history_service(session: AsyncSession = Depends(get_db)) -> PitchHistoryService:
    return PitchHistoryService(PitchRunRepository(session), PitchUsageRepository(session))


# --- Site content and admin ---

CSRF_HEADER = "X-Requested-With"
CSRF_HEADER_VALUE = "portfolio-admin"


def require_csrf(
    request: Request,
    # Declared (not just read from request.headers) so it appears in the OpenAPI schema and
    # Swagger UI shows an input for it. The check below is unchanged.
    x_requested_with: str | None = Header(
        default=None,
        alias=CSRF_HEADER,
        description=f'CSRF protection: must be "{CSRF_HEADER_VALUE}".',
        examples=[CSRF_HEADER_VALUE],
    ),
) -> None:
    """Mutating admin requests must carry the custom header (forces a CORS preflight) and come
    from an allowed Origin. Together with SameSite=Lax cookies this blocks cross-site forgery."""
    origin = request.headers.get("origin")
    if (
        x_requested_with != CSRF_HEADER_VALUE
        or not origin
        or origin not in settings.allowed_origins
    ):
        raise CsrfError()


def get_storage(request: Request) -> StorageService | None:
    return getattr(request.app.state, "storage", None)


def get_media_service(
    storage: StorageService | None = Depends(get_storage),
) -> MediaService:
    return MediaService(storage)


def get_content_service(
    session: AsyncSession = Depends(get_db),
    storage: StorageService | None = Depends(get_storage),
) -> ContentService:
    # One session shared by every repository, so a request sees one consistent snapshot.
    return ContentService(
        ProfileRepository(session),
        ProjectRepository(session),
        CareerRepository(session),
        SkillGroupRepository(session),
        storage,
    )
