import json
import re
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, IPvAnyNetwork, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# scheme://host[:port], exactly what browsers send in the Origin header. These values also go
# into CORS and a Content-Security-Policy header, so anything else (paths, "*", spaces, ";")
# is rejected at startup rather than silently widening a policy.
_ORIGIN = re.compile(r"https?://[A-Za-z0-9.-]+(?::\d{1,5})?")

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
    # How long GET /health/ready waits for `SELECT 1`. Generous by default: a serverless
    # database (Neon) can take a few seconds to wake from idle.
    health_ready_timeout_seconds: float = Field(default=10, ge=0.5, le=30)

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

    # --- Contact form ---
    # Public URL of a square image shown at the top of contact emails; empty = no image.
    email_avatar_url: str = ""
    # Send visitors a "message received" email. Only honoured when TURNSTILE_SECRET is
    # also set, so the form can't be abused to mail arbitrary addresses.
    contact_confirmation_enabled: bool = False

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
    # Which LLM writes the pitch. "fake" is deterministic and free (tests, local dev).
    pitch_llm_provider: Literal["gemini", "fake"] = "gemini"
    pitch_llm_model: str = "gemini-2.5-flash"
    # Which web search backs the researcher agent. "fake" returns canned results.
    pitch_research_provider: Literal["tavily", "fake"] = "tavily"
    # Identical (company, role) requests replay from memory for this long (per process).
    pitch_cache_ttl_hours: int = Field(default=24, ge=0)
    # Hard ceiling on one run, end to end.
    pitch_timeout_seconds: float = Field(default=45, gt=0)

    # --- Error tracking (optional) ---
    # Empty DSN = Sentry off. See src/core/observability.py for what is (not) sent.
    sentry_dsn: SecretStr = SecretStr("")
    # Empty = use ENV.
    sentry_environment: str = ""
    sentry_traces_sample_rate: float = Field(default=0, ge=0, le=1)

    # --- Image uploads (content admin) ---
    # Same names as the server's existing .env. All optional: an empty bucket disables uploads.
    aws_access_key_id: str = ""
    aws_secret_access_key: SecretStr = SecretStr("")
    aws_region: str = ""
    s3_bucket_name: str = ""
    # Where uploaded images are served from (CloudFront or custom domain); empty means the
    # bucket's own URL: https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com
    s3_public_base_url: str = ""
    # Every upload key starts with this; the IAM key only needs access to this prefix.
    s3_upload_prefix: str = "portfolio/"
    s3_max_upload_mb: int = Field(default=5, gt=0, le=50)

    @property
    def uploads_enabled(self) -> bool:
        return bool(self.s3_bucket_name)

    @property
    def s3_base_url(self) -> str:
        if self.s3_public_base_url:
            return self.s3_public_base_url.rstrip("/")
        if self.s3_bucket_name and self.aws_region:
            return f"https://{self.s3_bucket_name}.s3.{self.aws_region}.amazonaws.com"
        if self.s3_bucket_name:
            return f"https://{self.s3_bucket_name}.s3.amazonaws.com"
        return ""

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

    @field_validator("allowed_origins")
    @classmethod
    def check_origins(cls, value: list[str]) -> list[str]:
        # A trailing slash is a common copy-paste slip; browsers never send one.
        origins = [origin.rstrip("/") for origin in value]
        invalid = [origin for origin in origins if not _ORIGIN.fullmatch(origin)]
        if invalid:
            raise ValueError(
                "ALLOWED_ORIGINS entries must look like https://example.com or "
                f"http://localhost:5173 (no path or wildcard); got {invalid}"
            )
        return origins

    @field_validator("trusted_proxy_ips", mode="before")
    @classmethod
    def split_proxies(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("s3_upload_prefix")
    @classmethod
    def normalise_prefix(cls, value: str) -> str:
        value = value.strip().lstrip("/")
        if not value:
            raise ValueError("S3_UPLOAD_PREFIX must not be empty")
        return value if value.endswith("/") else f"{value}/"

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
        if self.is_prod and self.pitch_enabled:
            self._check_pitch_ready()
        return self

    def pitch_readiness_problems(self) -> list[str]:
        """Why Pitch Me isn't safe to run with real money in prod (empty = ready).

        Names settings only, never their values. Also used by scripts/pitch_golive_check.py.
        """
        problems = []
        if not self.turnstile_secret.get_secret_value():
            problems.append("TURNSTILE_SECRET is required (bot protection)")
        if self.pitch_llm_provider == "fake" or self.pitch_research_provider == "fake":
            problems.append("PITCH_LLM_PROVIDER and PITCH_RESEARCH_PROVIDER cannot be fake")
        if self.pitch_llm_provider == "gemini" and not self.gemini_api_key.get_secret_value():
            problems.append("GEMINI_API_KEY is required for PITCH_LLM_PROVIDER=gemini")
        if self.pitch_research_provider == "tavily" and not self.tavily_api_key.get_secret_value():
            problems.append("TAVILY_API_KEY is required for PITCH_RESEARCH_PROVIDER=tavily")
        return problems

    def _check_pitch_ready(self) -> None:
        """Pitch Me spends money on every run: refuse to enable it half-configured in prod."""
        if problems := self.pitch_readiness_problems():
            raise ValueError(
                "PITCH_ENABLED=true with ENV=prod, but Pitch Me is not safe to run: "
                + "; ".join(problems)
                + ". Fix these or set PITCH_ENABLED=false."
            )


settings = Settings()
