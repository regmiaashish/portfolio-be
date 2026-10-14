from collections.abc import AsyncIterator
from uuid import UUID

import httpx
from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.database import AsyncSessionLocal
from src.core.security import verify_token
from src.models.user_model import User
from src.repositories.contact_message_repo import ContactMessageRepository
from src.repositories.user_repo import UserRepository
from src.services.auth_service import AuthService
from src.services.contact_service import ContactDelivery, ContactService, own_session_repository
from src.services.context_service import ContextService
from src.services.email_service import EmailSender, email_service
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
