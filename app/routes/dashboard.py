"""Dashboard — the main HTML view of the application.

This renders a server-side template using Jinja2. Interactivity (live
updates, partial reloads) is added via HTMX attributes in the templates,
not via a SPA framework.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Company,
    Deal,
    Load,
    LoadStatus,
    Match,
    MatchFeedback,
    MatchStatus,
    User,
    Vehicle,
    VehicleStatus,
)
from app.db.session import get_session
from app.services.auth import get_optional_user

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/dashboard", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User | None = Depends(get_optional_user),
) -> HTMLResponse:
    """Main dashboard — summary counters and latest loads/vehicles/matches."""
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

    latest_loads = (await session.scalars(
        select(Load).order_by(Load.created_at.desc()).limit(10)
    )).all()

    latest_vehicles = (await session.scalars(
        select(Vehicle).order_by(Vehicle.created_at.desc()).limit(10)
    )).all()

    top_matches = (await session.scalars(
        select(Match)
        .where(Match.status.in_([MatchStatus.PROPOSED, MatchStatus.REVIEWED]))
        .order_by(Match.score.desc())
        .limit(10)
    )).all()

    # Fetch this operator's feedback for visible matches
    match_ids = [m.id for m in top_matches]
    feedback_by_match: dict[int, str] = {}
    if match_ids and current_user:
        fb_rows = (await session.scalars(
            select(MatchFeedback).where(
                MatchFeedback.match_id.in_(match_ids),
                MatchFeedback.operator_id == current_user.id,
            )
        )).all()
        feedback_by_match = {fb.match_id: fb.verdict for fb in fb_rows}

    # Attach feedback_verdict as a transient attribute so the template can access it
    for m in top_matches:
        m.feedback_verdict = feedback_by_match.get(m.id, "")  # type: ignore[attr-defined]

    # Eagerly load company for navbar profile link
    company = None
    if current_user and current_user.company_id:
        result = await session.execute(
            select(Company).where(Company.id == current_user.company_id)
        )
        company = result.scalar_one_or_none()
        if current_user and company:
            current_user.company = company  # type: ignore[attr-defined]

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "current_user": current_user,
            "counters": {
                "loads_new": loads_new or 0,
                "loads_matched": loads_matched or 0,
                "vehicles_available": vehicles_available or 0,
                "matches_open": matches_open or 0,
                "deals_total": deals_total or 0,
            },
            "latest_loads": latest_loads,
            "latest_vehicles": latest_vehicles,
            "top_matches": top_matches,
        },
    )


@router.get("/partials/counters", response_class=HTMLResponse)
async def counters_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """HTMX partial — just the counter cards, polled every few seconds."""
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
        "components/counters.html",
        {
            "request": request,
            "counters": {
                "loads_new": loads_new or 0,
                "loads_matched": loads_matched or 0,
                "vehicles_available": vehicles_available or 0,
                "matches_open": matches_open or 0,
                "deals_total": deals_total or 0,
            },
        },
    )
