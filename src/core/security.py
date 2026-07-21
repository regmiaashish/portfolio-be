from datetime import datetime, timedelta, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Response
from jose import JWTError, jwt

from src.core.config import settings

password_hasher = PasswordHasher()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return password_hasher.verify(hashed_password, plain_password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def _build_token_payload(
    data: dict[str, Any],
    expires_delta: timedelta,
    token_type: str,
) -> dict[str, Any]:
    issued_at = utc_now()
    payload = data.copy()
    payload.update(
        {
            "exp": issued_at + expires_delta,
            "iat": issued_at,
            "type": token_type,
        }
    )
    return payload


def create_access_token(data: dict[str, Any]) -> str:
    payload = _build_token_payload(
        data=data,
        expires_delta=timedelta(minutes=settings.jwt_access_token_expire_minutes),
        token_type="access",
    )
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(data: dict[str, Any]) -> str:
    payload = _build_token_payload(
        data=data,
        expires_delta=timedelta(days=settings.jwt_refresh_token_expire_days),
        token_type="refresh",
    )
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def get_access_token_expires_in() -> int:
    return settings.jwt_access_token_expire_minutes * 60


def get_refresh_token_expires_in() -> int:
    return settings.jwt_refresh_token_expire_days * 24 * 60 * 60


def verify_token(token: str, token_type: str = "access") -> dict[str, Any] | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None

    if payload.get("type") != token_type:
        return None

    return payload


def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    samesite = settings.cookie_samesite.lower()
    secure_flag = True if samesite == "none" else settings.cookie_secure

    cookie_options = {
        "httponly": True,
        "secure": secure_flag,
        "samesite": samesite,
        "path": "/",
    }

    if settings.cookie_domain:
        cookie_options["domain"] = settings.cookie_domain

    response.set_cookie(
        key=settings.access_token_cookie_name,
        value=access_token,
        max_age=get_access_token_expires_in(),
        **cookie_options,
    )
    response.set_cookie(
        key=settings.refresh_token_cookie_name,
        value=refresh_token,
        max_age=get_refresh_token_expires_in(),
        **cookie_options,
    )


def clear_auth_cookies(response: Response) -> None:
    samesite = settings.cookie_samesite.lower()
    secure_flag = True if samesite == "none" else settings.cookie_secure

    cookie_options = {
        "path": "/",
        "secure": secure_flag,
        "samesite": samesite,
    }

    if settings.cookie_domain:
        cookie_options["domain"] = settings.cookie_domain

    response.delete_cookie(settings.access_token_cookie_name, **cookie_options)
    response.delete_cookie(settings.refresh_token_cookie_name, **cookie_options)