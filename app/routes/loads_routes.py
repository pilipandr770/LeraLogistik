"""Operational endpoints — manual triggers for the MVP.

Eventually these will be invoked by a background scheduler. For now, Lera
presses buttons on the dashboard to trigger each step.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import SearchFilter
from app.adapters.lardi import LardiAdapter
from app.agents.matcher import MatcherAgent
from app.agents.pricing import PricingAgent
from app.config import get_settings
from app.db.models import Load, Match, MatchFeedback, MatchStatus, Vehicle
from app.db.session import get_session
from app.services.ingestion import IngestionService
from app.services.auth import get_current_user
from app.db.models import User

router = APIRouter(prefix="/ops", tags=["operations"])
templates = Jinja2Templates(directory="app/templates")


@router.post("/lardi/health")
async def lardi_health() -> JSONResponse:
    """Check that our Lardi API token is alive and connected."""
    settings = get_settings()
    if not settings.lardi_api_token:
        raise HTTPException(400, "LARDI_API_TOKEN is not configured")

    async with LardiAdapter() as adapter:
        ok = await adapter.health_check()

    return JSONResponse({"ok": ok})


@router.post("/lardi/ingest/loads")
async def ingest_loads(
    session: AsyncSession = Depends(get_session),
    countries_from: str | None = None,
    countries_to: str | None = None,
) -> JSONResponse:
    """Pull fresh cargo proposals from Lardi-Trans.

    Query params:
        countries_from - comma-separated ISO-2 codes, e.g. 'UA,PL'
        countries_to   - same
    """
    flt = SearchFilter(
        countries_from=_parse_countries(countries_from),
        countries_to=_parse_countries(countries_to),
        date_from=datetime.now(timezone.utc) - timedelta(days=1),
        per_page=50,
    )
    async with LardiAdapter() as adapter:
        service = IngestionService(adapter, session)
        new = await service.ingest_loads(flt)

    return JSONResponse({"new_loads": new})


@router.post("/lardi/ingest/vehicles")
async def ingest_vehicles(
    session: AsyncSession = Depends(get_session),
    countries_from: str | None = None,
    countries_to: str | None = None,
) -> JSONResponse:
    """Pull fresh vehicle proposals from Lardi-Trans."""
    flt = SearchFilter(
        countries_from=_parse_countries(countries_from),
        countries_to=_parse_countries(countries_to),
        date_from=datetime.now(timezone.utc) - timedelta(days=1),
        per_page=50,
    )
    async with LardiAdapter() as adapter:
        service = IngestionService(adapter, session)
        new = await service.ingest_vehicles(flt)

    return JSONResponse({"new_vehicles": new})


@router.post("/matcher/run")
async def run_matcher(
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Run the AI Matcher agent over all NEW loads."""
    settings = get_settings()
    if not settings.agent_matcher_enabled:
        raise HTTPException(400, "Matcher agent is disabled via feature flag")
    if not settings.anthropic_api_key:
        raise HTTPException(400, "ANTHROPIC_API_KEY is not configured")

    agent = MatcherAgent(session)
    created = await agent.match_all_new_loads()
    return JSONResponse({"matches_created": created})


@router.post("/pricing/run")
async def run_pricing(
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Run the Pricing Agent over all unpriced matches."""
    settings = get_settings()
    if not settings.agent_pricing_enabled:
        raise HTTPException(400, "Pricing agent is disabled via feature flag")
    if not settings.anthropic_api_key:
        raise HTTPException(400, "ANTHROPIC_API_KEY is not configured")

    agent = PricingAgent(session)
    priced = await agent.price_all_unpriced_matches()
    return JSONResponse({"matches_priced": priced})


@router.get("/loads/{load_id}", response_class=HTMLResponse)
async def load_detail(
    load_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """HTML view of a single load and its matches."""
    load = await session.get(Load, load_id)
    if not load:
        raise HTTPException(404, "Load not found")

    matches = (await session.scalars(
        select(Match).where(Match.load_id == load.id).order_by(Match.score.desc())
    )).all()

    # Prefetch vehicles for the matches
    vehicles_by_id: dict[int, Vehicle] = {}
    if matches:
        vehicle_ids = [m.vehicle_id for m in matches]
        result = await session.scalars(select(Vehicle).where(Vehicle.id.in_(vehicle_ids)))
        vehicles_by_id = {v.id: v for v in result.all()}

    return templates.TemplateResponse(
     request,
     "load_detail.html",
     {
            "load": load,
            "matches": matches,
            "vehicles_by_id": vehicles_by_id,
     },
 )


@router.post("/matches/{match_id}/reject")
async def reject_match(
    match_id: int,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Mark an AI-proposed match as rejected by the human."""
    match = await session.get(Match, match_id)
    if not match:
        raise HTTPException(404, "Match not found")
    match.status = MatchStatus.REJECTED
    await session.commit()
    return JSONResponse({"ok": True})


@router.post("/matches/{match_id}/feedback")
async def match_feedback(
    match_id: int,
    verdict: str,   # query param: good | bad | unclear
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    """Record operator's thumbs-up / thumbs-down on an AI match.

    Uses INSERT … ON CONFLICT DO UPDATE so the user can change their mind.
    """
    if verdict not in ("good", "bad", "unclear"):
        raise HTTPException(400, "verdict must be good | bad | unclear")

    match = await session.get(Match, match_id)
    if not match:
        raise HTTPException(404, "Match not found")

    # Upsert: update if the row already exists
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    stmt = pg_insert(MatchFeedback).values(
        match_id=match_id,
        operator_id=current_user.id,
        verdict=verdict,
    ).on_conflict_do_update(
        constraint="uq_feedback_match_operator",
        set_={"verdict": verdict},
    )
    await session.execute(stmt)

    # Mark match as reviewed if it was still "proposed"
    if match.status == MatchStatus.PROPOSED:
        match.status = MatchStatus.REVIEWED

    await session.commit()
    return JSONResponse({"ok": True, "verdict": verdict})


# ---------- helpers ----------

def _parse_countries(value: str | None) -> list[str]:
    if not value:
        return []
    return [c.strip().upper() for c in value.split(",") if c.strip()]
