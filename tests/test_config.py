"""Settings parsing and production guards (plan 1.2, audit B-COR-11/12)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.core.config import Settings
from src.main import create_app
from tests import TEST_ENV

REQUIRED = {
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    "smtp_host": "localhost",
    "smtp_user": "sender@example.com",
    "smtp_password": "smtp-secret-value",
    "super_admin_email": "inbox@example.com",
    "portfolio_url": "https://example.com",
    "github_url": "https://github.com/example",
    "linkedin_url": "https://linkedin.com/in/example",
    "jwt_secret_key": "x" * 48,
}


@pytest.fixture
def without_test_env(monkeypatch):
    # tests/__init__.py exports a dummy environment for the app; remove the keys
    # whose *defaults* these tests are checking.
    for key in ("ENV", "CONTEXT_MOCK", "GEMINI_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def make_settings(**overrides) -> Settings:
    # _env_file=None: never read a real .env from the test's working directory.
    return Settings(_env_file=None, **{**REQUIRED, **overrides})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('["https://a.example", "https://b.example"]', ["https://a.example", "https://b.example"]),
        ("https://a.example, https://b.example", ["https://a.example", "https://b.example"]),
        ("https://a.example", ["https://a.example"]),
        ("", []),
    ],
)
def test_allowed_origins_accepts_json_or_comma_separated(monkeypatch, raw, expected):
    monkeypatch.setenv("ALLOWED_ORIGINS", raw)

    assert make_settings().allowed_origins == expected


def test_env_defaults_to_prod(without_test_env):
    assert make_settings().env == "prod"


def test_prod_rejects_short_jwt_secret():
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY must be at least 32"):
        make_settings(env="prod", jwt_secret_key="too-short")


def test_dev_allows_short_jwt_secret():
    assert make_settings(env="dev", jwt_secret_key="short").env == "dev"


def test_unknown_env_value_is_rejected():
    with pytest.raises(ValidationError):
        make_settings(env="staging")


def test_retired_keys_in_environment_are_ignored(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "legacy")
    monkeypatch.setenv("FRONTEND_URL", "https://legacy.example")

    settings = make_settings()

    assert not hasattr(settings, "aws_access_key_id")
    assert not hasattr(settings, "frontend_url")


def test_secrets_are_masked_in_repr():
    settings = make_settings(gemini_api_key="gemini-secret-value")
    rendered = repr(settings) + str(settings)

    for secret in ("smtp-secret-value", "x" * 48, "u:p@localhost", "gemini-secret-value"):
        assert secret not in rendered
    assert settings.smtp_password.get_secret_value() == "smtp-secret-value"


def test_optional_feature_keys_have_safe_defaults(without_test_env):
    settings = make_settings()

    assert settings.context_mock is False
    assert settings.pitch_enabled is False
    assert settings.pitch_daily_cap == 100
    assert settings.hourly_rate_gbp is None
    assert settings.gemini_api_key.get_secret_value() == ""


def test_samesite_is_case_insensitive_and_validated():
    assert make_settings(cookie_samesite="Lax").cookie_samesite == "lax"
    with pytest.raises(ValidationError):
        make_settings(cookie_samesite="sometimes")


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_hidden_in_prod(path):
    client = TestClient(create_app(make_settings(env="prod")))

    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_available_outside_prod(path):
    client = TestClient(create_app(make_settings(env="dev")))

    assert client.get(path).status_code == 200


def test_env_example_loads_and_documents_every_setting(monkeypatch):
    # Only .env.example may supply values here, not the suite's dummy environment.
    for key in TEST_ENV:
        monkeypatch.delenv(key, raising=False)
    example = Path(__file__).resolve().parents[1] / ".env.example"

    settings = Settings(_env_file=example)

    assert settings.env == "dev"
    assert settings.hourly_rate_gbp is None
    documented = {
        line.split("=", 1)[0].strip().lower()
        for line in example.read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    assert set(Settings.model_fields) <= documented
