"""Dashboard — role-based views for authenticated users; landing page for guests."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    Deal,
    DealStatus,
    Load,
    LoadStatus,
    Match,
    MatchStatus,
    User,
    UserRole,
    Vehicle,
    VehicleStatus,
    Company,
)
from app.db.session import get_session
from app.services.auth import get_optional_user

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")


# ---------------------------------------------------------------------------
# Root — landing page for guests, role dashboard for logged-in users
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def root(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User | None = Depends(get_optional_user),
) -> HTMLResponse:
    if not current_user:
        return templates.TemplateResponse(request, "landing.html", {})
    return await _dashboard_response(request, session, current_user)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User | None = Depends(get_optional_user),
) -> HTMLResponse:
    if not current_user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/auth/login", status_code=302)
    return await _dashboard_response(request, session, current_user)


async def _dashboard_response(
    request: Request,
    session: AsyncSession,
    current_user: User,
) -> HTMLResponse:
    """Build the appropriate dashboard for the given user's role."""
    # Re-fetch current_user with company eagerly loaded
    result = await session.execute(
        select(User)
        .options(selectinload(User.company))
        .where(User.id == current_user.id)
    )
    current_user = result.scalar_one()

    if current_user.role == UserRole.ADMIN:
        return await _admin_dashboard(request, session, current_user)
    elif current_user.role == UserRole.CARRIER:
        return await _carrier_dashboard(request, session, current_user)
    else:
        return await _shipper_dashboard(request, session, current_user)


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------

async def _admin_dashboard(request: Request, session: AsyncSession, current_user: User) -> HTMLResponse:
    loads_new = await session.scalar(
        select(func.count()).select_from(Load).where(Load.status == LoadStatus.NEW)
    ) or 0
    vehicles_available = await session.scalar(
        select(func.count()).select_from(Vehicle).where(Vehicle.status == VehicleStatus.AVAILABLE)
    ) or 0
    matches_open = await session.scalar(
        select(func.count()).select_from(Match).where(Match.status == MatchStatus.PROPOSED)
    ) or 0
    deals_total = await session.scalar(select(func.count()).select_from(Deal)) or 0
    users_total = await session.scalar(select(func.count()).select_from(User)) or 0

    latest_loads = (await session.scalars(
        select(Load).order_by(Load.created_at.desc()).limit(8)
    )).all()

    top_matches = (await session.scalars(
        select(Match)
        .options(selectinload(Match.load), selectinload(Match.vehicle))
        .where(Match.status.in_([MatchStatus.PROPOSED, MatchStatus.REVIEWED]))
        .order_by(Match.score.desc())
        .limit(8)
    )).all()

    latest_users = (await session.scalars(
        select(User)
        .options(selectinload(User.company))
        .order_by(User.created_at.desc())
        .limit(10)
    )).all()

    return templates.TemplateResponse(
        request,
        "dashboard_admin.html",
        {
            "current_user": current_user,
            "stats": {
                "loads_new": loads_new,
                "vehicles_available": vehicles_available,
                "matches_open": matches_open,
                "deals_total": deals_total,
                "users_total": users_total,
            },
            "latest_loads": latest_loads,
            "top_matches": top_matches,
            "latest_users": latest_users,
        },
    )


# ---------------------------------------------------------------------------
# Carrier dashboard
# ---------------------------------------------------------------------------

