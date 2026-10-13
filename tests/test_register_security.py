"""Regression tests for B-SEC-1: POST /register must never create a superuser."""

from tests.conftest import login_as, make_user

REGISTER_URL = "/auth/api/v1/register"

VALID_PAYLOAD = {
    "name": "New User",
    "email": "new.user@example.com",
    "password": "a-long-enough-password",
}


def test_register_requires_authentication(client, fake_repo):
    response = client.post(REGISTER_URL, json={**VALID_PAYLOAD, "is_superuser": True})

    assert response.status_code == 401
    assert fake_repo.users == {}


def test_register_rejects_non_superuser(client, fake_repo):
    regular = make_user(fake_repo, email="regular@example.com", is_superuser=False)
    login_as(client, regular)

    response = client.post(REGISTER_URL, json=VALID_PAYLOAD)

    assert response.status_code == 403
    assert len(fake_repo.users) == 1


def test_register_cannot_set_is_superuser(client, fake_repo):
    admin = make_user(fake_repo, email="admin@example.com", is_superuser=True)
    login_as(client, admin)

    response = client.post(REGISTER_URL, json={**VALID_PAYLOAD, "is_superuser": True})

    assert response.status_code == 422
    assert [u.email for u in fake_repo.users.values()] == ["admin@example.com"]


def test_register_cannot_set_is_active(client, fake_repo):
    admin = make_user(fake_repo, email="admin@example.com", is_superuser=True)
    login_as(client, admin)

    response = client.post(REGISTER_URL, json={**VALID_PAYLOAD, "is_active": False})

    assert response.status_code == 422
    assert len(fake_repo.users) == 1


def test_register_by_superuser_creates_regular_user(client, fake_repo):
    admin = make_user(fake_repo, email="admin@example.com", is_superuser=True)
    login_as(client, admin)

    response = client.post(REGISTER_URL, json=VALID_PAYLOAD)

    assert response.status_code == 200
    created = next(
        u for u in fake_repo.users.values() if u.email == VALID_PAYLOAD["email"]
    )
    assert created.is_superuser is False
    assert created.is_active is True


def test_register_rejects_short_password(client, fake_repo):
    admin = make_user(fake_repo, email="admin@example.com", is_superuser=True)
    login_as(client, admin)

    response = client.post(REGISTER_URL, json={**VALID_PAYLOAD, "password": "p"})

    assert response.status_code == 422
    assert len(fake_repo.users) == 1
