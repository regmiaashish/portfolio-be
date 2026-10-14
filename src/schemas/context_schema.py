from typing import Literal

from pydantic import BaseModel


class WeatherContext(BaseModel):
    temperature_c: float | None = None
    apparent_temperature_c: float | None = None
    precipitation_mm: float | None = None
    weather_code: int | None = None
    description: str | None = None
    is_day: bool | None = None


class VisitorContext(BaseModel):
    """What the hero greeting knows about the visitor. Every field degrades to null."""

    city: str | None = None
    country: str | None = None
    country_code: str | None = None
    timezone: str | None = None
    local_time: str | None = None
    utc_offset_minutes: int | None = None
    weather: WeatherContext | None = None
    currency_code: str | None = None
    currency_symbol: str | None = None
    base_currency: Literal["GBP"] = "GBP"
    conversion_rate: float | None = None
    hourly_rate_gbp: float | None = None
    hourly_rate_local: float | None = None
    my_timezone: Literal["Asia/Kathmandu"] = "Asia/Kathmandu"
    my_local_time: str
    timezone_overlap_hours: float | None = None
