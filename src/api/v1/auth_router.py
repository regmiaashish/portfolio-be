from fastapi import APIRouter, Depends, Request, Response, status

from src.core.dependencies import (
    get_auth_service,
    get_current_user,
    get_current_user_from_refresh,
    require_superuser,
)
from src.core.rate_limit import LOGIN_LIMIT, limiter
from src.core.security import clear_auth_cookies, set_auth_cookies
from src.models.user_model import User
from src.schemas.auth_schema import LoginRequest, RegisterRequest
from src.schemas.common_schema import APIResponse, ErrorResponse
from src.schemas.user_schema import UserRead
from src.services.auth_service import AuthService, issue_tokens

router = APIRouter(tags=["auth"])

_UNAUTHORIZED = {401: {"model": ErrorResponse, "description": "Not authenticated"}}


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=APIResponse,
    # Runs before body validation, so anonymous callers get 401 whatever they send.
    dependencies=[Depends(require_superuser)],
    responses={
        **_UNAUTHORIZED,
        403: {"model": ErrorResponse, "description": "Not a superuser"},
        409: {"model": ErrorResponse, "description": "Email already registered"},
    },
)
async def register_user(
    payload: RegisterRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> APIResponse:
    await auth_service.register_user(payload)
    return APIResponse(message="User registered successfully.")


@router.post(
    "/login",
    response_model=APIResponse,
    responses={
        **_UNAUTHORIZED,
        429: {"model": ErrorResponse, "description": "Too many login attempts"},
    },
)
@limiter.limit(LOGIN_LIMIT)
async def login_user(
    request: Request,
    credentials: LoginRequest,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service),
) -> APIResponse:
    user = await auth_service.authenticate_user(credentials.email, credentials.password)
    tokens = issue_tokens(user)
    set_auth_cookies(response, tokens.access_token, tokens.refresh_token)
    return APIResponse(message="Login successful.")


@router.get("/me", response_model=APIResponse[UserRead], responses=_UNAUTHORIZED)
async def get_me(current_user: User = Depends(get_current_user)) -> APIResponse[UserRead]:
    return APIResponse(data=UserRead.model_validate(current_user))


@router.post("/logout", response_model=APIResponse)
async def logout(response: Response) -> APIResponse:
    """Always succeeds and clears both cookies, even if they are missing or expired."""
    clear_auth_cookies(response)
    return APIResponse(message="Logged out successfully.")


@router.post("/refresh", response_model=APIResponse, responses=_UNAUTHORIZED)
async def refresh_tokens(
    response: Response, current_user: User = Depends(get_current_user_from_refresh)
) -> APIResponse:
    tokens = issue_tokens(current_user)
    set_auth_cookies(response, tokens.access_token, tokens.refresh_token)
    return APIResponse(message="Tokens refreshed successfully.")
