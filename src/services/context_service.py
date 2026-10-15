"""Visitor context for the hero greeting: where they are, their weather, currency and
how much our working days overlap.

Geolocation runs first; weather and FX then run concurrently. Weather and FX are
optional: if either fails the response still succeeds with those fields null. Only a
geolocation failure makes the whole lookup fail.
"""

import asyncio
import math
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from cachetools import TTLCache
from loguru import logger

from src.core.config import settings
from src.core.exceptions import ServiceUnavailableError
from src.schemas.context_schema import VisitorContext, WeatherContext

GEOLOCATION_URL = "https://ipapi.co/{ip}/json/"
GEOLOCATION_FALLBACK_URL = "https://ipwho.is/{ip}"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
CURRENCY_URL = "https://api.frankfurter.app/latest"

BASE_CURRENCY = "GBP"
MY_TIMEZONE = "Asia/Kathmandu"
WORKDAY_START = time(9)
WORKDAY_END = time(18)

GEO_CACHE_TTL_SECONDS = 30 * 60
FX_CACHE_TTL_SECONDS = 6 * 60 * 60

WEATHER_DESCRIPTIONS = {
    0: "clear skies",
    1: "mainly clear skies",
    2: "partly cloudy skies",
    3: "overcast skies",
    45: "foggy conditions",
    48: "freezing fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    80: "light rain showers",
    81: "rain showers",
    82: "heavy rain showers",
    95: "a thunderstorm",
    96: "a thunderstorm with hail",
    99: "a thunderstorm with heavy hail",
}

CURRENCY_SYMBOLS = {
    "GBP": "£",
    "EUR": "€",
    "USD": "$",
    "NPR": "रू",
    "INR": "₹",
    "JPY": "¥",
    "CAD": "CA$",
    "AUD": "A$",
}

CONTEXT_UNAVAILABLE = "Visitor context is temporarily unavailable."


