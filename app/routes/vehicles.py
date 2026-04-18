"""Vehicle detail & deal-proposal routes (shipper-facing).

GET  /vehicles/{vehicle_id}            — public vehicle detail (auth required)
POST /vehicles/{vehicle_id}/propose    — shipper proposes a deal linking one of
                                         their loads to this vehicle
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
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
    Vehicle,
    VehicleStatus,
)
from app.db.session import get_session
from app.services.auth import get_current_user

log = logging.getLogger(__name__)
router = APIRouter(prefix="/vehicles", tags=["vehicles"])
templates = Jinja2Templates(directory="app/templates")


# ---------------------------------------------------------------------------
# GET /vehicles/{vehicle_id}
# ---------------------------------------------------------------------------

@router.get("/{vehicle_id}", response_class=HTMLResponse)
async def vehicle_detail(
    vehicle_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    result = await session.execute(
        select(Vehicle)
        .options(selectinload(Vehicle.company), selectinload(Vehicle.carrier))
        .where(Vehicle.id == vehicle_id, Vehicle.status == VehicleStatus.AVAILABLE)
    )
    vehicle = result.scalar_one_or_none()
    if not vehicle:
        raise HTTPException(404, "Транспорт не знайдено або вже недоступний.")

    # Load shipper's active loads for the "propose deal" form
    my_loads: list[Load] = []
    if current_user.role in ("shipper", "forwarder", "admin"):
        my_loads = list(
            (
                await session.scalars(
                    select(Load)
                    .where(
                        Load.posted_by_user_id == current_user.id,
                        Load.status.in_([LoadStatus.NEW, LoadStatus.MATCHED]),
                    )
                    .order_by(Load.created_at.desc())
                    .limit(20)
                )
            ).all()
        )

    # Check if a deal/match already exists between this vehicle and any of shipper's loads
    existing_deal = None
    if my_loads:
        load_ids = [lo.id for lo in my_loads]
        existing_deal = await session.scalar(
            select(Deal)
            .where(
                Deal.vehicle_id == vehicle_id,
                Deal.load_id.in_(load_ids),
            )
            .limit(1)
        )

    return templates.TemplateResponse(
        request,
        "vehicles/detail.html",
        {
            "current_user": current_user,
            "vehicle": vehicle,
            "my_loads": my_loads,
            "existing_deal": existing_deal,
        },
    )


# ---------------------------------------------------------------------------
# POST /vehicles/{vehicle_id}/propose — create a Match → Deal (booked)
# ---------------------------------------------------------------------------

@router.post("/{vehicle_id}/propose")
async def vehicle_propose_deal(
    vehicle_id: int,
    load_id: int = Form(...),
    price_amount: float = Form(...),
    price_currency: str = Form("EUR"),
    notes: str = Form(""),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ("shipper", "forwarder", "admin"):
        raise HTTPException(403, "Тільки відправники можуть пропонувати угоди.")

    vehicle = await session.get(Vehicle, vehicle_id)
    if not vehicle or vehicle.status != VehicleStatus.AVAILABLE:
        raise HTTPException(404, "Транспорт не знайдено або вже недоступний.")

    load = await session.get(Load, load_id)
    if not load:
        raise HTTPException(404, "Вантаж не знайдено.")
    if load.posted_by_user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(403, "Це не ваш вантаж.")
    if load.status not in (LoadStatus.NEW, LoadStatus.MATCHED):
        raise HTTPException(400, "Вантаж вже заброньовано або скасовано.")

    # Check for duplicate
    existing = await session.scalar(
        select(Deal).where(Deal.vehicle_id == vehicle_id, Deal.load_id == load_id)
    )
    if existing:
        return RedirectResponse(f"/deals/{existing.id}", status_code=303)

    # Create Match record
    match = Match(
        load_id=load.id,
        vehicle_id=vehicle.id,
        status=MatchStatus.PROMOTED,
        score=80,
        reasoning="Запропоновано відправником через платформу",
    )
    session.add(match)
    await session.flush()

    # Create Deal
    deal = Deal(
        load_id=load.id,
        vehicle_id=vehicle.id,
        carrier_id=vehicle.carrier_id,
        status=DealStatus.BOOKED,
        price_amount=price_amount,
        price_currency=price_currency.upper(),
        notes=notes.strip() or None,
    )
    session.add(deal)

    # Update statuses
    vehicle.status = VehicleStatus.BOOKED
    load.status = LoadStatus.BOOKED

    await session.commit()
    return RedirectResponse(f"/deals/{deal.id}", status_code=303)
