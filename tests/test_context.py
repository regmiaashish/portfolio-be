"""Visitor context engine (plan 3.2): providers mocked with respx, a fixed clock."""

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
import respx

from src.api.v1 import context_router
from src.core.config import settings
from src.core.dependencies import get_context_service
from src.main import app
from src.services.context_service import (
    CURRENCY_URL,
    WEATHER_URL,
    ContextService,
    round_half_up_to_half,
    working_overlap_hours,
)

URL = "/api/v1/context"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)  # London is on BST (UTC+1)
IPAPI = "https://ipapi.co/{ip}/json/"
IPWHO = "https://ipwho.is/{ip}"

GEO_US = {
    "city": "Mountain View",
    "country_name": "United States",
    "country_code": "US",
    "timezone": "America/Los_Angeles",
    "currency": "USD",
    "latitude": 37.4,
    "longitude": -122.1,
}
WEATHER = {
    "current": {
        "temperature_2m": 21.5,
        "apparent_temperature": 20.9,
        "precipitation": 0.0,
        "weather_code": 1,
        "is_day": 1,
    }
}
FX_USD = {"rates": {"USD": 1.25}}


@pytest.fixture
def providers():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(IPAPI.format(ip="8.8.8.8"), name="ipapi").mock(
            return_value=httpx.Response(200, json=GEO_US)
        )
        mock.get(WEATHER_URL, name="weather").mock(return_value=httpx.Response(200, json=WEATHER))
        mock.get(CURRENCY_URL, name="fx").mock(return_value=httpx.Response(200, json=FX_USD))
        yield mock


@pytest_asyncio.fixture
async def context_client(client, providers, monkeypatch):
    """`client` with a ContextService on its own HTTP client and the fixed clock.

    tests/__init__.py turns CONTEXT_MOCK on for the suite; these tests need real lookups.
    """
    monkeypatch.setattr(settings, "context_mock", False)
    async with httpx.AsyncClient() as http:
        service = ContextService(http, clock=lambda: NOW)
        app.dependency_overrides[get_context_service] = lambda: service
        yield client


# --- happy path ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_context(context_client, providers, monkeypatch):
    monkeypatch.setattr(settings, "hourly_rate_gbp", Decimal("40"))

    response = context_client.get(URL, params={"ip": "8.8.8.8"})

    assert response.status_code == 200
    body = response.json()
    assert "ip" not in body
    assert body["city"] == "Mountain View"
    assert body["timezone"] == "America/Los_Angeles"
    assert body["local_time"] == "2026-10-14T05:00:00-07:00"
    assert body["utc_offset_minutes"] == -420
    assert body["weather"]["description"] == "mainly clear skies"
    assert body["currency_symbol"] == "$"
    assert body["conversion_rate"] == 1.25
    assert body["hourly_rate_gbp"] == 40.0
    assert body["hourly_rate_local"] == 50.0
    assert body["my_timezone"] == "Asia/Kathmandu"
    assert body["my_local_time"] == "2026-10-14T17:45:00+05:45"
    assert body["timezone_overlap_hours"] == 0.0


@pytest.mark.asyncio
async def test_hourly_rates_null_when_not_configured(context_client):
    body = context_client.get(URL, params={"ip": "8.8.8.8"}).json()

    assert body["hourly_rate_gbp"] is None
    assert body["hourly_rate_local"] is None


# --- where the IP comes from ---------------------------------------------------------


@pytest.mark.asyncio
async def test_ip_param_honoured_outside_prod(context_client, providers):
    assert context_client.get(URL, params={"ip": "8.8.8.8"}).status_code == 200
    assert providers["ipapi"].called


@pytest.mark.asyncio
async def test_ip_param_ignored_in_prod(context_client, providers, monkeypatch):
    monkeypatch.setattr(settings, "env", "prod")
    monkeypatch.setattr(context_router, "client_ip", lambda request: "1.1.1.1")
    providers.get(IPAPI.format(ip="1.1.1.1"), name="ipapi-real").mock(
        return_value=httpx.Response(200, json=GEO_US)
    )

    response = context_client.get(URL, params={"ip": "8.8.8.8"})

    assert response.status_code == 200
    assert providers["ipapi-real"].called
    assert not providers["ipapi"].called


@pytest.mark.asyncio
async def test_private_ip_is_400(context_client, providers):
    response = context_client.get(URL, params={"ip": "10.0.0.1"})

    assert response.status_code == 400
    assert response.json()["success"] is False
    assert not providers["ipapi"].called


@pytest.mark.asyncio
async def test_garbage_ip_is_400(context_client):
    assert context_client.get(URL, params={"ip": "not-an-ip"}).status_code == 400


