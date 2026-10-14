"""Pitch history (docs/ADMIN_CMS_CONTRACT.md, "Pitch history (admin)").

Recording uses a fake recorder and the admin endpoints a fake repository; the real SQL is
covered by tests/test_pitch_history_db.py against a throwaway Postgres.
"""

import asyncio
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from src.core.config import settings
from src.core.dependencies import get_pitch_history_service, get_pitch_recorder
from src.main import app
from src.models.pitch_run_model import PitchRunRecord
from src.repositories.pitch_run_repo import PitchRunCounts
from src.services.pitch.graph import PitchPipeline
from src.services.pitch.history import DbPitchRecorder, PitchHistoryService, PitchRunResult
from src.services.pitch.llm import FakeLLM
from src.services.pitch.research import FakeResearch
from src.services.pitch.stream import PitchCache, PitchRun
from tests.conftest import login_as, make_user
from tests.test_pitch import (
    EXPECTED_PITCH,
    FailingResearch,
    FakeRecorder,
    SlowLLM,
    get_events,
    install_pitch_fakes,
)

ADMIN = "/api/v1/admin/pitches"
CSRF = {"X-Requested-With": "portfolio-admin", "Origin": "http://localhost:5173"}
TODAY = date(2026, 10, 14)


@pytest.fixture
def pitch(client, monkeypatch):
    return install_pitch_fakes(monkeypatch)


@pytest.fixture
def recorder(pitch) -> FakeRecorder:
    shared = FakeRecorder()
    app.dependency_overrides[get_pitch_recorder] = lambda: shared
    return shared


# --- recording ------------------------------------------------------------------------------


def test_completed_run_is_recorded_with_text_and_spend(client, recorder):
    get_events(client)

    (result,) = recorder.results
    assert result.outcome == "completed"
    assert result.company == "Spotify"
    assert result.role == "Backend engineer"
    assert result.pitch_text == EXPECTED_PITCH
    assert result.error_code is None
    assert result.searches == 1
    assert isinstance(result.estimated_cost_usd, Decimal)
    assert result.estimated_cost_usd > 0  # at least the search is priced
    assert result.duration_ms >= 0


def test_cached_replay_is_recorded_as_free(client, recorder):
    get_events(client)
    get_events(client, company="SPOTIFY", role="backend ENGINEER")

    cached = recorder.results[1]
    assert cached.outcome == "cached"
    assert cached.pitch_text == EXPECTED_PITCH
    assert cached.company == "SPOTIFY"
    assert (cached.input_tokens, cached.output_tokens, cached.searches) == (0, 0, 0)
    assert cached.estimated_cost_usd == 0


def test_timeout_is_recorded(client, recorder, pitch, monkeypatch):
    monkeypatch.setattr(settings, "pitch_timeout_seconds", 0.2)
    pitch[2].make_llm = lambda: SlowLLM(delay=5)

    get_events(client)

    (result,) = recorder.results
    assert (result.outcome, result.error_code) == ("timeout", "timeout")


def test_upstream_failure_is_recorded(client, recorder, pitch):
    from src.services.pitch.llm import PitchUpstreamError

    pitch[2].make_research = lambda: FailingResearch(PitchUpstreamError("search down"))

    get_events(client)

    (result,) = recorder.results
    assert (result.outcome, result.error_code) == ("upstream_error", "upstream")
    assert result.pitch_text is None


def test_internal_failure_is_recorded(client, recorder, pitch):
    pitch[2].make_research = lambda: FailingResearch(RuntimeError("boom"))

    get_events(client)

    (result,) = recorder.results
    assert (result.outcome, result.error_code) == ("internal_error", "internal")


def test_refused_runs_are_not_recorded(client, recorder, monkeypatch):
    client.get("/api/v1/pitch/stream", params={"company": "<script>", "role": "x"})
    monkeypatch.setattr(settings, "pitch_daily_cap", 0)
    client.get("/api/v1/pitch/stream", params={"company": "Spotify", "role": "Backend"})

    assert recorder.results == []


@pytest.mark.asyncio
async def test_disconnect_is_recorded_with_partial_text():
    recorder = FakeRecorder()
    run = PitchRun(
        PitchPipeline(FakeLLM(), FakeResearch()),
        "Spotify",
        "Backend engineer",
        PitchCache(ttl_hours=24),
        timeout_seconds=30,
        recorder=recorder,
    )
    stream = run.stream()
    async for message in stream:
        if message["event"] == "token":
            break  # the visitor leaves after the first word
    await stream.aclose()

    (result,) = recorder.results
    assert result.outcome == "disconnected"
    assert result.error_code is None
    assert result.pitch_text  # what had been streamed so far
    assert EXPECTED_PITCH.startswith(result.pitch_text)


def test_a_broken_recorder_never_breaks_the_stream(client, pitch):
    class BrokenRecorder:
        def record(self, result) -> None:
            raise RuntimeError("history store unavailable")

    app.dependency_overrides[get_pitch_recorder] = BrokenRecorder

    response, events = get_events(client)

    assert events[-1][0] == "done"
    assert "history store unavailable" not in response.text


