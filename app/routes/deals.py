"""Deal lifecycle routes.

Endpoints
---------
GET  /deals                     — list deals for current user (role-aware)
GET  /deals/{deal_id}           — deal detail page
POST /deals/from-match/{match_id} — admin creates a deal from an AI match
POST /deals/{deal_id}/status    — update deal status (role-gated transitions)

Status transitions allowed:
  Admin:      any → any
  Carrier:    booked → loaded → in_transit → delivered
  Shipper:    delivered → (view only, cannot advance status)
  Forwarder:  same as shipper

The shipper tracking window opens automatically when status reaches
'loaded' and closes when it reaches 'delivered'.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    Carrier,
    Deal,
    DealStatus,
    Load,
    LoadStatus,
    Match,
    MatchStatus,
    PriceSample,
    User,
    UserRole,
    Vehicle,
)
from app.db.session import get_session
from app.services.access import AccessControl, require_admin
from app.services.auth import get_current_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/deals", tags=["deals"])
templates = Jinja2Templates(directory="app/templates")


# ---------------------------------------------------------------------------
# Allowed status transitions per role
# ---------------------------------------------------------------------------

_CARRIER_TRANSITIONS: dict[str, str] = {
    DealStatus.BOOKED: DealStatus.LOADED,
    DealStatus.LOADED: DealStatus.IN_TRANSIT,
    DealStatus.IN_TRANSIT: DealStatus.DELIVERED,
}

_ADMIN_TRANSITIONS: dict[str, list[str]] = {
    DealStatus.BOOKED: [DealStatus.LOADED, DealStatus.CANCELLED],
    DealStatus.LOADED: [DealStatus.IN_TRANSIT, DealStatus.CANCELLED],
    DealStatus.IN_TRANSIT: [DealStatus.DELIVERED, DealStatus.CANCELLED],
    DealStatus.DELIVERED: [DealStatus.INVOICED],
    DealStatus.INVOICED: [DealStatus.PAID],
}


async def _get_deal_with_relations(deal_id: int, session: AsyncSession) -> Deal:
    result = await session.execute(
        select(Deal)
        .where(Deal.id == deal_id)
        .options(
            selectinload(Deal.load),
            selectinload(Deal.vehicle),
            selectinload(Deal.carrier),
        )
    )
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail="Угоду не знайдено.")
    return deal


# ---------------------------------------------------------------------------
# GET /deals  —  list deals for current user
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
async def deals_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    stmt = select(Deal).options(
        selectinload(Deal.load),
        selectinload(Deal.vehicle),
        selectinload(Deal.carrier),
    )

    if current_user.role == UserRole.ADMIN:
        pass  # see all
    elif current_user.role in (UserRole.SHIPPER, UserRole.FORWARDER):
        # Only deals where they own the load
        owned_load_ids = select(Load.id).where(
            Load.posted_by_user_id == current_user.id
        ).scalar_subquery()
        stmt = stmt.where(Deal.load_id.in_(owned_load_ids))
    elif current_user.role == UserRole.CARRIER:
        # Deals where the carrier company is linked via Vehicle → Carrier
        # For now: show all deals (carrier identifies via their company's vehicles)
        # TODO: link Deal.carrier_id → Company when platform carriers are fully integrated
        pass

    stmt = stmt.order_by(Deal.created_at.desc()).limit(50)
    deals = (await session.scalars(stmt)).all()

    return templates.TemplateResponse(
        "deals/list.html",
        {
            "request": request,
            "current_user": current_user,
            "deals": deals,
            "DealStatus": DealStatus,
        },
    )


# ---------------------------------------------------------------------------
# GET /deals/{deal_id}  —  deal detail
# ---------------------------------------------------------------------------

@router.get("/{deal_id}", response_class=HTMLResponse)
async def deal_detail(
    deal_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    deal = await _get_deal_with_relations(deal_id, session)

    # Access check
    if not AccessControl.can_view_deal(current_user, deal):
        raise HTTPException(status_code=403, detail="Доступ заборонено.")

    # Determine which status transitions are available for this user
    next_statuses: list[str] = []
    if current_user.role == UserRole.ADMIN:
        next_statuses = _ADMIN_TRANSITIONS.get(deal.status, [])
    elif current_user.role == UserRole.CARRIER:
        nxt = _CARRIER_TRANSITIONS.get(deal.status)
        if nxt:
            next_statuses = [nxt]

    can_track = AccessControl.can_track_deal(current_user, deal)

    return templates.TemplateResponse(
        "deals/detail.html",
        {
            "request": request,
            "current_user": current_user,
            "deal": deal,
            "next_statuses": next_statuses,
            "can_track": can_track,
            "DealStatus": DealStatus,
        },
    )


# ---------------------------------------------------------------------------
# POST /deals/from-match/{match_id}  —  admin converts a match into a deal
# ---------------------------------------------------------------------------

@router.post("/from-match/{match_id}", response_class=RedirectResponse)
async def create_deal_from_match(
    match_id: int,
    price_amount: Decimal = Form(...),
    price_currency: str = Form(default="UAH"),
    notes: str = Form(default=""),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> RedirectResponse:
    """Convert an AI-proposed match into a booked deal."""
    # Load match with its load and vehicle
    result = await session.execute(
        select(Match)
        .where(Match.id == match_id)
        .options(
            selectinload(Match.load),   # type: ignore[attr-defined]
            selectinload(Match.vehicle),  # type: ignore[attr-defined]
        )
    )
    match = result.scalar_one_or_none()
    if not match:
        raise HTTPException(status_code=404, detail="Матч не знайдено.")

    if match.status == MatchStatus.REJECTED:
        raise HTTPException(status_code=400, detail="Цей матч відхилено.")

    # Ensure carrier exists (vehicle must have carrier_id)
    if not match.vehicle or not match.vehicle.carrier_id:
        raise HTTPException(
            status_code=400,
            detail="Транспортний засіб не прив'язаний до перевізника.",
        )

    # Create the deal
    deal = Deal(
        load_id=match.load_id,
        vehicle_id=match.vehicle_id,
        carrier_id=match.vehicle.carrier_id,
        negotiation_id=None,
        price_amount=price_amount,
        price_currency=price_currency.upper(),
        status=DealStatus.BOOKED,
        notes=notes or None,
    )
    session.add(deal)

    # Advance match and load statuses
    match.status = MatchStatus.PROMOTED
    if match.load:
        match.load.status = LoadStatus.BOOKED

    await session.commit()
    await session.refresh(deal)

    log.info(
        "Admin %s created deal #%d from match #%d (load=%d, vehicle=%d)",
        current_user.email, deal.id, match_id, deal.load_id, deal.vehicle_id,
    )
    return RedirectResponse(url=f"/deals/{deal.id}", status_code=303)


# ---------------------------------------------------------------------------
# POST /deals/{deal_id}/status  —  advance status
# ---------------------------------------------------------------------------

_STATUS_LABELS = {
    DealStatus.LOADED: "Завантажено",
    DealStatus.IN_TRANSIT: "В дорозі",
    DealStatus.DELIVERED: "Доставлено",
    DealStatus.INVOICED: "Рахунок виставлено",
    DealStatus.PAID: "Оплачено",
    DealStatus.CANCELLED: "Скасовано",
}


@router.post("/{deal_id}/status", response_class=RedirectResponse)
async def update_deal_status(
    deal_id: int,
    new_status: str = Form(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    """Advance the deal to the next lifecycle status."""
    deal = await _get_deal_with_relations(deal_id, session)

    # Validate transition
    if current_user.role == UserRole.ADMIN:
        allowed = _ADMIN_TRANSITIONS.get(deal.status, [])
        if new_status not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Перехід {deal.status} → {new_status} не дозволено.",
            )
    elif current_user.role == UserRole.CARRIER:
        expected_next = _CARRIER_TRANSITIONS.get(deal.status)
        if new_status != expected_next:
            raise HTTPException(
                status_code=400,
                detail=f"Наступний доступний статус: {expected_next}.",
            )
    else:
        raise HTTPException(status_code=403, detail="Тільки перевізник або адмін може змінювати статус.")

    old_status = deal.status
    deal.status = new_status

    # When a deal is delivered, record a price sample for future pricing suggestions
    if new_status == DealStatus.DELIVERED and deal.price_amount and deal.load:
        load = deal.load
        sample = PriceSample(
            source="deal",
            source_id=str(deal.id),
            from_country=load.from_country,
            from_city=load.from_city,
            to_country=load.to_country,
            to_city=load.to_city,
            price_amount=deal.price_amount,
            price_currency=deal.price_currency or "UAH",
            weight_tons=load.weight_tons,
            body_type=(load.body_types[0] if load.body_types else None),
        )
        session.add(sample)

    await session.commit()

    log.info(
        "Deal #%d status: %s → %s (by user %s)",
        deal_id, old_status, new_status, current_user.email,
    )
    return RedirectResponse(url=f"/deals/{deal_id}", status_code=303)
