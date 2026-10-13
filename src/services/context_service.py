import asyncio
from ipaddress import ip_address
from urllib.parse import quote

import httpx

from src.core.config import settings
from src.schemas.context_schema import VisitorContext, WeatherContext

GEOLOCATION_URL = "https://ipapi.co/{ip}/json/"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
CURRENCY_URL = "https://api.frankfurter.app/latest"
GEOLOCATION_FALLBACK_URL = "https://ipwho.is/{ip}"
BASE_CURRENCY = "GBP"

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


def validate_ip(value: str) -> str:
    try:
        return str(ip_address(value.strip()))
    except ValueError as exc:
        raise ValueError("A valid IPv4 or IPv6 address is required.") from exc


async def _get_json(client: httpx.AsyncClient, url: str, **params: object) -> dict:
    response = await client.get(url, params=params or None)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        # ValueError on purpose: the router maps it to 400.
        raise ValueError("The upstream service returned an invalid response.")  # noqa: TRY004
    return payload


async def _get_geolocation(client: httpx.AsyncClient, normalized_ip: str) -> dict:
    encoded_ip = quote(normalized_ip, safe="")
    try:
        geo = await _get_json(
            client,
            GEOLOCATION_URL.format(ip=encoded_ip),
        )
        if not geo.get("error"):
            return geo
    except httpx.HTTPStatusError:
        # ipapi.co can rate-limit shared development IPs. Try the fallback below.
        pass

    fallback = await _get_json(
        client,
        GEOLOCATION_FALLBACK_URL.format(ip=encoded_ip),
    )
    if fallback.get("success") is False:
        raise ValueError(fallback.get("message", "Unable to locate the visitor."))

    timezone = fallback.get("timezone") or {}
    currency = fallback.get("currency") or {}
    return {
        "city": fallback.get("city"),
        "country_name": fallback.get("country"),
        "country_code": fallback.get("country_code"),
        "timezone": timezone.get("id"),
        "currency": currency.get("code"),
        "latitude": fallback.get("latitude"),
        "longitude": fallback.get("longitude"),
    }


async def _get_weather(
    client: httpx.AsyncClient, latitude: float, longitude: float
) -> WeatherContext:
    payload = await _get_json(
        client,
        WEATHER_URL,
        latitude=latitude,
        longitude=longitude,
        current=("temperature_2m,apparent_temperature,precipitation,weather_code,is_day"),
        timezone="auto",
    )
    current = payload.get("current", {})
    weather_code = current.get("weather_code")
    return WeatherContext(
        temperature_c=current.get("temperature_2m"),
        apparent_temperature_c=current.get("apparent_temperature"),
        precipitation_mm=current.get("precipitation"),
        weather_code=weather_code,
        description=WEATHER_DESCRIPTIONS.get(weather_code, "unknown conditions"),
        is_day=bool(current["is_day"]) if "is_day" in current else None,
    )


async def _get_conversion_rate(client: httpx.AsyncClient, currency_code: str) -> float | None:
    if currency_code == BASE_CURRENCY:
        return 1.0

    payload = await _get_json(
        client,
        CURRENCY_URL,
        **{"from": BASE_CURRENCY},
        to=currency_code,
    )
    rate = payload.get("rates", {}).get(currency_code)
    return float(rate) if rate is not None else None


async def get_visitor_context(visitor_ip: str) -> VisitorContext:
    if settings.context_mock:
        return VisitorContext(
            ip=visitor_ip,
            city="London",
            country="United Kingdom",
            country_code="GB",
            timezone="Europe/London",
            currency_code="GBP",
            currency_symbol="£",
            base_currency=BASE_CURRENCY,
            conversion_rate=1.0,
            weather=WeatherContext(
                temperature_c=18.0,
                apparent_temperature_c=18.0,
                precipitation_mm=0.2,
                weather_code=61,
                description="light rain",
                is_day=True,
            ),
        )

    normalized_ip = validate_ip(visitor_ip)
    timeout = httpx.Timeout(6.0, connect=3.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        geo = await _get_geolocation(client, normalized_ip)

        if geo.get("error"):
            raise ValueError(geo.get("reason", "Unable to locate the visitor."))

        latitude = geo.get("latitude")
        longitude = geo.get("longitude")
        currency_code = geo.get("currency") or BASE_CURRENCY
        if latitude is None or longitude is None:
            raise ValueError("The geolocation service did not return coordinates.")

        weather, conversion_rate = await asyncio.gather(
            _get_weather(client, float(latitude), float(longitude)),
            _get_conversion_rate(client, currency_code),
        )

    return VisitorContext(
        ip=normalized_ip,
        city=geo.get("city"),
        country=geo.get("country_name"),
        country_code=geo.get("country_code"),
        timezone=geo.get("timezone"),
        currency_code=currency_code,
        currency_symbol=CURRENCY_SYMBOLS.get(currency_code, currency_code),
        base_currency=BASE_CURRENCY,
        conversion_rate=conversion_rate,
        weather=weather,
    )
