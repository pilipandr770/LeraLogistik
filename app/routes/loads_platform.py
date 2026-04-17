"""Load management routes — shippers post their own cargo directly on the platform.

Endpoints:
  GET  /loads/new       — form to post a new load (auth required, shipper/forwarder)
  POST /loads/new       — create the load
  GET  /loads/my        — list all loads posted by the current user
  GET  /loads/{id}      — load detail + its AI matches (existing route reused)
  POST /loads/{id}/cancel — cancel a load
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Load, LoadStatus, User, UserRole
from app.db.session import get_session
from app.services.auth import get_current_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/loads", tags=["loads"])
templates = Jinja2Templates(directory="app/templates")

_ALLOWED_ROLES = {UserRole.SHIPPER, UserRole.FORWARDER, UserRole.ADMIN}


def _require_poster(user: User) -> User:
    if user.role not in _ALLOWED_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Тільки заказчики та експедитори можуть публікувати вантажі",
        )
    return user


# ---------------------------------------------------------------------------
# New load form
# ---------------------------------------------------------------------------

@router.get("/new", response_class=HTMLResponse)
async def new_load_form(
    request: Request,
    user: User = Depends(get_current_user),
) -> HTMLResponse:
    _require_poster(user)
    return templates.TemplateResponse(
        "loads/new.html",
        {"request": request, "current_user": user, "errors": []},
    )


@router.post("/new", response_class=HTMLResponse)
async def new_load_submit(
    request: Request,
    # Route
    from_country: str = Form(...),
    from_city: str = Form(...),
    to_country: str = Form(...),
    to_city: str = Form(...),
    # Cargo
    cargo_name: str = Form(...),
    weight_tons: str = Form(""),
    volume_m3: str = Form(""),
    body_types: list[str] = Form(default=[]),
    is_adr: str = Form(""),
    # Dates
    pickup_date_from: str = Form(""),
    pickup_date_to: str = Form(""),
    # Price
    price_amount: str = Form(""),
    price_currency: str = Form("UAH"),
    # Notes become cargo_name extended
    notes: str = Form(""),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    _require_poster(user)
    errors: list[str] = []

    from_country = from_country.strip().upper()
    from_city = from_city.strip()
    to_country = to_country.strip().upper()
    to_city = to_city.strip()
    cargo_name = cargo_name.strip()

    if not from_country or not from_city:
        errors.append("Вкажіть місто відправлення")
    if not to_country or not to_city:
        errors.append("Вкажіть місто доставки")
    if not cargo_name:
        errors.append("Вкажіть тип вантажу")

    weight: float | None = None
    if weight_tons:
        try:
            weight = float(weight_tons.replace(",", "."))
            if weight <= 0:
                errors.append("Вага має бути більше 0")
        except ValueError:
            errors.append("Некоректне значення ваги")

    volume: float | None = None
    if volume_m3:
        try:
            volume = float(volume_m3.replace(",", "."))
        except ValueError:
            errors.append("Некоректне значення об'єму")

    price: float | None = None
    if price_amount:
        try:
            price = float(price_amount.replace(",", ".").replace(" ", ""))
        except ValueError:
            errors.append("Некоректна ціна")

    pickup_from: datetime | None = None
    pickup_to: datetime | None = None
    if pickup_date_from:
        try:
            pickup_from = datetime.fromisoformat(pickup_date_from).replace(tzinfo=timezone.utc)
        except ValueError:
            errors.append("Некоректна дата отримання")
    if pickup_date_to:
        try:
            pickup_to = datetime.fromisoformat(pickup_date_to).replace(tzinfo=timezone.utc)
        except ValueError:
            errors.append("Некоректна кінцева дата")

    if errors:
        return templates.TemplateResponse(
            "loads/new.html",
            {
                "request": request,
                "current_user": user,
                "errors": errors,
                "form": await request.form(),
            },
            status_code=422,
        )

    load = Load(
        source="platform",
        status=LoadStatus.NEW,
        from_country=from_country,
        from_city=from_city,
        to_country=to_country,
        to_city=to_city,
        cargo_name=cargo_name + (f" — {notes.strip()}" if notes.strip() else ""),
        weight_tons=weight,
        volume_m3=volume,
        body_types=body_types if body_types else None,
        is_adr=bool(is_adr),
        pickup_date_from=pickup_from,
        pickup_date_to=pickup_to,
        price_amount=price,
        price_currency=price_currency.strip().upper() or "UAH",
        posted_by_user_id=user.id,
    )
    session.add(load)
    await session.commit()
    log.info("New platform load %s posted by user %s", load.id, user.id)

    return RedirectResponse(url=f"/loads/my", status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# My loads
# ---------------------------------------------------------------------------

@router.get("/my", response_class=HTMLResponse)
async def my_loads(
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    _require_poster(user)

    loads = (await session.scalars(
        select(Load)
        .where(Load.posted_by_user_id == user.id)
        .order_by(Load.created_at.desc())
        .limit(100)
    )).all()

    return templates.TemplateResponse(
        "loads/my.html",
        {"request": request, "current_user": user, "loads": loads},
    )


# ---------------------------------------------------------------------------
# Cancel load
# ---------------------------------------------------------------------------

@router.post("/{load_id}/cancel")
async def cancel_load(
    load_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    result = await session.execute(select(Load).where(Load.id == load_id))
    load = result.scalar_one_or_none()

    if not load:
        raise HTTPException(404, "Вантаж не знайдено")
    if load.posted_by_user_id != user.id and user.role != UserRole.ADMIN:
        raise HTTPException(403, "Ви не можете скасувати цей вантаж")
    if load.status in (LoadStatus.BOOKED, LoadStatus.CANCELLED):
        raise HTTPException(400, f"Вантаж вже в статусі {load.status}")

    load.status = LoadStatus.CANCELLED
    await session.commit()
    return RedirectResponse(url="/loads/my", status_code=status.HTTP_303_SEE_OTHER)
