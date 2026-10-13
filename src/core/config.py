import json
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, IPvAnyNetwork, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

MIN_PROD_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """Application settings, read from environment variables and then `.env`.

    Every key is documented in `.env.example`. Secrets are `SecretStr`, so they never
    appear in reprs, logs or tracebacks; call `.get_secret_value()` where needed.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Unknown keys (e.g. retired AWS_* settings in an old .env) are ignored, not errors.
        extra="ignore",
        # An empty value (e.g. "HOURLY_RATE_GBP=") means "not set": the default applies.
        env_ignore_empty=True,
    )

    # --- Runtime ---
    # Defaults to prod so a deploy that forgets ENV gets the secure behaviour.
    env: Literal["dev", "test", "prod"] = "prod"

    # --- Database ---
    database_url: SecretStr

    # --- CORS ---
    # NoDecode: accept a JSON list or a comma-separated string (see split_origins).
    allowed_origins: Annotated[list[str], NoDecode] = []

    # --- Client IP and rate limiting ---
    # Proxies whose forwarding headers are believed (CIDRs or single IPs). Empty = trust
    # no one and rate-limit by the TCP peer address. See .env.example for examples.
    trusted_proxy_ips: Annotated[list[IPvAnyNetwork], NoDecode] = []
    # Which header a trusted proxy uses to pass on the visitor's address.
    client_ip_header: Literal["x-forwarded-for", "cf-connecting-ip"] = "x-forwarded-for"
    # "memory://" is per worker process; use redis://... to share limits across workers.
    rate_limit_storage_uri: str = "memory://"

    # --- Email (contact form) ---
    smtp_host: str
    smtp_port: int = 587
    smtp_user: str
    smtp_password: SecretStr
    super_admin_email: str

    # --- Links shown in emails ---
    portfolio_url: str
    github_url: str
    linkedin_url: str

    # --- Auth cookies ---
    cookie_domain: str = ""
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_secure: bool = True
    access_token_cookie_name: str = "access_token"
    refresh_token_cookie_name: str = "refresh_token"

    # --- JWT ---
    jwt_secret_key: SecretStr
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_access_token_expire_minutes: int = Field(default=60, gt=0)
    jwt_refresh_token_expire_days: int = Field(default=7, gt=0)

    # --- Visitor context ---
    context_mock: bool = False
    hourly_rate_gbp: Decimal | None = Field(default=None, gt=0)

    # --- Pitch Me and anti-abuse (used from Phases 3-4) ---
    gemini_api_key: SecretStr = SecretStr("")
    tavily_api_key: SecretStr = SecretStr("")
    turnstile_secret: SecretStr = SecretStr("")
    pitch_enabled: bool = False
    pitch_daily_cap: int = Field(default=100, ge=0)

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return []
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return [origin.strip() for origin in value.split(",") if origin.strip()]
            if isinstance(parsed, list):
                return [str(origin).strip() for origin in parsed]
        return value

    @field_validator("trusted_proxy_ips", mode="before")
    @classmethod
    def split_proxies(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("client_ip_header", "cookie_samesite", mode="before")
    @classmethod
    def lowercase_choice(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def check_production_safety(self) -> "Settings":
        if (
            self.is_prod
            and len(self.jwt_secret_key.get_secret_value()) < MIN_PROD_JWT_SECRET_LENGTH
        ):
            raise ValueError(
                f"JWT_SECRET_KEY must be at least {MIN_PROD_JWT_SECRET_LENGTH} characters when "
                'ENV=prod. Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return self


settings = Settings()
