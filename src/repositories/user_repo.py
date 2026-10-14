from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, RepositoryError
from src.models.user_model import User

# Repository errors carry generic messages because 5xx messages reach clients.
# The original database error stays attached as __cause__ and is logged by the
# exception handler.


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_user_by_id(self, user_id: UUID) -> User | None:
        try:
            result = await self.session.execute(select(User).where(User.id == user_id))
            return result.scalar_one_or_none()
        except Exception as e:
            raise RepositoryError("Error retrieving user by ID.") from e

    async def get_user_by_email(self, email: str) -> User | None:
        # Case-insensitive so rows stored before emails were normalised still match.
        try:
            result = await self.session.execute(
                select(User).where(func.lower(User.email) == email.lower())
            )
            return result.scalar_one_or_none()
        except Exception as e:
            raise RepositoryError("Error retrieving user by email.") from e

    async def create_user(self, user: User) -> User:
        try:
            self.session.add(user)
            await self.session.commit()
            await self.session.refresh(user)
            return user
        except IntegrityError as e:
            await self.session.rollback()
            raise ConflictError("A user with this email already exists.") from e
        except Exception as e:
            await self.session.rollback()
            raise RepositoryError("Error creating user.") from e

    async def update_user(self, user: User) -> User:
        try:
            await self.session.commit()
            await self.session.refresh(user)
            return user
        except Exception as e:
            await self.session.rollback()
            raise RepositoryError("Error updating user.") from e

    async def get_superusers(self) -> list[User]:
        try:
            result = await self.session.execute(select(User).where(User.is_superuser.is_(True)))
            return list(result.scalars().all())
        except Exception as e:
            raise RepositoryError("Error retrieving superusers.") from e
