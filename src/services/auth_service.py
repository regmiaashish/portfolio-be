from src.core.exceptions import AuthenticationError, BusinessRuleViolationError, ConflictError
from src.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)
from src.models.user_model import User
from src.repositories.user_repo import UserRepository
from src.schemas.auth_schema import AuthTokens, RegisterRequest

# Verified against when the email is unknown, so a failed login costs the same
# argon2 work either way and response time doesn't reveal which emails exist.
_DUMMY_PASSWORD_HASH = hash_password("timing-equaliser-not-a-real-password")


def issue_tokens(user: User) -> AuthTokens:
    claims = {
        "sub": str(user.id),
        "email": user.email,
        "is_superuser": user.is_superuser,
    }
    return AuthTokens(
        access_token=create_access_token(claims),
        refresh_token=create_refresh_token(claims),
    )


class AuthService:
    def __init__(self, user_repository: UserRepository):
        self.user_repository = user_repository

    async def register_user(self, user_create: RegisterRequest) -> User:
        if await self.user_repository.get_user_by_email(user_create.email):
            raise ConflictError("A user with this email already exists.")

        user = User(
            name=user_create.name,
            email=user_create.email,
            hashed_password=hash_password(user_create.password),
            is_active=True,
            is_superuser=False,
        )
        return await self.user_repository.create_user(user)

    async def authenticate_user(self, email: str, password: str) -> User:
        user = await self.user_repository.get_user_by_email(email)
        if user is None:
            verify_password(password, _DUMMY_PASSWORD_HASH)
            raise AuthenticationError()
        if not verify_password(password, user.hashed_password) or not user.is_active:
            raise AuthenticationError()
        return user

    async def update_password(self, user: User, new_password: str) -> User:
        if verify_password(new_password, user.hashed_password):
            raise BusinessRuleViolationError("New password cannot be the same as the old password.")

        user.hashed_password = hash_password(new_password)
        return await self.user_repository.update_user(user)
