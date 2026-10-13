"""Regression tests for B-SEC-1: POST /register must never create a superuser."""

from tests.conftest import login_as, make_user

REGISTER_URL = "/api/v1/auth/register"

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

    assert response.status_code == 201
    created = next(u for u in fake_repo.users.values() if u.email == VALID_PAYLOAD["email"])
    assert created.is_superuser is False
    assert created.is_active is True


def test_register_rejects_short_password(client, fake_repo):
    admin = make_user(fake_repo, email="admin@example.com", is_superuser=True)
    login_as(client, admin)

    response = client.post(REGISTER_URL, json={**VALID_PAYLOAD, "password": "p"})

    assert response.status_code == 422
    assert len(fake_repo.users) == 1


def test_register_empty_body_without_auth_is_401_not_422(client, fake_repo):
    # Auth runs before body validation; smoke_prod.sh relies on this.
    response = client.post(REGISTER_URL, json={})

    assert response.status_code == 401
    assert response.json() == {"success": False, "message": "Not authenticated.", "errors": None}


def test_register_duplicate_email_is_409_case_insensitive(client, fake_repo):
    admin = make_user(fake_repo, email="admin@example.com", is_superuser=True)
    make_user(fake_repo, email="new.user@example.com", is_superuser=False)
    login_as(client, admin)

    response = client.post(REGISTER_URL, json={**VALID_PAYLOAD, "email": "New.User@Example.com"})

    assert response.status_code == 409
    assert len(fake_repo.users) == 2


def test_register_stores_lowercased_email(client, fake_repo):
    admin = make_user(fake_repo, email="admin@example.com", is_superuser=True)
    login_as(client, admin)

    response = client.post(REGISTER_URL, json={**VALID_PAYLOAD, "email": "Mixed.Case@Example.com"})

    assert response.status_code == 201
    assert "mixed.case@example.com" in [u.email for u in fake_repo.users.values()]


def test_old_double_prefixed_path_is_gone(client, fake_repo):
    assert client.post("/auth/api/v1/register", json={}).status_code == 404
