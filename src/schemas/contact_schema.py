from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

MAX_EMAIL_LENGTH = 254


class ContactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(..., min_length=1, max_length=100, description="Visitor's name")
    email: EmailStr = Field(..., description="Address to reply to")
    message: str = Field(..., min_length=1, max_length=1000)
    # Honeypot: hidden from people, filled in by naive bots. Must stay empty.
    website: str = Field(default="", max_length=200, description="Leave empty.")
    # Cloudflare Turnstile token; required only when the server has TURNSTILE_SECRET set.
    turnstile_token: str | None = Field(default=None, max_length=2048)

    @field_validator("email")
    @classmethod
    def limit_email_length(cls, value: str) -> str:
        if len(value) > MAX_EMAIL_LENGTH:
            raise ValueError(f"Email must be at most {MAX_EMAIL_LENGTH} characters.")
        return value