async def _carrier_dashboard(request: Request, session: AsyncSession, current_user: User) -> HTMLResponse:
    company_id = current_user.company_id

    my_vehicles = (await session.scalars(
        select(Vehicle)
        .where(Vehicle.company_id == company_id)
        .order_by(Vehicle.created_at.desc())
        .limit(10)
    )).all() if company_id else []

    vehicle_ids = [v.id for v in my_vehicles]

    my_matches = []
    if vehicle_ids:
        my_matches = (await session.scalars(
            select(Match)
            .options(selectinload(Match.load))
            .where(
                Match.vehicle_id.in_(vehicle_ids),
                Match.status.in_([MatchStatus.PROPOSED, MatchStatus.REVIEWED]),
            )
            .order_by(Match.score.desc())
            .limit(8)
        )).all()

    active_deals = await session.scalar(
        select(func.count()).select_from(Deal).where(
            Deal.status.in_([DealStatus.LOADED, DealStatus.IN_TRANSIT])
        )
    ) or 0

    return templates.TemplateResponse(
        request,
        "dashboard_carrier.html",
        {
            "current_user": current_user,
            "counters": {
                "my_vehicles": len([v for v in my_vehicles if v.status == VehicleStatus.AVAILABLE]),
                "my_matches": len(my_matches),
                "active_deals": active_deals,
            },
            "my_vehicles": my_vehicles,
            "my_matches": my_matches,
        },
    )


# ---------------------------------------------------------------------------
# Shipper / Forwarder dashboard
# ---------------------------------------------------------------------------

async def _shipper_dashboard(request: Request, session: AsyncSession, current_user: User) -> HTMLResponse:
    my_loads = (await session.scalars(
        select(Load)
        .where(Load.posted_by_user_id == current_user.id)
        .order_by(Load.created_at.desc())
        .limit(8)
    )).all()

    my_active = sum(1 for lo in my_loads if lo.status in (LoadStatus.NEW, LoadStatus.MATCHED))
    my_matches_count = await session.scalar(
        select(func.count()).select_from(Match)
        .join(Load, Match.load_id == Load.id)
        .where(
            Load.posted_by_user_id == current_user.id,
            Match.status.in_([MatchStatus.PROPOSED, MatchStatus.REVIEWED]),
        )
    ) or 0
    my_deals_count = await session.scalar(
        select(func.count()).select_from(Deal)
        .join(Load, Deal.load_id == Load.id)
        .where(
            Load.posted_by_user_id == current_user.id,
            Deal.status.in_([DealStatus.LOADED, DealStatus.IN_TRANSIT]),
        )
    ) or 0
    total_done = await session.scalar(
        select(func.count()).select_from(Deal)
        .join(Load, Deal.load_id == Load.id)
        .where(
            Load.posted_by_user_id == current_user.id,
            Deal.status == DealStatus.DELIVERED,
        )
    ) or 0

    available_vehicles = (await session.scalars(
        select(Vehicle)
        .options(selectinload(Vehicle.company))
        .where(Vehicle.status == VehicleStatus.AVAILABLE)
        .order_by(Vehicle.created_at.desc())
        .limit(6)
    )).all()

    return templates.TemplateResponse(
        request,
        "dashboard_shipper.html",
        {
            "current_user": current_user,
            "counters": {
                "my_active": my_active,
                "my_matches": my_matches_count,
                "my_deals": my_deals_count,
                "total_done": total_done,
            },
            "my_loads": my_loads,
            "available_vehicles": available_vehicles,
        },
    )


# ---------------------------------------------------------------------------
# HTMX partial — counters (kept for backward compat)
# ---------------------------------------------------------------------------

@router.get("/partials/counters", response_class=HTMLResponse)
async def counters_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    loads_new = await session.scalar(
        select(func.count()).select_from(Load).where(Load.status == LoadStatus.NEW)
    )
    loads_matched = await session.scalar(
        select(func.count()).select_from(Load).where(Load.status == LoadStatus.MATCHED)
    )
    vehicles_available = await session.scalar(
        select(func.count()).select_from(Vehicle).where(Vehicle.status == VehicleStatus.AVAILABLE)
    )
    matches_open = await session.scalar(
        select(func.count()).select_from(Match).where(Match.status == MatchStatus.PROPOSED)
    )
    deals_total = await session.scalar(select(func.count()).select_from(Deal))

    return templates.TemplateResponse(
        request,
        "components/counters.html",
        {
            "counters": {
                "loads_new": loads_new or 0,
                "loads_matched": loads_matched or 0,
                "vehicles_available": vehicles_available or 0,
                "matches_open": matches_open or 0,
                "deals_total": deals_total or 0,
            },
        },
    )




