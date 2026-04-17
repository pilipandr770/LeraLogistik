"""Authentication routes: register, login, logout.

Registration flow:
  GET  /auth/register  → choose role page (or role pre-selected via ?role=)
  POST /auth/register  → validate form, create User + Company, run verification,
                         set cookie, redirect to /onboarding

Login flow:
  GET  /auth/login     → login form
  POST /auth/login     → verify credentials, set cookie, redirect to /dashboard

Logout:
  POST /auth/logout    → delete cookie, redirect to /auth/login
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Company, User, UserRole
from app.db.session import get_session
from app.services.auth import COOKIE_NAME, create_access_token, hash_password, verify_password
from app.services.verification import VerificationService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])
templates = Jinja2Templates(directory="app/templates")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_VALID_ROLES: set[str] = {UserRole.SHIPPER, UserRole.CARRIER, UserRole.FORWARDER}


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, role: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        "auth/register.html",
        {"request": request, "prefill_role": role, "errors": []},
    )


@router.post("/register", response_class=HTMLResponse)
async def register_submit(
    request: Request,
    role: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    password: str = Form(...),
    password2: str = Form(...),
    company_name: str = Form(...),
    country: str = Form("UA"),
    edrpou: str = Form(""),
    vat_number: str = Form(""),
    vat_country: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    errors: list[str] = []

    role = role.strip()
    email = email.strip().lower()
    company_name = company_name.strip()
    edrpou = edrpou.strip()
    vat_number = vat_number.strip()
    vat_country = vat_country.strip().upper()
    country = country.strip().upper() or "UA"

    # --- Validation ---
    if role not in _VALID_ROLES:
        errors.append("Оберіть тип акаунту (Заказчик / Перевізник / Експедитор)")

    if not _EMAIL_RE.match(email):
        errors.append("Некоректний формат email")

    if len(password) < 8:
        errors.append("Пароль має бути не менше 8 символів")

    if password != password2:
        errors.append("Паролі не співпадають")

    if len(company_name) < 2:
        errors.append("Введіть назву компанії (мінімум 2 символи)")

    if edrpou and not re.fullmatch(r"\d{8,10}", edrpou):
        errors.append("ЄДРПОУ має містити 8–10 цифр")

    if errors:
        return templates.TemplateResponse(
            "auth/register.html",
            {
                "request": request,
                "errors": errors,
                "prefill_role": role,
                "form": {
                    "email": email,
                    "phone": phone,
                    "company_name": company_name,
                    "country": country,
                    "edrpou": edrpou,
                },
            },
            status_code=422,
        )

    # --- Email uniqueness ---
    existing_user = await session.execute(select(User).where(User.email == email))
    if existing_user.scalar_one_or_none():
        return templates.TemplateResponse(
            "auth/register.html",
            {
                "request": request,
                "errors": ["Цей email вже зареєстрований. Увійдіть або скористайтеся відновленням паролю."],
                "prefill_role": role,
                "form": {"email": email, "company_name": company_name, "country": country},
            },
            status_code=422,
        )

    # --- Build unique slug ---
    base_slug = _slugify(company_name)
    slug = base_slug
    suffix = 1
    while True:
        existing_slug = await session.execute(
            select(Company).where(Company.slug == slug)
        )
        if not existing_slug.scalar_one_or_none():
            break
        slug = f"{base_slug}-{suffix}"
        suffix += 1

    # --- Create Company ---
    company = Company(
        name=company_name,
        country=country,
        edrpou=edrpou or None,
        vat_number=vat_number or None,
        vat_country=vat_country or None,
        slug=slug,
    )
    session.add(company)
    await session.flush()  # get company.id

    # --- Create User ---
    user = User(
        email=email,
        phone=phone.strip() or None,
        password_hash=hash_password(password),
        role=role,
        company_id=company.id,
    )
    session.add(user)
    await session.flush()  # get user.id

    # --- Run automated verification (in same transaction) ---
    company.users = [user]
    svc = VerificationService(session)
    await svc.run_all(company)

    await session.commit()

    log.info(
        "New user registered: id=%s role=%s email=%s company=%r trust_score=%s",
        user.id, user.role, user.email, company.name, company.trust_score,
    )

    # --- Issue session cookie ---
    token = create_access_token(user.id, user.role)
    response = RedirectResponse(url="/onboarding", status_code=status.HTTP_303_SEE_OTHER)
    _set_auth_cookie(response, token)
    return response


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/dashboard") -> HTMLResponse:
    return templates.TemplateResponse(
        "auth/login.html",
        {"request": request, "next": next, "error": None},
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/dashboard"),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    email = email.strip().lower()

    result = await session.execute(
        select(User).where(User.email == email, User.is_active == True)  # noqa: E712
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "auth/login.html",
            {
                "request": request,
                "error": "Невірний email або пароль",
                "next": next,
                "email": email,
            },
            status_code=401,
        )

    # Update last login timestamp
    user.last_login_at = datetime.now(timezone.utc)
    await session.commit()

    # Defend against open redirect attacks
    if not next.startswith("/") or "//" in next:
        next = "/dashboard"

    token = create_access_token(user.id, user.role)
    response = RedirectResponse(url=next, status_code=status.HTTP_303_SEE_OTHER)
    _set_auth_cookie(response, token)
    return response


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

@router.post("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE_NAME, httponly=True, samesite="lax")
    return response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_auth_cookie(response: RedirectResponse, token: str) -> None:
    from app.config import get_settings
    settings = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=settings.is_production,  # HTTPS-only in prod, allows HTTP in dev
        samesite="lax",
        max_age=60 * 60 * 24 * 30,  # 30 days
    )


def _slugify(name: str) -> str:
    """Convert a company name to a URL-safe slug."""
    # Normalize unicode (handle Cyrillic transliteration via ASCII approximation)
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    # Lowercase, replace spaces/underscores with hyphens, strip non-alphanumeric
    slug = re.sub(r"[^\w\s-]", "", ascii_name.lower())
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug.strip("-")[:80] or "company"
