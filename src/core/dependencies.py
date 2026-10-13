from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.database import AsyncSessionLocal
from src.core.security import verify_token
from src.models.user_model import User
from src.repositories.user_repo import UserRepository
from src.services.auth_service import AuthService


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
