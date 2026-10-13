# Test environment variables are set in tests/__init__.py, which pytest
# imports before this module, so src never reads real secrets.
import uuid
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from src.api.v1 import auth_router as auth_router_module
from src.core import dependencies
from src.core.dependencies import get_db
from src.core.exceptions import UserNotFoundException
from src.core.security import create_access_token, hash_password
from src.main import app
from src.models.user_model import User


class FakeUserRepository:
    """In-memory stand-in for UserRepository; shares state across instances."""

    users: ClassVar[dict[str, User]] = {}

    def __init__(self, session=None):
        self.session = session

    async def get_user_by_id(self, user_id) -> User:
        user = self.users.get(str(user_id))
        if not user:
            raise UserNotFoundException(f"User with ID {user_id} not found.")
        return user

    async def get_user_by_email(self, email: str) -> User | None:
        return next((u for u in self.users.values() if u.email == email), None)

    async def create_user(self, user: User) -> User:
        if user.id is None:
            user.id = uuid.uuid4()
        self.users[str(user.id)] = user
        return user


@pytest.fixture
def fake_repo(monkeypatch):
    FakeUserRepository.users = {}
    monkeypatch.setattr(auth_router_module, "UserRepository", FakeUserRepository)
    monkeypatch.setattr(dependencies, "UserRepository", FakeUserRepository)
    return FakeUserRepository


@pytest.fixture
def client(fake_repo):
    async def no_db():
        yield None

    app.dependency_overrides[get_db] = no_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def make_user(repo, *, email: str, is_superuser: bool) -> User:
    user = User(
        id=uuid.uuid4(),
        name="Existing User",
        email=email,
        hashed_password=hash_password("a-long-enough-password"),
        is_active=True,
        is_superuser=is_superuser,
    )
    repo.users[str(user.id)] = user
    return user


def login_as(client: TestClient, user: User) -> None:
    client.cookies.set("access_token", create_access_token({"sub": str(user.id)}))
