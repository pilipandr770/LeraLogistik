"""Admin dashboard — full platform visibility for admins only.

Endpoints
---------
GET /admin/dashboard     Overview: counters, recent activity
GET /admin/users         Full user list
GET /admin/companies     All companies with verification status
GET /admin/deals         All deals with status timeline

Access: requires UserRole.ADMIN (enforced via require_admin dependency).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Company,
    Deal,
    DealStatus,
    Load,
    LoadStatus,
    Match,
    MatchStatus,
    User,
    UserRole,
    Vehicle,
    VerificationCheck,
)
from app.db.session import get_session
from app.services.access import require_admin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> HTMLResponse:
    """Platform-wide overview for admins."""

    # ── User counts ──────────────────────────────────────────────────────
    total_users = await session.scalar(select(func.count()).select_from(User))
    users_by_role = {
        role: await session.scalar(
            select(func.count()).select_from(User).where(User.role == role)
        )
        for role in (UserRole.SHIPPER, UserRole.CARRIER, UserRole.FORWARDER)
    }

    # ── Companies ────────────────────────────────────────────────────────
    total_companies = await session.scalar(select(func.count()).select_from(Company))
    verified_companies = await session.scalar(
        select(func.count()).select_from(Company).where(Company.is_verified == True)  # noqa: E712
    )
    pending_verifications = await session.scalar(
        select(func.count()).select_from(VerificationCheck)
        .where(VerificationCheck.status == "pending")
    )

    # ── Loads ────────────────────────────────────────────────────────────
    loads_by_status = {
        s: await session.scalar(
            select(func.count()).select_from(Load).where(Load.status == s)
        )
        for s in (LoadStatus.NEW, LoadStatus.MATCHED, LoadStatus.BOOKED, LoadStatus.NEGOTIATING)
    }

    # ── Deals ────────────────────────────────────────────────────────────
    active_deals = await session.scalar(
        select(func.count()).select_from(Deal)
        .where(Deal.status.in_([DealStatus.LOADED, DealStatus.IN_TRANSIT]))
    )
    total_deals = await session.scalar(select(func.count()).select_from(Deal))

    # ── AI matches ───────────────────────────────────────────────────────
    matches_pending = await session.scalar(
        select(func.count()).select_from(Match)
        .where(Match.status == MatchStatus.PROPOSED)
    )

    # ── Recent registrations ─────────────────────────────────────────────
    recent_users = (await session.scalars(
        select(User).order_by(User.created_at.desc()).limit(10)
    )).all()

    # ── Active deals list ─────────────────────────────────────────────────
    active_deals_list = (await session.scalars(
        select(Deal)
        .where(Deal.status.in_([DealStatus.LOADED, DealStatus.IN_TRANSIT]))
        .order_by(Deal.created_at.desc())
        .limit(20)
    )).all()

    # ── Recent loads ──────────────────────────────────────────────────────
    recent_loads = (await session.scalars(
        select(Load).order_by(Load.created_at.desc()).limit(10)
    )).all()

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "current_user": current_user,
            "total_users": total_users or 0,
            "users_by_role": users_by_role,
            "total_companies": total_companies or 0,
            "verified_companies": verified_companies or 0,
            "pending_verifications": pending_verifications or 0,
            "loads_by_status": loads_by_status,
            "active_deals": active_deals or 0,
            "total_deals": total_deals or 0,
            "matches_pending": matches_pending or 0,
            "recent_users": recent_users,
            "active_deals_list": active_deals_list,
            "recent_loads": recent_loads,
        },
    )


@router.get("/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> HTMLResponse:
    users = (await session.scalars(
        select(User).order_by(User.created_at.desc())
    )).all()
    return templates.TemplateResponse(
        "admin/users.html",
        {"request": request, "current_user": current_user, "users": users},
    )


@router.get("/companies", response_class=HTMLResponse)
async def admin_companies(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> HTMLResponse:
    companies = (await session.scalars(
        select(Company).order_by(Company.created_at.desc())
    )).all()
    return templates.TemplateResponse(
        "admin/companies.html",
        {"request": request, "current_user": current_user, "companies": companies},
    )
