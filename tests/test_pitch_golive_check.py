"""scripts/pitch_golive_check.py: a read-only go-live checklist that never prints secrets."""

import asyncio
import sys
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.core.config import Settings
from tests.test_config import REQUIRED

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pitch_golive_check import (
    Check,
    database_checks,
    render,
    settings_checks,
    settings_error_checks,
)

GEMINI = "gemini-secret-value-123"
TAVILY = "tavily-secret-value-456"
TURNSTILE = "turnstile-secret-value-789"


def make(**overrides) -> Settings:
    return Settings(_env_file=None, **{**REQUIRED, "env": "dev", **overrides})


def ready(**overrides) -> Settings:
    return make(
        **{
            "env": "prod",
            "pitch_llm_provider": "gemini",
            "pitch_research_provider": "tavily",
            "gemini_api_key": GEMINI,
            "tavily_api_key": TAVILY,
            "turnstile_secret": TURNSTILE,
            "pitch_daily_cap": 20,
            "trusted_proxy_ips": "173.245.48.0/20",
            "client_ip_header": "cf-connecting-ip",
            **overrides,
        }
    )


def statuses(checks: list[Check]) -> dict[str, str]:
    return {check.name: check.status for check in checks}


def test_fully_configured_production_is_ready():
    checks = settings_checks(ready())

    assert not [check for check in checks if check.status in ("FAIL", "WARN")]
    assert render(checks).splitlines()[-1].startswith("READY")


def test_secret_values_are_never_printed():
    output = render(settings_checks(ready()))

    for secret in (GEMINI, TAVILY, TURNSTILE):
        assert secret not in output
    assert "GEMINI_API_KEY" in output and "set" in output


def test_missing_keys_and_fake_providers_fail(monkeypatch):
    # tests/__init__.py exports a dummy GEMINI_API_KEY; this test needs it unset.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    checks = settings_checks(
        make(env="prod", pitch_llm_provider="fake", pitch_research_provider="tavily")
    )

    failures = [check.detail for check in checks if check.status == "FAIL"]
    assert any("TURNSTILE_SECRET is required" in detail for detail in failures)
    assert any("cannot be fake" in detail for detail in failures)
    assert any("TAVILY_API_KEY is required" in detail for detail in failures)
    gemini = next(check for check in checks if check.name == "GEMINI_API_KEY")
    assert (gemini.status, gemini.detail) == ("INFO", "missing")
    assert render(checks).splitlines()[-1].startswith("NOT READY")


def test_large_cap_dev_env_and_missing_cloudflare_trust_are_warnings():
    checks = settings_checks(
        ready(
            pitch_daily_cap=500, env="dev", trusted_proxy_ips="", client_ip_header="x-forwarded-for"
        )
    )

    result = statuses(checks)
    assert result["PITCH_DAILY_CAP"] == "WARN"
    assert result["ENV"] == "WARN"
    assert result["Per-visitor rate limit"] == "WARN"


class FailingSession:
    async def __aenter__(self):
        raise OSError("connection refused to secret-host")

    async def __aexit__(self, *exc):
        return False


def test_unreachable_database_is_a_failure_without_details():
    checks = asyncio.run(database_checks(ready(), FailingSession, date(2026, 10, 14)))

    assert checks == [Check("FAIL", "Database", "not reachable (OSError)")]


def test_settings_errors_name_the_setting_but_never_its_value(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)  # tests/__init__.py sets it
    inputs = {key: value for key, value in REQUIRED.items() if key != "smtp_host"}
    inputs["jwt_secret_key"] = "short-secret-value"
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, **inputs, env="prod")

    checks = settings_error_checks(caught.value)
    output = render(checks)

    assert {check.name for check in checks} >= {"SMTP_HOST"}
    assert all(check.status == "FAIL" for check in checks)
    assert "Field required" in output
    assert "short-secret-value" not in output