@pytest.mark.asyncio
async def test_db_recorder_logs_failures_and_never_raises():
    class BrokenSession:
        async def __aenter__(self):
            raise ConnectionError("database down")

        async def __aexit__(self, *exc):
            return False

    recorder = DbPitchRecorder(lambda: BrokenSession())
    recorder.record(_result())  # schedules the write; must not raise

    await recorder.drain(wait_seconds=1)  # the failing write finished quietly


def test_no_visitor_data_is_stored():
    columns = set(PitchRunRecord.__table__.columns.keys())

    assert columns == {
        "id",
        "company",
        "role",
        "outcome",
        "pitch_text",
        "error_code",
        "duration_ms",
        "input_tokens",
        "output_tokens",
        "searches",
        "estimated_cost_usd",
        "created_at",
    }
    assert not any("ip" in name.split("_") or "agent" in name for name in columns)


# --- admin endpoints ------------------------------------------------------------------------


def _result(**overrides) -> PitchRunResult:
    values = {
        "company": "Spotify",
        "role": "Backend engineer",
        "outcome": "completed",
        "pitch_text": "Hi Spotify, " + "x" * 300,
        "error_code": None,
        "duration_ms": 1800,
        "input_tokens": 900,
        "output_tokens": 120,
        "searches": 1,
        "estimated_cost_usd": Decimal("0.008570"),
    }
    values.update(overrides)
    return PitchRunResult(**values)


def _record(minutes_ago: int, **overrides) -> PitchRunRecord:
    record = PitchRunRecord(id=uuid.uuid4(), **vars(_result(**overrides)))
    record.created_at = datetime(2026, 10, 14, 12, 0, tzinfo=UTC) - timedelta(minutes=minutes_ago)
    return record


class FakeRuns:
    """In-memory PitchRunRepository with the same contract."""

    def __init__(self, records: list[PitchRunRecord]):
        self.records = records
        self.since_calls: list[datetime] = []

    async def list(self, *, limit, offset, q=None, outcome=None):
        rows = [
            r
            for r in self.records
            if (not q or q.lower() in r.company.lower() or q.lower() in r.role.lower())
            and (not outcome or r.outcome == outcome)
        ]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return rows[offset : offset + limit], len(rows)

    async def get(self, run_id):
        return next((r for r in self.records if r.id == run_id), None)

    async def delete(self, record) -> None:
        self.records.remove(record)

    async def counts_since(self, since: datetime) -> PitchRunCounts:
        self.since_calls.append(since)
        rows = [r for r in self.records if r.created_at >= since]
        return PitchRunCounts(
            runs=len(rows),
            cached=sum(r.outcome == "cached" for r in rows),
            failed=sum(r.outcome in {"timeout", "upstream_error", "internal_error"} for r in rows),
            estimated_cost_usd=sum((r.estimated_cost_usd or Decimal(0) for r in rows), Decimal(0)),
        )


class FakeUsageReader:
    def __init__(self, runs: int):
        self.runs = runs
        self.days: list[date] = []

    async def runs_on(self, day: date) -> int:
        self.days.append(day)
        return self.runs


@pytest.fixture
def history(client):
    runs = FakeRuns(
        [
            _record(1, company="Spotify", role="Backend engineer"),
            _record(5, company="Zalando", role="Platform", outcome="cached", estimated_cost_usd=0),
            _record(9, company="Monzo", role="SRE", outcome="timeout", pitch_text=None),
            _record(60 * 24 * 3, company="Stripe", role="API engineer"),  # 3 days ago
            _record(60 * 24 * 40, company="Old", role="Ancient"),  # outside the 30-day window
        ]
    )
    usage = FakeUsageReader(runs=7)
    service = PitchHistoryService(runs, usage, today=lambda: TODAY)
    app.dependency_overrides[get_pitch_history_service] = lambda: service
    return runs, usage


@pytest.fixture
def admin(client, fake_repo):
    login_as(client, make_user(fake_repo, email="admin@example.com", is_superuser=True))
    return client


def test_list_is_newest_first_with_preview(admin, history):
    body = admin.get(ADMIN).json()

    assert body["total"] == 5
    assert (body["limit"], body["offset"]) == (20, 0)
    assert [item["company"] for item in body["items"]] == [
        "Spotify",
        "Zalando",
        "Monzo",
        "Stripe",
        "Old",
    ]
    first = body["items"][0]
    assert set(first) == {
        "id",
        "company",
        "role",
        "outcome",
        "duration_ms",
        "estimated_cost_usd",
        "created_at",
        "preview",
    }
    assert len(first["preview"]) == 160
    assert first["estimated_cost_usd"] == pytest.approx(0.00857)
    assert body["items"][2]["preview"] is None


def test_list_paginates(admin, history):
    body = admin.get(ADMIN, params={"limit": 2, "offset": 2}).json()

    assert [item["company"] for item in body["items"]] == ["Monzo", "Stripe"]
    assert (body["total"], body["limit"], body["offset"]) == (5, 2, 2)


