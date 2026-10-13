from pydantic import BaseModel, ConfigDict, EmailStr, Field


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


class loginRequest(BaseModel):
    email: EmailStr = Field(..., description="User's email address")
    password: str = Field(..., description="User's password")


class loginResponse(BaseModel):
    message: str = Field(..., description="Response message")

    class Config:
        from_attributes = True
