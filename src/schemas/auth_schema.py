from typing import Any

from pydantic import BaseModel, EmailStr, Field

class AuthTokens(BaseModel):
    access_token: str = Field(..., description="JWT access token")
    refresh_token: str = Field(..., description="JWT refresh token")

class RegisterRequest(BaseModel):
    name: str = Field(..., description="User's full name")
    email: EmailStr = Field(..., description="User's email address")
    password: str = Field(..., description="User's password")
    is_active: bool = Field(default=True, description="Indicates if the user is active")
    is_superuser: bool = Field(default=False, description="Indicates if the user has superuser privileges")

class loginRequest(BaseModel):
    email: EmailStr = Field(..., description="User's email address")
    password: str = Field(..., description="User's password")

class loginResponse(BaseModel):
    message: str = Field(..., description="Response message")

    class Config:
        from_attributes = True
        