def _failure(exc: Exception) -> str:
    """An upstream failure without its text: httpx errors quote the request URL, which holds the
    visitor's IP (geolocation) or coordinates (weather), and those never go into the logs."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


class GeolocationError(Exception):
    """Neither geolocation provider could place the IP."""


# --- Pure helpers (no I/O) -----------------------------------------------------------


def _zone(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _workday(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    return (
        datetime.combine(day, WORKDAY_START, tzinfo=zone),
        datetime.combine(day, WORKDAY_END, tzinfo=zone),
    )


def round_half_up_to_half(hours: float) -> float:
    """4.25 -> 4.5, 3.24 -> 3.0. Python's round() would send 4.25 to 4.0 (banker's rounding)."""
    return math.floor(hours * 2 + 0.5) / 2


def working_overlap_hours(
    visitor_timezone: str, now: datetime, my_timezone: str = MY_TIMEZONE
) -> float | None:
    """Hours the visitor's 09:00-18:00 today overlaps my 09:00-18:00 working days.

    My adjacent days count too: across the date line the visitor's afternoon can be my
    next morning. Uses real tz rules, so DST and Nepal's UTC+5:45 are handled. Rounded to
    the nearest half hour (halves round up), never negative.
    """
    visitor_zone, my_zone = _zone(visitor_timezone), _zone(my_timezone)
    if visitor_zone is None or my_zone is None:
        return None

    visitor_start, visitor_end = _workday(now.astimezone(visitor_zone).date(), visitor_zone)
    my_today = now.astimezone(my_zone).date()

    overlap = timedelta()
    for offset in (-1, 0, 1):
        my_start, my_end = _workday(my_today + timedelta(days=offset), my_zone)
        start, end = max(visitor_start, my_start), min(visitor_end, my_end)
        if end > start:
            overlap += end - start
    return max(0.0, round_half_up_to_half(overlap.total_seconds() / 3600))


def _local_time(zone: ZoneInfo | None, now: datetime) -> tuple[str | None, int | None]:
    if zone is None:
        return None, None
    local = now.astimezone(zone)
    offset = local.utcoffset() or timedelta()
    return local.isoformat(timespec="seconds"), int(offset.total_seconds() // 60)


def _hourly_rates(conversion_rate: float | None) -> tuple[float | None, float | None]:
    rate_gbp = float(settings.hourly_rate_gbp) if settings.hourly_rate_gbp is not None else None
    if rate_gbp is None or conversion_rate is None:
        return rate_gbp, None
    return rate_gbp, round(rate_gbp * conversion_rate, 2)


# --- Service --------------------------------------------------------------------------


class ContextService:
    """Looks up visitor context through a shared HTTP client, with in-memory TTL caches.

    The caches are only touched between awaits on a single event loop, so they need no
    lock. Failures are never cached.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        geo_ttl: float = GEO_CACHE_TTL_SECONDS,
        fx_ttl: float = FX_CACHE_TTL_SECONDS,
    ):
        self.client = client
        self.clock = clock
        self._geo_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=2048, ttl=geo_ttl)
        self._weather_cache: TTLCache[str, WeatherContext] = TTLCache(maxsize=2048, ttl=geo_ttl)
        self._fx_cache: TTLCache[str, float] = TTLCache(maxsize=256, ttl=fx_ttl)

    async def get_context(self, ip: str) -> VisitorContext:
        if settings.context_mock:
            return self._mock_context()

        try:
            geo = await self._geolocation(ip)
        except (GeolocationError, httpx.HTTPError, ValueError) as exc:
            logger.warning("Geolocation failed: {}", _failure(exc))
            # `from None`: the provider error's text holds a URL with the visitor's IP, and the
            # 503 handler logs this exception's chain.
            raise ServiceUnavailableError(CONTEXT_UNAVAILABLE) from None

        currency_code = geo.get("currency") or BASE_CURRENCY
        weather, conversion_rate = await asyncio.gather(
            self._weather_or_none(ip, geo), self._conversion_or_none(currency_code)
        )
        return self._build(geo, weather, currency_code, conversion_rate)

    # -- providers --

    async def _get_json(self, url: str, **params: object) -> dict[str, Any]:
        response = await self.client.get(url, params=params or None)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("The upstream service returned an invalid response.")  # noqa: TRY004
        return payload

    async def _geolocation(self, ip: str) -> dict[str, Any]:
        if ip in self._geo_cache:
            return self._geo_cache[ip]

        encoded = quote(ip, safe="")
        geo: dict[str, Any] | None = None
        try:
            primary = await self._get_json(GEOLOCATION_URL.format(ip=encoded))
            if not primary.get("error"):
                geo = {
                    "city": primary.get("city"),
                    "country": primary.get("country_name"),
                    "country_code": primary.get("country_code"),
                    "timezone": primary.get("timezone"),
                    "currency": primary.get("currency"),
                    "latitude": primary.get("latitude"),
                    "longitude": primary.get("longitude"),
                }
        except (httpx.HTTPError, ValueError) as exc:
            # ipapi.co rate-limits shared IPs aggressively; the fallback usually works.
            logger.info("Primary geolocation failed: {}", _failure(exc))

        if geo is None:
            fallback = await self._get_json(GEOLOCATION_FALLBACK_URL.format(ip=encoded))
            if fallback.get("success") is False:
                raise GeolocationError(fallback.get("message", "Unable to locate the visitor."))
            geo = {
                "city": fallback.get("city"),
                "country": fallback.get("country"),
                "country_code": fallback.get("country_code"),
                "timezone": (fallback.get("timezone") or {}).get("id"),
                "currency": (fallback.get("currency") or {}).get("code"),
                "latitude": fallback.get("latitude"),
                "longitude": fallback.get("longitude"),
            }

        self._geo_cache[ip] = geo
        return geo

    async def _weather_or_none(self, ip: str, geo: dict[str, Any]) -> WeatherContext | None:
        if ip in self._weather_cache:
            return self._weather_cache[ip]
        latitude, longitude = geo.get("latitude"), geo.get("longitude")
        if latitude is None or longitude is None:
            return None
        try:
            payload = await self._get_json(
                WEATHER_URL,
                latitude=float(latitude),
                longitude=float(longitude),
                current="temperature_2m,apparent_temperature,precipitation,weather_code,is_day",
                timezone="auto",
            )
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Weather lookup failed: {}", _failure(exc))
            return None

        current = payload.get("current") or {}
        code = current.get("weather_code")
        weather = WeatherContext(
            temperature_c=current.get("temperature_2m"),
            apparent_temperature_c=current.get("apparent_temperature"),
            precipitation_mm=current.get("precipitation"),
            weather_code=code,
            description=WEATHER_DESCRIPTIONS.get(code, "unknown conditions"),
            is_day=bool(current["is_day"]) if "is_day" in current else None,
        )
        self._weather_cache[ip] = weather
        return weather

    async def _conversion_or_none(self, currency_code: str) -> float | None:
        if currency_code == BASE_CURRENCY:
            return 1.0
        if currency_code in self._fx_cache:
            return self._fx_cache[currency_code]
        try:
            payload = await self._get_json(
                CURRENCY_URL, **{"from": BASE_CURRENCY}, to=currency_code
            )
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("FX lookup failed for {}: {}", currency_code, _failure(exc))
            return None
        rate = (payload.get("rates") or {}).get(currency_code)
        if rate is None:
            return None
        self._fx_cache[currency_code] = float(rate)
        return float(rate)

    # -- assembly --

    def _build(
        self,
        geo: dict[str, Any],
        weather: WeatherContext | None,
        currency_code: str,
        conversion_rate: float | None,
    ) -> VisitorContext:
        now = self.clock()
        timezone = geo.get("timezone")
        zone = _zone(timezone)
        local_time, utc_offset = _local_time(zone, now)
        my_local_time, _ = _local_time(_zone(MY_TIMEZONE), now)
        rate_gbp, rate_local = _hourly_rates(conversion_rate)
        return VisitorContext(
            city=geo.get("city"),
            country=geo.get("country"),
            country_code=geo.get("country_code"),
            timezone=timezone if zone else None,
            local_time=local_time,
            utc_offset_minutes=utc_offset,
            weather=weather,
            currency_code=currency_code,
            currency_symbol=CURRENCY_SYMBOLS.get(currency_code, currency_code),
            conversion_rate=conversion_rate,
            hourly_rate_gbp=rate_gbp,
            hourly_rate_local=rate_local,
            my_local_time=my_local_time or now.isoformat(timespec="seconds"),
            timezone_overlap_hours=working_overlap_hours(timezone, now) if zone else None,
        )

    def _mock_context(self) -> VisitorContext:
        geo = {
            "city": "London",
            "country": "United Kingdom",
            "country_code": "GB",
            "timezone": "Europe/London",
        }
        weather = WeatherContext(
            temperature_c=18.0,
            apparent_temperature_c=18.0,
            precipitation_mm=0.2,
            weather_code=61,
            description="light rain",
            is_day=True,
        )
        return self._build(geo, weather, "GBP", 1.0)
