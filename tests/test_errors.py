"""Every error uses {success: false, message, errors} (plan 1.1, audit B-COR-2)."""

from src.core.dependencies import get_user_repository
from src.core.exceptions import RepositoryError
from src.main import app
from tests.conftest import login_as, make_user


def _assert_error_shape(response, status_code):
    assert response.status_code == status_code
    body = response.json()
    assert set(body) == {"success", "message", "errors"}
    assert body["success"] is False
    assert isinstance(body["message"], str)
    assert body["message"]
    return body


def test_unknown_route_is_404_in_error_shape(client):
    body = _assert_error_shape(client.get("/does-not-exist"), 404)
    assert body["message"] == "Not Found"


def test_wrong_method_is_405_in_error_shape(client):
    _assert_error_shape(client.get("/api/v1/auth/login"), 405)


def test_forbidden_is_403_in_error_shape(client, fake_repo):
    login_as(client, make_user(fake_repo, email="user@example.com", is_superuser=False))

    response = client.post("/api/v1/auth/register", json={})

    _assert_error_shape(response, 403)


def test_validation_error_lists_fields_without_echoing_input(client):
    secret = "hunter2-should-never-come-back"
    response = client.post("/api/v1/auth/login", json={"email": "not-an-email", "password": secret})

    body = _assert_error_shape(response, 422)
    assert body["message"] == "Request validation failed."
    assert {tuple(e["loc"]) for e in body["errors"]} == {("body", "email")}
    assert all(set(e) == {"loc", "msg", "type"} for e in body["errors"])
    assert secret not in response.text
    assert "not-an-email" not in response.text


def test_long_password_is_not_echoed(client):
    secret = "x" * 129
    response = client.post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": secret}
    )

    _assert_error_shape(response, 422)
    assert secret not in response.text


def test_repository_error_is_500_without_leaking_internals(client_no_raise):
    class BrokenRepo:
        async def get_user_by_email(self, email):
            raise RepositoryError("Error retrieving user by email.") from ConnectionError(
                "password=supersecret host=db.internal"
            )

    app.dependency_overrides[get_user_repository] = lambda: BrokenRepo()

    response = client_no_raise.post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": "whatever-password"}
    )

    body = _assert_error_shape(response, 500)
    assert body["message"] == "Error retrieving user by email."
    assert "supersecret" not in response.text


def test_unhandled_exception_is_generic_500(client_no_raise):
    class ExplodingRepo:
        async def get_user_by_email(self, email):
            raise RuntimeError("internal detail that must not leak")

    app.dependency_overrides[get_user_repository] = lambda: ExplodingRepo()

    response = client_no_raise.post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": "whatever-password"}
    )

    body = _assert_error_shape(response, 500)
    assert body["message"] == "Internal server error."
    assert "internal detail" not in response.text
