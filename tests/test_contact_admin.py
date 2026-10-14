"""Contact messages in the admin (docs/ADMIN_CMS_CONTRACT.md, Phase 6.3).

The endpoints run against the in-memory FakeContactRepository from conftest, and retries go
through the real ContactDelivery with the fake email sender; the real SQL is covered by
tests/test_contact_admin_db.py against a throwaway Postgres.
"""

import smtplib
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.models.contact_message_model import ContactMessage, ContactMessageStatus
from tests import TEST_ENV
from tests.conftest import login_as, make_user

ADMIN = "/api/v1/admin/messages"
CSRF = {"X-Requested-With": "portfolio-admin", "Origin": "http://localhost:5173"}


def _message(repo, minutes_ago: int, **overrides) -> ContactMessage:
    values = {
        "id": uuid.uuid4(),
        "name": "Visitor",
        "email": "visitor@example.com",
        "message": "Hello there",
        "status": ContactMessageStatus.SENT,
        "error": None,
        "read_at": None,
        "created_at": datetime.now(UTC) - timedelta(minutes=minutes_ago),
    }
    values.update(overrides)
    record = ContactMessage(**values)
    repo.messages[record.id] = record
    return record


@pytest.fixture
def inbox(fake_contact_repo) -> list[ContactMessage]:
    read = datetime.now(UTC) - timedelta(hours=1)
    return [
        _message(fake_contact_repo, 1, name="Ada Lovelace", message="Hi! " + "x" * 300),
        _message(
            fake_contact_repo,
            5,
            name="Grace",
            email="grace@navy.example",
            status=ContactMessageStatus.FAILED,
            error="SMTPAuthenticationError: (535, b'bad credentials')",
        ),
        _message(fake_contact_repo, 9, name="Linus", message="About a KERNEL role", read_at=read),
        _message(
            fake_contact_repo,
            60 * 24 * 3,
            name="Barbara",
            status=ContactMessageStatus.PENDING,
            read_at=read,
        ),
        _message(fake_contact_repo, 60 * 24 * 10, name="Old Timer"),  # outside the 7 days
    ]


@pytest.fixture
def admin(client, fake_repo):
    login_as(client, make_user(fake_repo, email="admin@example.com", is_superuser=True))
    return client


def _names(response) -> list[str]:
    return [item["name"] for item in response.json()["items"]]


# --- list ----------------------------------------------------------------------------------


def test_list_is_newest_first_with_preview_and_read_flag(admin, inbox):
    body = admin.get(ADMIN).json()

    assert body["total"] == 5
    assert (body["limit"], body["offset"]) == (20, 0)
    assert [item["name"] for item in body["items"]] == [
        "Ada Lovelace",
        "Grace",
        "Linus",
        "Barbara",
        "Old Timer",
    ]
    first = body["items"][0]
    assert set(first) == {"id", "name", "email", "preview", "status", "read", "created_at"}
    assert len(first["preview"]) == 160
    assert first["preview"] == inbox[0].message[:160]
    assert [item["read"] for item in body["items"]] == [False, False, True, True, False]
    assert body["items"][1]["status"] == "failed"


def test_list_paginates(admin, inbox):
    response = admin.get(ADMIN, params={"limit": 2, "offset": 2})

    assert _names(response) == ["Linus", "Barbara"]
    assert (response.json()["total"], response.json()["limit"]) == (5, 2)


def test_list_searches_name_email_and_message_case_insensitively(admin, inbox):
    assert _names(admin.get(ADMIN, params={"q": "LOVELACE"})) == ["Ada Lovelace"]
    assert _names(admin.get(ADMIN, params={"q": "navy.example"})) == ["Grace"]
    assert _names(admin.get(ADMIN, params={"q": "kernel"})) == ["Linus"]
    assert admin.get(ADMIN, params={"q": "   "}).json()["total"] == 5  # blank = no filter