# --- degradation ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_geo_falls_back_to_ipwho(context_client, providers):
    providers["ipapi"].mock(return_value=httpx.Response(429, json={"error": True}))
    providers.get(IPWHO.format(ip="8.8.8.8")).mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "city": "Sydney",
                "country": "Australia",
                "country_code": "AU",
                "timezone": {"id": "Australia/Sydney"},
                "currency": {"code": "USD"},
                "latitude": -33.9,
                "longitude": 151.2,
            },
        )
    )

    body = context_client.get(URL, params={"ip": "8.8.8.8"}).json()

    assert body["city"] == "Sydney"
    assert body["timezone_overlap_hours"] == 4.0


@pytest.mark.asyncio
async def test_geo_failure_is_503(context_client, providers):
    providers["ipapi"].mock(side_effect=httpx.ConnectError("down"))
    providers.get(IPWHO.format(ip="8.8.8.8")).mock(
        return_value=httpx.Response(200, json={"success": False, "message": "Reserved range"})
    )

    response = context_client.get(URL, params={"ip": "8.8.8.8"})

    assert response.status_code == 503
    assert response.json()["message"] == "Visitor context is temporarily unavailable."


@pytest.mark.asyncio
async def test_weather_failure_still_200(context_client, providers):
    providers["weather"].mock(return_value=httpx.Response(500))

    response = context_client.get(URL, params={"ip": "8.8.8.8"})

    assert response.status_code == 200
    assert response.json()["weather"] is None
    assert response.json()["conversion_rate"] == 1.25


@pytest.mark.asyncio
async def test_fx_failure_nulls_conversion(context_client, providers, monkeypatch):
    monkeypatch.setattr(settings, "hourly_rate_gbp", Decimal("40"))
    providers["fx"].mock(side_effect=httpx.ReadTimeout("slow"))

    body = context_client.get(URL, params={"ip": "8.8.8.8"}).json()

    assert body["conversion_rate"] is None
    assert body["hourly_rate_gbp"] == 40.0
    assert body["hourly_rate_local"] is None
    assert body["weather"] is not None


# --- caching and limits ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_repeat_visits_hit_the_cache(context_client, providers):
    for _ in range(3):
        assert context_client.get(URL, params={"ip": "8.8.8.8"}).status_code == 200

    assert providers["ipapi"].call_count == 1
    assert providers["weather"].call_count == 1
    assert providers["fx"].call_count == 1


@pytest.mark.asyncio
async def test_failures_are_not_cached(context_client, providers):
    providers["weather"].mock(side_effect=[httpx.Response(500), httpx.Response(200, json=WEATHER)])

    first = context_client.get(URL, params={"ip": "8.8.8.8"}).json()
    second = context_client.get(URL, params={"ip": "8.8.8.8"}).json()

    assert first["weather"] is None
    assert second["weather"]["temperature_c"] == 21.5


@pytest.mark.asyncio
async def test_context_is_limited_to_30_per_minute(context_client):
    statuses = [context_client.get(URL, params={"ip": "8.8.8.8"}).status_code for _ in range(31)]

    assert statuses == [200] * 30 + [429]


# --- mock mode -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_mode_makes_no_calls(context_client, providers, monkeypatch):
    monkeypatch.setattr(settings, "context_mock", True)

    body = context_client.get(URL, params={"ip": "127.0.0.1"}).json()

    assert body["city"] == "London"
    assert body["local_time"] == "2026-10-14T13:00:00+01:00"
    assert body["utc_offset_minutes"] == 60
    assert body["timezone_overlap_hours"] == 4.5
    assert not any(route.called for route in providers.routes)


# --- overlap maths (pure) --------------------------------------------------------------

OCT = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
JAN = datetime(2026, 1, 14, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("zone", "now", "expected"),
    [
        ("Asia/Kathmandu", OCT, 9.0),  # same zone: the full day
        ("Asia/Kolkata", OCT, 9.0),  # UTC+5:30 vs +5:45: 8.75 rounds up
        ("Europe/London", OCT, 4.5),  # BST: 08:00-17:00 UTC vs 03:15-12:15 UTC = 4.25
        ("Europe/London", JAN, 3.5),  # GMT: one hour less in winter (DST)
        ("America/New_York", OCT, 0.0),  # no overlap at all
        ("America/New_York", JAN, 0.0),
        ("Pacific/Honolulu", OCT, 1.0),  # their afternoon meets my next morning (0.75)
        ("Australia/Sydney", OCT, 4.0),  # across the date line: 3.75
    ],
)
def test_working_overlap_hours(zone, now, expected):
    assert working_overlap_hours(zone, now) == expected


def test_unknown_timezone_gives_none():
    assert working_overlap_hours("Mars/Olympus_Mons", OCT) is None


@pytest.mark.parametrize(("hours", "expected"), [(4.25, 4.5), (4.24, 4.0), (0.2, 0.0), (8.75, 9.0)])
def test_round_half_up_to_half(hours, expected):
    assert round_half_up_to_half(hours) == expected
