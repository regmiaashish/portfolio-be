"""Migrations against a real, throwaway Postgres (plan 1.4).

Opt-in: set TEST_DATABASE_URL to an empty local database, e.g.
    docker run --rm -d -p 127.0.0.1:55432:5432 -e POSTGRES_PASSWORD=test postgres:16-alpine
    TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@127.0.0.1:55432/postgres uv run pytest
The test drops everything in that database, so it refuses any non-local host.
"""

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests import TEST_ENV

ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")


def _alembic(*args: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    host = urlsplit(TEST_DATABASE_URL.replace("+asyncpg", "")).hostname
    assert host in {"localhost", "127.0.0.1", "::1"}, f"refusing non-local database host {host!r}"
    env = {**os.environ, **TEST_ENV, "DATABASE_URL": TEST_DATABASE_URL, "PYTHONPATH": str(ROOT)}
    # Run from an empty directory so no .env is ever picked up.
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_upgrade_check_downgrade_roundtrip(tmp_path):
    for step in (
        ("downgrade", "base"),
        ("upgrade", "head"),
        ("check",),
        ("downgrade", "base"),
        ("upgrade", "head"),
    ):
        result = _alembic(*step, tmp_path=tmp_path)
        assert result.returncode == 0, f"alembic {' '.join(step)} failed:\n{result.stderr}"

    assert "0002_contact_messages (head)" in _alembic("current", tmp_path=tmp_path).stdout