def test_list_searches_company_or_role_case_insensitively(admin, history):
    assert [i["company"] for i in admin.get(ADMIN, params={"q": "SPOT"}).json()["items"]] == [
        "Spotify"
    ]
    assert [i["company"] for i in admin.get(ADMIN, params={"q": "platform"}).json()["items"]] == [
        "Zalando"
    ]
    assert admin.get(ADMIN, params={"q": "   "}).json()["total"] == 5  # blank = no filter


def test_list_filters_by_outcome(admin, history):
    body = admin.get(ADMIN, params={"outcome": "timeout"}).json()

    assert [i["company"] for i in body["items"]] == ["Monzo"]


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"q": "x" * 81}, {"outcome": "exploded"}],
)
def test_list_rejects_bad_parameters(admin, history, params):
    response = admin.get(ADMIN, params=params)

    assert response.status_code == 422
    assert response.json()["success"] is False


def test_detail_has_every_column(admin, history):
    run = history[0].records[0]

    body = admin.get(f"{ADMIN}/{run.id}").json()

    assert body["id"] == str(run.id)
    assert body["pitch_text"] == run.pitch_text
    assert (body["input_tokens"], body["output_tokens"], body["searches"]) == (900, 120, 1)
    assert body["error_code"] is None


def test_detail_unknown_id_is_404(admin, history):
    response = admin.get(f"{ADMIN}/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["message"] == "Pitch run not found."


def test_delete_needs_csrf(admin, history):
    run = history[0].records[0]

    response = admin.delete(f"{ADMIN}/{run.id}")

    assert response.status_code == 403
    assert response.json()["errors"] == {"code": "csrf"}
    assert run in history[0].records


def test_delete_removes_the_run(admin, history):
    run = history[0].records[0]

    assert admin.delete(f"{ADMIN}/{run.id}", headers=CSRF).status_code == 204
    assert run not in history[0].records
    assert admin.delete(f"{ADMIN}/{run.id}", headers=CSRF).status_code == 404


def test_stats_today_and_last_30_days(admin, history, monkeypatch):
    monkeypatch.setattr(settings, "pitch_enabled", True)
    monkeypatch.setattr(settings, "pitch_daily_cap", 100)
    runs, usage = history

    body = admin.get(f"{ADMIN}/stats").json()

    assert body == {
        "enabled": True,
        "daily_cap": 100,
        "today": {"paid_runs": 7, "cached": 1, "failed": 1},
        "last_30_days": {
            "runs": 4,
            "cached": 1,
            "failed": 1,
            "estimated_cost_usd": pytest.approx(0.008570 * 3),
        },
    }
    # UTC day boundaries: today's midnight, and 29 days before it (30 calendar days).
    assert runs.since_calls == [
        datetime(2026, 10, 14, tzinfo=UTC),
        datetime(2026, 9, 15, tzinfo=UTC),
    ]
    assert usage.days == [TODAY]


def test_stats_route_is_not_captured_by_the_id_route(admin, history):
    assert admin.get(f"{ADMIN}/stats").status_code == 200


ROUTES = [
    ("get", ADMIN),
    ("get", f"{ADMIN}/stats"),
    ("get", f"{ADMIN}/{uuid.uuid4()}"),
    ("delete", f"{ADMIN}/{uuid.uuid4()}"),
]


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_anonymous_is_401(client, history, method, path):
    assert getattr(client, method)(path, headers=CSRF).status_code == 401


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_non_superuser_is_403(client, history, fake_repo, method, path):
    login_as(client, make_user(fake_repo, email="user@example.com", is_superuser=False))

    assert getattr(client, method)(path, headers=CSRF).status_code == 403


def test_delete_is_audited(admin, history):
    from loguru import logger

    lines: list[str] = []
    sink = logger.add(lines.append, format="{message}")
    try:
        run = history[0].records[0]
        admin.delete(f"{ADMIN}/{run.id}", headers=CSRF)
    finally:
        logger.remove(sink)

    assert any(
        "admin@example.com" in line and "deleted pitch run" in line and str(run.id) in line
        for line in lines
    )


def test_recorded_result_matches_the_model():
    # Every PitchRunResult field maps onto a PitchRunRecord column (the DB recorder relies on it).
    record = PitchRunRecord(**json.loads(json.dumps(vars(_result()), default=str)))

    assert record.company == "Spotify"


def test_drain_waits_for_pending_writes():
    written: list[str] = []

    class SlowSession:
        async def __aenter__(self):
            await asyncio.sleep(0.05)
            return self

        async def __aexit__(self, *exc):
            return False

        def add(self, record):
            written.append(record.company)

        async def commit(self):
            return None

        async def rollback(self):
            return None

    async def scenario():
        recorder = DbPitchRecorder(lambda: SlowSession())
        recorder.record(_result(company="Drained"))
        await recorder.drain(wait_seconds=1)

    asyncio.run(scenario())

    assert written == ["Drained"]
