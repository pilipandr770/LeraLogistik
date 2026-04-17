"""Onboarding wizard and live verification lookup endpoints.

After registration the user lands here to complete their profile.
All heavy HTMX interactions (live ЄДРПОУ/VIES lookup, step navigation) go here.

Endpoints:
  GET  /onboarding               → onboarding index (role-aware wizard)
  GET  /onboarding/lookup/edrpou → live ЄДРПОУ lookup (HTMX partial)
  GET  /onboarding/lookup/vat    → live EU VAT VIES lookup (HTMX partial)
  POST /onboarding/complete      → save profile data, redirect to /dashboard
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.edrpou import EdrpouAdapter
from app.adapters.vies import ViesAdapter
from app.config import get_settings
from app.db.models import Company, User
from app.db.session import get_session
from app.services.auth import get_current_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])
templates = Jinja2Templates(directory="app/templates")


# ---------------------------------------------------------------------------
# Main onboarding page
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
async def onboarding_index(
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    company: Company | None = None
    if user.company_id:
        result = await session.execute(
            select(Company).where(Company.id == user.company_id)
        )
        company = result.scalar_one_or_none()

    return templates.TemplateResponse(
     request,
     "onboarding/index.html",
     {
            "user": user,
            "company": company,
     },
 )


# ---------------------------------------------------------------------------
# HTMX live lookups — called as user types, return HTML or JSON partials
# ---------------------------------------------------------------------------

@router.get("/lookup/edrpou", response_class=HTMLResponse)
async def lookup_edrpou(request: Request, code: str = "") -> HTMLResponse:
    """Live ЄДРПОУ lookup — returns an HTML partial rendered by HTMX."""
    settings = get_settings()
    code = code.strip()

    if not code:
        return HTMLResponse("")

    if not code.isdigit() or not (8 <= len(code) <= 10):
        return templates.TemplateResponse(
            request,
            "onboarding/_lookup_result.html",
            {"error": "ЄДРПОУ має бути 8–10 цифр"},
        )

    async with EdrpouAdapter(api_key=settings.opendatabot_api_key) as adapter:
        result = await adapter.lookup(code)

    if result is None:
        return templates.TemplateResponse(
     request,
     "onboarding/_lookup_result.html",
     {
                "warning": "Компанія не знайдена в реєстрі або API тимчасово недоступний. "
                           "Ваш акаунт буде перевірено вручну.",
     },
 )

    if not result.is_active:
        return templates.TemplateResponse(
     request,
     "onboarding/_lookup_result.html",
     {
                "warning": f"Увага: компанія «{result.legal_name}» неактивна в реєстрі "
                           f"(ліквідація або банкрутство). Будь ласка, перевірте ЄДРПОУ.",
                "result": result,
     },
 )

    return templates.TemplateResponse(
     request,
     "onboarding/_lookup_result.html",
     {
            "success": True,
            "result": result,
     },
 )


@router.get("/lookup/vat", response_class=HTMLResponse)
async def lookup_vat(request: Request, country: str = "", number: str = "") -> HTMLResponse:
    """Live EU VAT VIES lookup — returns an HTML partial rendered by HTMX."""
    country = country.strip().upper()
    number = number.strip()

    if not country or not number:
        return HTMLResponse("")

    async with ViesAdapter() as adapter:
        result = await adapter.validate(country, number)

    if result is None:
        return templates.TemplateResponse(
     request,
     "onboarding/_lookup_result.html",
     {
                "warning": "VIES API недоступний або країна не є членом ЄС. "
                           "Перевірка буде виконана вручну.",
     },
 )

    if not result.is_valid:
        return templates.TemplateResponse(
     request,
     "onboarding/_lookup_result.html",
     {
                "warning": f"VAT номер {country}{number} не знайдено або неактивний в системі VIES ЄС.",
     },
 )

    return templates.TemplateResponse(
     request,
     "onboarding/_lookup_result.html",
     {
            "success": True,
            "vies": result,
     },
 )


# ---------------------------------------------------------------------------
# Save profile (onboarding complete)
# ---------------------------------------------------------------------------

@router.post("/complete", response_class=RedirectResponse)
async def onboarding_complete(
    request: Request,
    tagline: str = Form(""),
    description: str = Form(""),
    phone: str = Form(""),
    website: str = Form(""),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if user.company_id:
        result = await session.execute(
            select(Company).where(Company.id == user.company_id)
        )
        company = result.scalar_one_or_none()
        if company:
            company.tagline = tagline.strip() or company.tagline
            company.description = description.strip() or company.description
            company.phone = phone.strip() or company.phone
            company.website = website.strip() or company.website
            await session.commit()

    return RedirectResponse(url="/dashboard", status_code=303)
