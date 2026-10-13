"""Login, session and token handling (plan 1.1, audit B-COR-2/4, B-SEC-4)."""

import uuid

from src.core.security import create_access_token, create_refresh_token
from tests.conftest import DEFAULT_PASSWORD, login_as, make_user

AUTH = "/api/v1/auth"


def _login(client, email, password=DEFAULT_PASSWORD):
    return client.post(f"{AUTH}/login", json={"email": email, "password": password})


def test_login_success_sets_both_cookies(client, fake_repo):
    make_user(fake_repo, email="admin@example.com", is_superuser=True)

    response = _login(client, "admin@example.com")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert {"access_token", "refresh_token"} <= set(response.cookies.keys())


def test_login_is_case_insensitive_on_email(client, fake_repo):
    make_user(fake_repo, email="admin@example.com", is_superuser=True)

    assert _login(client, "Admin@Example.COM").status_code == 200


def test_login_wrong_password_and_unknown_email_look_identical(client, fake_repo):
    make_user(fake_repo, email="admin@example.com", is_superuser=True)

    wrong_password = _login(client, "admin@example.com", "not-the-password")
    unknown_email = _login(client, "nobody@example.com")

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert wrong_password.json()["message"] == "Invalid email or password."
    assert "access_token" not in wrong_password.cookies


def test_login_inactive_user_is_401(client, fake_repo):
    make_user(fake_repo, email="gone@example.com", is_superuser=False, is_active=False)

    assert _login(client, "gone@example.com").status_code == 401


def test_me_returns_profile_in_envelope(client, fake_repo):
    user = make_user(fake_repo, email="admin@example.com", is_superuser=True)
    login_as(client, user)

    response = client.get(f"{AUTH}/me")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["email"] == "admin@example.com"
    assert body["data"]["updated_at"] is None
    assert "hashed_password" not in body["data"]


def test_me_without_cookie_is_401(client):
    response = client.get(f"{AUTH}/me")

    assert response.status_code == 401
    assert response.json()["message"] == "Not authenticated."


def test_token_for_deleted_user_is_401_not_500(client, fake_repo):
    client.cookies.set("access_token", create_access_token({"sub": str(uuid.uuid4())}))

    response = client.get(f"{AUTH}/me")

    assert response.status_code == 401
    assert response.json()["message"] == "User not found or inactive."


def test_token_with_malformed_sub_is_401_not_500(client):
    client.cookies.set("access_token", create_access_token({"sub": "not-a-uuid"}))

    assert client.get(f"{AUTH}/me").status_code == 401


def test_refresh_token_cannot_be_used_as_access_token(client, fake_repo):
    user = make_user(fake_repo, email="admin@example.com", is_superuser=True)
    client.cookies.set("access_token", create_refresh_token({"sub": str(user.id)}))

    assert client.get(f"{AUTH}/me").status_code == 401


def test_garbage_token_is_401(client):
    client.cookies.set("access_token", "not.a.jwt")

    assert client.get(f"{AUTH}/me").status_code == 401


def test_refresh_reissues_cookies(client, fake_repo):
    user = make_user(fake_repo, email="admin@example.com", is_superuser=True)
    client.cookies.set("refresh_token", create_refresh_token({"sub": str(user.id)}))

    response = client.post(f"{AUTH}/refresh")

    assert response.status_code == 200
    assert {"access_token", "refresh_token"} <= set(response.cookies.keys())


def test_refresh_with_access_token_is_401(client, fake_repo):
    user = make_user(fake_repo, email="admin@example.com", is_superuser=True)
    client.cookies.set("refresh_token", create_access_token({"sub": str(user.id)}))

    assert client.post(f"{AUTH}/refresh").status_code == 401


def test_logout_without_any_cookie_succeeds_and_clears_cookies(client):
    response = client.post(f"{AUTH}/logout")

    assert response.status_code == 200
    set_cookie = response.headers.get_list("set-cookie")
    assert any(c.startswith("access_token=") and "Max-Age=0" in c for c in set_cookie)
    assert any(c.startswith("refresh_token=") and "Max-Age=0" in c for c in set_cookie)


def test_logout_with_expired_session_still_succeeds(client):
    client.cookies.set("access_token", "expired-or-garbage")

    assert client.post(f"{AUTH}/logout").status_code == 200
