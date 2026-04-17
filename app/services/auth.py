"""Authentication service: password hashing and JWT token management.

Security design:
- Passwords hashed with bcrypt (cost factor 12)
- JWT tokens stored in httpOnly + Secure + SameSite=lax cookies (not localStorage)
  → protects against XSS token theft
- Token payload contains user_id + role only (minimal surface)
- Tokens expire after JWT_EXPIRE_MINUTES (default 30 days, configurable)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import User
from app.db.session import get_session

log = logging.getLogger(__name__)

ALGORITHM = "HS256"
COOKIE_NAME = "ll_token"

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """Return a bcrypt hash of the given plain-text password."""
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if plain matches the stored bcrypt hash."""
    return _pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def create_access_token(user_id: int, role: str) -> str:
    """Create a signed JWT for the given user."""
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": str(user_id),
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=ALGORITHM)


def _decode_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session. Please log in again.",
        ) from exc


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

async def get_current_user(
    ll_token: Annotated[str | None, Cookie()] = None,
    session: AsyncSession = Depends(get_session),
) -> User:
    """Require an authenticated user. Raises 401 if token is missing or invalid."""
    if not ll_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    payload = _decode_token(ll_token)
    user_id = int(payload["sub"])

    result = await session.execute(
        select(User).where(User.id == user_id, User.is_active == True)  # noqa: E712
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found or deactivated.",
        )
    return user


async def get_optional_user(
    ll_token: Annotated[str | None, Cookie()] = None,
    session: AsyncSession = Depends(get_session),
) -> User | None:
    """Like get_current_user but returns None instead of 401 for public pages."""
    if not ll_token:
        return None
    try:
        return await get_current_user(ll_token, session)
    except HTTPException:
        return None
