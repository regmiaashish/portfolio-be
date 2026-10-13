from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.dependencies import (
    get_current_user,
    get_current_user_from_refresh,
    get_db,
    require_superuser,
)
from src.core.exceptions import RepositoryError, UserNotFoundException
from src.core.security import clear_auth_cookies, set_auth_cookies
from src.models.user_model import User
from src.repositories.user_repo import UserRepository
from src.schemas.auth_schema import RegisterRequest, loginRequest
from src.schemas.common_schema import APIResponse
from src.services.auth_service import AuthService

auth_router = APIRouter(tags=["auth"], prefix="/api/v1")


@auth_router.post("/register", dependencies=[Depends(require_superuser)])
async def register_user(user_create: RegisterRequest, session: AsyncSession = Depends(get_db)):
    user_repository = UserRepository(session)
    auth_service = AuthService(user_repository)

    try:
        await auth_service.register_user(user_create)
        return APIResponse(
            status_code=status.HTTP_201_CREATED,
            content={"message": "User registered successfully."},
        )
    except RepositoryError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@auth_router.post("/login")
async def login_user(
    credentials: loginRequest, response: Response, session: AsyncSession = Depends(get_db)
):
    user_repository = UserRepository(session)
    auth_service = AuthService(user_repository)

    try:
        user = await auth_service.authenticate_user(credentials.email, credentials.password)
        tokens = auth_service.issue_tokens(user)
        set_auth_cookies(response, tokens.access_token, tokens.refresh_token)
        return APIResponse(status_code=status.HTTP_200_OK, content={"message": "Login successful."})
    except UserNotFoundException as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except RepositoryError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@auth_router.get("/me")
async def get_me(current_user: User = Depends(get_current_user)):
    return {
        "id": str(current_user.id),
        "name": current_user.name,
        "email": current_user.email,
        "is_active": current_user.is_active,
        "is_superuser": current_user.is_superuser,
    }


@auth_router.post("/logout")
async def logout(response: Response, _: User = Depends(get_current_user)):
    clear_auth_cookies(response)
    return APIResponse(
        status_code=status.HTTP_200_OK, content={"message": "Logged out successfully."}
    )


@auth_router.post("/refresh")
async def refresh_tokens(
    response: Response, current_user: User = Depends(get_current_user_from_refresh)
):
    auth_service = AuthService(None)
    tokens = auth_service.issue_tokens(current_user)
    set_auth_cookies(response, tokens.access_token, tokens.refresh_token)
    return APIResponse(
        status_code=status.HTTP_200_OK, content={"message": "Tokens refreshed successfully."}
    )
