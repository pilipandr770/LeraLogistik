"""Carrier/company search directory — public page, no auth required.

Endpoints:
  GET /search          — redirect to /search/carriers
  GET /search/carriers — searchable carrier directory
  GET /search/shippers — searchable shipper directory (future)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Company, User, UserRole
from app.db.session import get_session
from app.services.auth import get_optional_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["search"])
templates = Jinja2Templates(directory="app/templates")

_PAGE_SIZE = 20


@router.get("", response_class=RedirectResponse)
async def search_index() -> RedirectResponse:
    return RedirectResponse(url="/search/carriers")


@router.get("/carriers", response_class=HTMLResponse)
async def search_carriers(
    request: Request,
    q: str = Query(default="", max_length=200),
    country: str = Query(default=""),
    verified_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    session: AsyncSession = Depends(get_session),
    current_user: User | None = Depends(get_optional_user),
) -> HTMLResponse:
    """Public carrier directory with filtering.

    Returns companies whose users have role=carrier or role=forwarder.
    """
    q = q.strip()
    country = country.strip().upper()

    # Subquery: get company IDs that have at least one carrier/forwarder user
    carrier_company_ids = select(User.company_id).where(
        User.role.in_([UserRole.CARRIER, UserRole.FORWARDER]),
        User.company_id.isnot(None),
    ).scalar_subquery()

    stmt = select(Company).where(Company.id.in_(carrier_company_ids))

    if q:
        stmt = stmt.where(
            or_(
                func.lower(Company.name).contains(q.lower()),
                func.lower(Company.tagline).contains(q.lower()),
                func.lower(Company.description).contains(q.lower()),
            )
        )

    if country:
        stmt = stmt.where(Company.country == country)

    if verified_only:
        stmt = stmt.where(Company.is_verified == True)  # noqa: E712

    # Total count
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))

    # Paginate, sort verified first then by trust_score
    stmt = (
        stmt
        .order_by(Company.is_verified.desc(), Company.trust_score.desc(), Company.name)
        .offset((page - 1) * _PAGE_SIZE)
        .limit(_PAGE_SIZE)
    )
    companies = (await session.scalars(stmt)).all()

    total_pages = max(1, ((total or 0) + _PAGE_SIZE - 1) // _PAGE_SIZE)

    return templates.TemplateResponse(
        "search/carriers.html",
        {
            "request": request,
            "current_user": current_user,
            "companies": companies,
            "q": q,
            "country": country,
            "verified_only": verified_only,
            "page": page,
            "total": total or 0,
            "total_pages": total_pages,
        },
    )
