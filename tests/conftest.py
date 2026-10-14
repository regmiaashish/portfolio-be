# Test environment variables are set in tests/__init__.py, which pytest
# imports before this module, so src never reads real secrets.
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from src.api.health_router import get_db_probe
from src.core.dependencies import get_db, get_user_repository
from src.core.rate_limit import limiter
from src.core.security import create_access_token, hash_password
from src.main import app
from src.models.user_model import User

DEFAULT_PASSWORD = "a-long-enough-password"


class FakeUserRepository:
    """In-memory stand-in for UserRepository with the same async interface."""

    def __init__(self):
        self.users: dict[str, User] = {}

    async def get_user_by_id(self, user_id) -> User | None:
        return self.users.get(str(user_id))

    async def get_user_by_email(self, email: str) -> User | None:
        return next((u for u in self.users.values() if u.email.lower() == email.lower()), None)

    async def create_user(self, user: User) -> User:
        if user.id is None:
            user.id = uuid.uuid4()
        if user.created_at is None:  # the DB sets this via server_default
            user.created_at = datetime.now(UTC)
        self.users[str(user.id)] = user
        return user

    async def update_user(self, user: User) -> User:
        self.users[str(user.id)] = user
        return user

    async def get_superusers(self) -> list[User]:
        return [u for u in self.users.values() if u.is_superuser]


async def healthy_db_probe() -> None:
    """Stand-in for the readiness probe: the database always answers."""


@pytest.fixture
def fake_repo() -> FakeUserRepository:
    return FakeUserRepository()


def _install_overrides(repo: FakeUserRepository) -> None:
    # Each test starts with empty rate-limit counters.
    limiter.reset()

    async def no_db():
        yield None

    # Nothing in the test suite may open a real database connection.
    app.dependency_overrides[get_db] = no_db
    app.dependency_overrides[get_user_repository] = lambda: repo
    app.dependency_overrides[get_db_probe] = lambda: healthy_db_probe


@pytest.fixture
def client(fake_repo):
    _install_overrides(fake_repo)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def client_no_raise(fake_repo):
    """Like `client`, but unhandled errors become 500 responses instead of test errors."""
    _install_overrides(fake_repo)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def make_user(
    repo: FakeUserRepository,
    *,
    email: str,
    is_superuser: bool,
    password: str = DEFAULT_PASSWORD,
    is_active: bool = True,
) -> User:
    user = User(
        id=uuid.uuid4(),
        name="Existing User",
        email=email,
        hashed_password=hash_password(password),
        is_active=is_active,
        is_superuser=is_superuser,
        created_at=datetime.now(UTC),
    )
    repo.users[str(user.id)] = user
    return user


def login_as(client: TestClient, user: User) -> None:
    client.cookies.set("access_token", create_access_token({"sub": str(user.id)}))