def test_list_filters_by_status_and_unread(admin, inbox):
    assert _names(admin.get(ADMIN, params={"status": "failed"})) == ["Grace"]
    assert _names(admin.get(ADMIN, params={"unread": "true"})) == [
        "Ada Lovelace",
        "Grace",
        "Old Timer",
    ]
    assert _names(admin.get(ADMIN, params={"unread": "true", "status": "failed"})) == ["Grace"]
    assert admin.get(ADMIN, params={"unread": "false"}).json()["total"] == 5


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"q": "x" * 101}, {"status": "bounced"}],
)
def test_list_rejects_bad_parameters(admin, inbox, params):
    response = admin.get(ADMIN, params=params)

    assert response.status_code == 422
    assert response.json()["success"] is False


# --- stats ---------------------------------------------------------------------------------


def test_stats(admin, inbox):
    assert admin.get(f"{ADMIN}/stats").json() == {
        "unread": 3,
        "failed": 1,
        "total": 5,
        "last_7_days": 4,
    }


def test_stats_route_is_not_captured_by_the_id_route(admin, inbox):
    assert admin.get(f"{ADMIN}/stats").status_code == 200


# --- detail and read state -----------------------------------------------------------------


def test_detail_has_every_column_and_does_not_mark_read(admin, inbox):
    grace = inbox[1]

    body = admin.get(f"{ADMIN}/{grace.id}").json()

    assert body == {
        "id": str(grace.id),
        "name": "Grace",
        "email": "grace@navy.example",
        "message": "Hello there",
        "status": "failed",
        "error": grace.error,
        "read_at": None,
        "read": False,
        "created_at": body["created_at"],
    }
    assert grace.read_at is None


def test_detail_unknown_id_is_404(admin, inbox):
    response = admin.get(f"{ADMIN}/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["message"] == "Message not found."


def test_patch_marks_read_and_unread(admin, inbox):
    ada = inbox[0]

    body = admin.patch(f"{ADMIN}/{ada.id}", json={"read": True}, headers=CSRF).json()
    assert body["read"] is True
    assert body["read_at"] is not None
    first_read_at = ada.read_at
    assert first_read_at is not None

    # Marking an already-read message read again keeps when it was first read.
    admin.patch(f"{ADMIN}/{ada.id}", json={"read": True}, headers=CSRF)
    assert ada.read_at == first_read_at

    body = admin.patch(f"{ADMIN}/{ada.id}", json={"read": False}, headers=CSRF).json()
    assert (body["read"], body["read_at"]) == (False, None)
    assert ada.read_at is None


@pytest.mark.parametrize("payload", [{}, {"read": "yes"}, {"read": 1}, {"read": True, "x": 1}])
def test_patch_rejects_bad_bodies(admin, inbox, payload):
    response = admin.patch(f"{ADMIN}/{inbox[0].id}", json=payload, headers=CSRF)

    assert response.status_code == 422


def test_patch_unknown_id_is_404(admin, inbox):
    response = admin.patch(f"{ADMIN}/{uuid.uuid4()}", json={"read": True}, headers=CSRF)

    assert response.status_code == 404


# --- retry ---------------------------------------------------------------------------------


def test_retry_resends_a_failed_message(admin, inbox, fake_sender):
    grace = inbox[1]

    response = admin.post(f"{ADMIN}/{grace.id}/retry", headers=CSRF)

    assert response.status_code == 202
    body = response.json()
    # The response is sent before the delivery runs: back to pending, old error cleared.
    assert (body["status"], body["error"]) == ("pending", None)
    # The background delivery then ran, through the same path as the contact form.
    assert grace.status == ContactMessageStatus.SENT
    assert grace.error is None
    (mail,) = fake_sender.sent
    assert mail["recipient"] == TEST_ENV["SUPER_ADMIN_EMAIL"]
    assert "Grace" in mail["html"]


def test_retry_that_fails_again_is_recorded(admin, inbox, fake_sender):
    grace = inbox[1]
    fake_sender.fail_with = smtplib.SMTPServerDisconnected("gone")

    assert admin.post(f"{ADMIN}/{grace.id}/retry", headers=CSRF).status_code == 202

    assert grace.status == ContactMessageStatus.FAILED
    assert grace.error.startswith("SMTPServerDisconnected")


@pytest.mark.parametrize("index", [0, 3])  # sent, pending
def test_retry_refuses_messages_that_did_not_fail(admin, inbox, fake_sender, index):
    record = inbox[index]
    before = record.status

    response = admin.post(f"{ADMIN}/{record.id}/retry", headers=CSRF)

    assert response.status_code == 409
    assert response.json()["errors"] == {"code": "not_failed"}
    assert record.status == before
    assert fake_sender.sent == []


def test_retry_unknown_id_is_404(admin, inbox):
    assert admin.post(f"{ADMIN}/{uuid.uuid4()}/retry", headers=CSRF).status_code == 404


# --- delete --------------------------------------------------------------------------------


def test_delete_removes_the_message(admin, inbox, fake_contact_repo):
    ada = inbox[0]

    assert admin.delete(f"{ADMIN}/{ada.id}", headers=CSRF).status_code == 204
    assert ada.id not in fake_contact_repo.messages
    assert admin.delete(f"{ADMIN}/{ada.id}", headers=CSRF).status_code == 404


# --- access control and audit ----------------------------------------------------------------

MUTATIONS = [
    ("PATCH", "", {"json": {"read": True}}),
    ("POST", "/retry", {}),
    ("DELETE", "", {}),
]


@pytest.mark.parametrize(("method", "suffix", "kwargs"), MUTATIONS)
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "http://localhost:5173"},
        {"X-Requested-With": "portfolio-admin"},
        {"X-Requested-With": "portfolio-admin", "Origin": "https://evil.example"},
    ],
)
def test_mutations_need_csrf(admin, inbox, fake_contact_repo, method, suffix, kwargs, headers):
    grace = inbox[1]

    response = admin.request(method, f"{ADMIN}/{grace.id}{suffix}", headers=headers, **kwargs)

    assert response.status_code == 403
    assert response.json()["errors"] == {"code": "csrf"}
    assert grace.id in fake_contact_repo.messages
    assert (grace.status, grace.read_at) == (ContactMessageStatus.FAILED, None)


