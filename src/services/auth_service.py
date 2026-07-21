from src.core.security import hash_password, verify_password, create_access_token, create_refresh_token
from src.models.user_model import User
from src.repositories.user_repo import UserRepository
from src.core.exceptions import UserNotFoundException, RepositoryError, BusinessRuleViolationError
from src.schemas.user_schema import UserCreate, UserUpdate
from src.schemas.auth_schema import AuthTokens

class AuthService:
    def __init__(self, user_repository: UserRepository):
        self.user_repository = user_repository

    async def register_user(self, user_create: UserCreate) -> User:
        hashed_password = hash_password(user_create.password)
        existing_email = await self.user_repository.get_user_by_email(user_create.email)
        if existing_email:
            raise RepositoryError(f"User with email {user_create.email} already exists.")
        else:
            user = User(
                name=user_create.name,
                email=user_create.email,
                hashed_password=hashed_password,
                is_active=user_create.is_active,
                is_superuser=user_create.is_superuser,
            )
            return await self.user_repository.create_user(user)

    async def authenticate_user(self, email: str, password: str) -> User:
        user = await self.user_repository.get_user_by_email(email)
        if not user or not verify_password(password, user.hashed_password):
            raise UserNotFoundException("Invalid email or password.")
        return user
        

    def issue_tokens(self, user: User) -> AuthTokens:
        claims = {
            "sub": str(user.id),
            "email": user.email,
            "is_superuser": user.is_superuser,
        }
        return AuthTokens(
            access_token=create_access_token(claims),
            refresh_token=create_refresh_token(claims),
        )
        
        
    async def update_password(self, user: User, new_password: str) -> None:
        if verify_password(new_password, user.hashed_password):
            raise BusinessRuleViolationError("New password cannot be the same as the old password.")

        user.hashed_password = hash_password(new_password)
        await self.session.add(user)
        await self.session.commit()