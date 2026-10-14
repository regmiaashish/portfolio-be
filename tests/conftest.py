# Test environment variables are set in tests/__init__.py, which pytest
# imports before this module, so src never reads real secrets.
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from src.api.health_router import get_db_probe
from src.core.dependencies import (
    get_contact_delivery,
    get_contact_repository,
    get_db,
    get_user_repository,
)
from src.core.rate_limit import limiter
from src.core.security import create_access_token, hash_password
from src.main import app
from src.models.contact_message_model import ContactMessage, ContactMessageStatus
from src.models.user_model import User
from src.repositories.contact_message_repo import ContactMessageCounts
from src.services.contact_service import ContactDelivery

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


class FakeContactRepository:
    """In-memory stand-in for ContactMessageRepository."""

    def __init__(self):
        self.messages: dict[uuid.UUID, ContactMessage] = {}

    async def create(self, *, name: str, email: str, message: str) -> ContactMessage:
        record = ContactMessage(
            id=uuid.uuid4(),
            name=name,
            email=email,
            message=message,
            status=ContactMessageStatus.PENDING,
            created_at=datetime.now(UTC),
        )
        self.messages[record.id] = record
        return record

    async def mark_sent(self, message_id: uuid.UUID) -> None:
        self.messages[message_id].status = ContactMessageStatus.SENT

    async def mark_failed(self, message_id: uuid.UUID, error: str) -> None:
        self.messages[message_id].status = ContactMessageStatus.FAILED
        self.messages[message_id].error = error

    # Admin (Phase 6.3); the real SQL is covered by tests/test_contact_admin_db.py.

    async def list(self, *, limit, offset, q=None, status=None, unread=False):
        def matches(m: ContactMessage) -> bool:
            text = f"{m.name}\n{m.email}\n{m.message}".lower()
            return (
                (not q or q.lower() in text)
                and (not status or m.status == status)
                and (not unread or m.read_at is None)
            )

        rows = sorted(
            (m for m in self.messages.values() if matches(m)),
            key=lambda m: m.created_at,
            reverse=True,
        )
        return rows[offset : offset + limit], len(rows)

    async def counts(self, recent_since: datetime) -> ContactMessageCounts:
        rows = list(self.messages.values())
        return ContactMessageCounts(
            unread=sum(m.read_at is None for m in rows),
            failed=sum(m.status == ContactMessageStatus.FAILED for m in rows),
            total=len(rows),
            recent=sum(m.created_at >= recent_since for m in rows),
        )

    async def get(self, message_id: uuid.UUID) -> ContactMessage | None:
        return self.messages.get(message_id)

    async def set_read_at(self, record: ContactMessage, read_at: datetime | None) -> None:
        record.read_at = read_at

    async def delete(self, record: ContactMessage) -> None:
        del self.messages[record.id]

    async def mark_pending_for_retry(self, message_id: uuid.UUID) -> bool:
        record = self.messages.get(message_id)
        if record is None or record.status != ContactMessageStatus.FAILED:
            return False
        record.status, record.error = ContactMessageStatus.PENDING, None
        return True


class FakeEmailSender:
    """Records emails instead of sending them; set `fail_with` to simulate SMTP errors."""

    def __init__(self):
        self.sent: list[dict[str, str]] = []
        self.fail_with: Exception | None = None

    def send_email(self, recipient: str, subject: str, html: str) -> None:
        if self.fail_with:
            raise self.fail_with
        self.sent.append({"recipient": recipient, "subject": subject, "html": html})


async def healthy_db_probe() -> None:
    """Stand-in for the readiness probe: the database always answers."""


@pytest.fixture
def fake_repo() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture
def fake_contact_repo() -> FakeContactRepository:
    return FakeContactRepository()


@pytest.fixture
def fake_sender() -> FakeEmailSender:
    return FakeEmailSender()


def _install_overrides(
    repo: FakeUserRepository,
    contact_repo: FakeContactRepository,
    sender: FakeEmailSender,
) -> None:
    # Each test starts with empty rate-limit counters.
    limiter.reset()

    async def no_db():
        yield None

    # Nothing in the test suite may open a real database connection.
    app.dependency_overrides[get_db] = no_db
    app.dependency_overrides[get_user_repository] = lambda: repo
    app.dependency_overrides[get_db_probe] = lambda: healthy_db_probe

    # The contact flow never reaches real SMTP or a real database in tests.
    @asynccontextmanager
    async def fake_scope() -> AsyncIterator[FakeContactRepository]:
        yield contact_repo

    app.dependency_overrides[get_contact_repository] = lambda: contact_repo
    app.dependency_overrides[get_contact_delivery] = lambda: ContactDelivery(sender, fake_scope)


@pytest.fixture
def client(fake_repo, fake_contact_repo, fake_sender):
    _install_overrides(fake_repo, fake_contact_repo, fake_sender)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def client_no_raise(fake_repo, fake_contact_repo, fake_sender):
    """Like `client`, but unhandled errors become 500 responses instead of test errors."""
    _install_overrides(fake_repo, fake_contact_repo, fake_sender)
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
