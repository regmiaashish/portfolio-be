"""Is Pitch Me safe to switch on? A read-only checklist for the production server.

Run it inside the API container, before and after setting PITCH_ENABLED=true:

    sudo docker compose run --rm --entrypoint python api scripts/pitch_golive_check.py

It reads the effective settings and the database only. It makes NO calls to Gemini, Tavily,
Turnstile or anything else outside, so it costs nothing. Secrets are reported as "set" or
"missing", never printed (not even their length). Exit code 0 = no failures.
"""

import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydantic import ValidationError

Status = Literal["PASS", "WARN", "FAIL", "INFO"]

# A cap above this is allowed but flagged: start small and raise it once costs are known.
SUGGESTED_MAX_DAILY_CAP = 50


@dataclass(frozen=True)
class Check:
    status: Status
    name: str
    detail: str


def _secret_state(secret) -> str:
    return "set" if secret.get_secret_value() else "missing"


def settings_checks(settings) -> list[Check]:
    """Checks that only need the settings (no network, no database)."""
    checks: list[Check] = []

    checks.append(
        Check("PASS" if settings.is_prod else "WARN", "ENV", f"{settings.env} (expected prod)")
    )
    checks.append(
        Check(
            "INFO",
            "PITCH_ENABLED",
            "true: Pitch Me is live" if settings.pitch_enabled else "false: Pitch Me is off",
        )
    )

    # The same rules the app enforces at start-up with ENV=prod and PITCH_ENABLED=true.
    if problems := settings.pitch_readiness_problems():
        checks += [Check("FAIL", "Go-live requirement", problem) for problem in problems]
    else:
        checks.append(
            Check("PASS", "Go-live requirements", "real providers, keys and Turnstile all set")
        )

    checks.append(Check("INFO", "PITCH_LLM_PROVIDER", settings.pitch_llm_provider))
    checks.append(Check("INFO", "PITCH_LLM_MODEL", settings.pitch_llm_model))
    checks.append(Check("INFO", "PITCH_RESEARCH_PROVIDER", settings.pitch_research_provider))
    for name, secret in (
        ("GEMINI_API_KEY", settings.gemini_api_key),
        ("TAVILY_API_KEY", settings.tavily_api_key),
        ("TURNSTILE_SECRET", settings.turnstile_secret),
    ):
        # A missing key that matters is already a FAIL above; this line just shows the state.
        state = _secret_state(secret)
        checks.append(Check("PASS" if state == "set" else "INFO", name, state))

    cap = settings.pitch_daily_cap
    if cap == 0:
        checks.append(Check("WARN", "PITCH_DAILY_CAP", "0: every run will be refused"))
    elif cap > SUGGESTED_MAX_DAILY_CAP:
        checks.append(
            Check(
                "WARN",
                "PITCH_DAILY_CAP",
                f"{cap}: consider {SUGGESTED_MAX_DAILY_CAP} or less until costs are known",
            )
        )
    else:
        checks.append(Check("PASS", "PITCH_DAILY_CAP", f"{cap} runs per day"))

    checks.append(
        Check(
            "PASS" if settings.pitch_cache_ttl_hours > 0 else "WARN",
            "PITCH_CACHE_TTL_HOURS",
            f"{settings.pitch_cache_ttl_hours} (repeat requests replay for free)"
            if settings.pitch_cache_ttl_hours > 0
            else "0: identical requests are paid for every time",
        )
    )
    checks.append(
        Check("INFO", "PITCH_TIMEOUT_SECONDS", f"{settings.pitch_timeout_seconds:g} per run")
    )

    # Without trusted Cloudflare ranges every visitor shares Cloudflare's address, so the
    # 3/minute limit would apply to everyone at once (and a single abuser can't be told apart).
    behind_cloudflare = bool(settings.trusted_proxy_ips) and (
        settings.client_ip_header == "cf-connecting-ip"
    )
    checks.append(
        Check(
            "PASS" if behind_cloudflare else "WARN",
            "Per-visitor rate limit",
            "CLIENT_IP_HEADER=cf-connecting-ip with TRUSTED_PROXY_IPS set"
            if behind_cloudflare
            else "TRUSTED_PROXY_IPS / CLIENT_IP_HEADER not set for Cloudflare",
        )
    )
    return checks


async def database_checks(settings, session_factory, today: date) -> list[Check]:
    """Database reachable, Pitch Me tables present, today's usage against the cap."""
    from sqlalchemy import text

    from src.repositories.pitch_usage_repo import PitchUsageRepository

    checks: list[Check] = []
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
            checks.append(Check("PASS", "Database", "reachable"))

            tables = set(
                (
                    await session.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = current_schema() "
                            "AND table_name IN ('pitch_usage', 'pitch_runs')"
                        )
                    )
                ).scalars()
            )
            missing = {"pitch_usage", "pitch_runs"} - tables
            if missing:
                checks.append(
                    Check(
                        "FAIL",
                        "Pitch Me tables",
                        f"missing {', '.join(sorted(missing))}: run alembic upgrade head",
                    )
                )
                return checks
            checks.append(Check("PASS", "Pitch Me tables", "pitch_usage and pitch_runs exist"))

            runs = await PitchUsageRepository(session).runs_on(today)
            cap = settings.pitch_daily_cap
            checks.append(
                Check(
                    "WARN" if cap and runs >= cap else "INFO",
                    "Usage today (UTC)",
                    f"{runs} of {cap} paid runs",
                )
            )
    except Exception as error:  # noqa: BLE001 -- report any failure as a checklist line
        checks.append(Check("FAIL", "Database", f"not reachable ({type(error).__name__})"))
    return checks


def render(checks: list[Check]) -> str:
    width = max(len(check.name) for check in checks)
    lines = [f"[{check.status}] {check.name.ljust(width)}  {check.detail}" for check in checks]
    failures = sum(check.status == "FAIL" for check in checks)
    warnings = sum(check.status == "WARN" for check in checks)
    verdict = (
        "READY: nothing blocks switching Pitch Me on."
        if failures == 0
        else f"NOT READY: fix the {failures} FAIL line(s) first."
    )
    lines.append("")
    lines.append(f"{verdict} ({warnings} warning(s))")
    return "\n".join(lines)


def load_settings() -> tuple[object | None, list[Check]]:
    """The app's settings, or the reasons they don't load (messages only, never values)."""
    try:
        from src.core.config import settings
    except ValidationError as error:
        return None, settings_error_checks(error)
    return settings, []


def settings_error_checks(error: ValidationError) -> list[Check]:
    """One FAIL per invalid setting, named like its .env key (e.g. SMTP_HOST).

    include_input=False: pydantic would otherwise echo the raw (possibly secret) values.
    Whole-model checks have no field location and are labelled "Settings".
    """
    return [
        Check(
            "FAIL",
            ".".join(str(part) for part in item["loc"]).upper() or "Settings",
            item["msg"].removeprefix("Value error, "),
        )
        for item in error.errors(include_input=False, include_url=False)
    ]


async def main() -> int:
    settings, problems = load_settings()
    if settings is None:
        print(render(problems))
        return 1

    from src.core.database import AsyncSessionLocal, engine

    checks = settings_checks(settings)
    try:
        checks += await database_checks(settings, AsyncSessionLocal, datetime.now(UTC).date())
    finally:
        await engine.dispose()
    print(render(checks))
    return 1 if any(check.status == "FAIL" for check in checks) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
