from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import RepositoryError, UserNotFoundException
from src.models.user_model import User


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_user_by_id(self, user_id: UUID) -> User:
        try:
            query = select(User).where(User.id == user_id)
            result = await self.session.execute(query)
            user = result.scalar_one_or_none()
            if not user:
                raise UserNotFoundException(f"User with ID {user_id} not found.")
            return user
        except UserNotFoundException:
            raise
        except Exception as e:
            raise RepositoryError(f"Error retrieving user by ID: {e!s}") from e

    async def get_user_by_email(self, email: str) -> User | None:
        try:
            query = select(User).where(User.email == email)
            result = await self.session.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
            raise RepositoryError(f"Error retrieving user by email: {e!s}") from e

    async def create_user(self, user: User) -> User:
        try:
            self.session.add(user)
            await self.session.commit()
            await self.session.refresh(user)
            return user
        except Exception as e:
            await self.session.rollback()
            raise RepositoryError(f"Error creating user: {e!s}") from e

    async def update_user(self, user: User) -> User:
        try:
            await self.session.commit()
            await self.session.refresh(user)
            return user
        except Exception as e:
            await self.session.rollback()
            raise RepositoryError(f"Error updating user: {e!s}") from e

    async def get_superuser(self) -> list[User]:
        try:
            query = select(User).where(User.is_superuser == True)  # noqa: E712 -- SQLAlchemy column expression
            result = await self.session.execute(query)
            superuser = result.scalar_one_or_none()
            if not superuser:
                raise UserNotFoundException("No superuser found.")
            return superuser
        except Exception as e:
            raise RepositoryError(f"Error retrieving superusers: {e!s}") from e
