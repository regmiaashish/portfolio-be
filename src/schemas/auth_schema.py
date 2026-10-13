from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def _normalise_email(value: str) -> str:
    return value.strip().lower()


class AuthTokens(BaseModel):
    access_token: str = Field(..., description="JWT access token")
    refresh_token: str = Field(..., description="JWT refresh token")


class RegisterRequest(BaseModel):
    # Privilege fields (is_active, is_superuser) are never client-controlled;
    # unknown fields are rejected so attempts to send them fail loudly.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=100, description="User's full name")
    email: EmailStr = Field(..., description="User's email address")
    password: str = Field(..., min_length=12, max_length=128, description="User's password")

    normalise_email = field_validator("email")(_normalise_email)


class LoginRequest(BaseModel):
    email: EmailStr = Field(..., description="User's email address")
    # max_length bounds the argon2 work an anonymous caller can trigger.
    password: str = Field(..., min_length=1, max_length=128, description="User's password")

    normalise_email = field_validator("email")(_normalise_email)
