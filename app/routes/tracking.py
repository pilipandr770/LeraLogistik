"""Shipment tracking — shipper watches their cargo in real time.

Endpoints
---------
GET  /track/{deal_id}           Full tracking page (Leaflet map)
GET  /track/{deal_id}/position  HTMX partial — latest position card (auto-polls)

Access rule (enforced via AccessControl.can_track_deal):
  • Shipper/forwarder: only THEIR load, only while status in {loaded, in_transit}
  • Carrier: their own deal at any stage
  • Admin: any deal at any time
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Deal, Load, User, Vehicle, VehiclePosition
from app.db.session import get_session
from app.services.access import AccessControl, assert_can_track
from app.services.auth import get_current_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/track", tags=["tracking"])
templates = Jinja2Templates(directory="app/templates")


async def _load_deal(deal_id: int, session: AsyncSession) -> Deal:
    """Load Deal with deal.load and deal.vehicle eager-loaded. Raises 404 if missing."""
    result = await session.execute(
        select(Deal)
        .where(Deal.id == deal_id)
        .options(
            selectinload(Deal.load),   # type: ignore[attr-defined]
            selectinload(Deal.vehicle),  # type: ignore[attr-defined]
        )
    )
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail="Угоду не знайдено.")
    return deal


async def _latest_position(vehicle_id: int, session: AsyncSession) -> VehiclePosition | None:
    result = await session.execute(
        select(VehiclePosition)
        .where(VehiclePosition.vehicle_id == vehicle_id)
        .order_by(VehiclePosition.recorded_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


@router.get("/{deal_id}", response_class=HTMLResponse)
async def tracking_page(
    deal_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    deal = await _load_deal(deal_id, session)
    assert_can_track(current_user, deal)

    position = None
    if deal.vehicle_id:
        position = await _latest_position(deal.vehicle_id, session)

    return templates.TemplateResponse(
     request,
     "tracking/deal.html",
     {
            "current_user": current_user,
            "deal": deal,
            "position": position,
     },
 )


@router.get("/{deal_id}/position", response_class=HTMLResponse)
async def tracking_position_partial(
    deal_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    """HTMX partial: refreshed every 30 s via hx-trigger='every 30s'."""
    deal = await _load_deal(deal_id, session)
    assert_can_track(current_user, deal)

    position = None
    if deal.vehicle_id:
        position = await _latest_position(deal.vehicle_id, session)

    return templates.TemplateResponse(
     request,
     "tracking/_position_card.html",
     {
            "deal": deal,
            "position": position,
     },
 )
