from pydantic import BaseModel


class WeatherContext(BaseModel):
    temperature_c: float | None = None
    apparent_temperature_c: float | None = None
    precipitation_mm: float | None = None
    weather_code: int | None = None
    description: str | None = None
    is_day: bool | None = None


class VisitorContext(BaseModel):
    ip: str
    city: str | None = None
    country: str | None = None
    country_code: str | None = None
    timezone: str | None = None
    currency_code: str | None = None
    currency_symbol: str | None = None
    base_currency: str
    conversion_rate: float | None = None
    weather: WeatherContext