def _routes() -> list[tuple[str, str, dict]]:
    some_id = uuid.uuid4()
    return [
        ("GET", ADMIN, {}),
        ("GET", f"{ADMIN}/stats", {}),
        ("GET", f"{ADMIN}/{some_id}", {}),
        ("PATCH", f"{ADMIN}/{some_id}", {"json": {"read": True}}),
        ("POST", f"{ADMIN}/{some_id}/retry", {}),
        ("DELETE", f"{ADMIN}/{some_id}", {}),
    ]


@pytest.mark.parametrize(("method", "path", "kwargs"), _routes())
def test_anonymous_is_401(client, inbox, method, path, kwargs):
    assert client.request(method, path, headers=CSRF, **kwargs).status_code == 401


@pytest.mark.parametrize(("method", "path", "kwargs"), _routes())
def test_non_superuser_is_403(client, inbox, fake_repo, method, path, kwargs):
    login_as(client, make_user(fake_repo, email="user@example.com", is_superuser=False))

    assert client.request(method, path, headers=CSRF, **kwargs).status_code == 403


def test_mutations_are_audited_without_the_message(admin, inbox):
    from loguru import logger

    lines: list[str] = []
    sink = logger.add(lines.append, format="{message}")
    try:
        ada, grace = inbox[0], inbox[1]
        admin.patch(f"{ADMIN}/{ada.id}", json={"read": True}, headers=CSRF)
        admin.post(f"{ADMIN}/{grace.id}/retry", headers=CSRF)
        admin.delete(f"{ADMIN}/{ada.id}", headers=CSRF)
    finally:
        logger.remove(sink)

    audit = [line for line in lines if line.startswith("Admin admin@example.com")]
    assert any(f"marked message {ada.id} as read" in line for line in audit)
    assert any(f"retried the email for message {grace.id}" in line for line in audit)
    assert any(f"deleted message {ada.id}" in line for line in audit)
    assert not any("Hello there" in line or "xxxx" in line for line in audit)


def test_no_visitor_data_beyond_the_form_is_stored():
    columns = set(ContactMessage.__table__.columns.keys())

    assert columns == {"id", "name", "email", "message", "status", "error", "created_at", "read_at"}
    assert not any("ip" in name.split("_") or "agent" in name for name in columns)
