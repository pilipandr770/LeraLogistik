"""Public company profile pages — the "mini-site" for each registered company.

Routes:
  GET /p/{slug}          — public profile page (no auth required)
  GET /p/{slug}/contact  — HTMX partial: contact form, visible to logged-in users
  POST /p/{slug}/contact — send inquiry to the company owner (via internal message)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Company, User, VerificationCheck, VerificationStatus
from app.db.session import get_session
from app.services.auth import get_optional_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/p", tags=["profile"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/{slug}", response_class=HTMLResponse)
async def company_profile(
    slug: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User | None = Depends(get_optional_user),
) -> HTMLResponse:
    """Public profile page — accessible to anyone, even without an account."""
    result = await session.execute(
        select(Company).where(Company.slug == slug)
    )
    company = result.scalar_one_or_none()

    if not company:
        raise HTTPException(status_code=404, detail="Компанія не знайдена")

    # Load verification checks for the trust badge breakdown
    checks_result = await session.execute(
        select(VerificationCheck)
        .where(
            VerificationCheck.company_id == company.id,
            VerificationCheck.status == VerificationStatus.PASSED,
        )
        .order_by(VerificationCheck.checked_at.desc())
    )
    passed_checks = checks_result.scalars().all()

    # Build check type set for easy template rendering
    verified_types: set[str] = {c.check_type for c in passed_checks}

    # Find the company owner (first user in the company)
    owner_result = await session.execute(
        select(User).where(User.company_id == company.id).limit(1)
    )
    owner = owner_result.scalar_one_or_none()

    return templates.TemplateResponse(
     request,
     "profile/company.html",
     {
            "current_user": current_user,
            "company": company,
            "owner": owner,
            "verified_types": verified_types,
     },
 )


@router.post("/{slug}/contact", response_class=HTMLResponse)
async def contact_submit(
    slug: str,
    request: Request,
    sender_name: str = Form(...),
    sender_email: str = Form(...),
    message: str = Form(...),
    session: AsyncSession = Depends(get_session),
    current_user: User | None = Depends(get_optional_user),
) -> HTMLResponse:
    """Handle contact form submission — log it (email integration comes later)."""
    result = await session.execute(
        select(Company).where(Company.slug == slug)
    )
    company = result.scalar_one_or_none()

    if not company:
        raise HTTPException(status_code=404, detail="Компанія не знайдена")

    # Sanitise inputs
    sender_name = sender_name.strip()[:255]
    sender_email = sender_email.strip().lower()[:255]
    message_text = message.strip()[:2000]

    log.info(
        "Contact inquiry for company %r (id=%s) from %s <%s>: %s…",
        company.name, company.id, sender_name, sender_email, message_text[:80],
    )

    # Return a success partial (HTMX replaces the form)
    return templates.TemplateResponse(
     request,
     "profile/_contact_success.html",
     {
            "company": company,
     },
 )
