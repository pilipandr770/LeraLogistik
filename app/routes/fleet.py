"""Fleet management routes for carrier companies.

Carriers can:
  GET  /fleet              — list their vehicles with GPS status
  GET  /fleet/new          — form to add a new vehicle
  POST /fleet/new          — create + register in Traccar
  POST /fleet/{id}/link    — link an existing vehicle to a Traccar device
  POST /fleet/{id}/delete  — remove a vehicle from fleet (and Traccar)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.traccar import TraccarAdapter
from app.config import get_settings
from app.db.models import TelematicsAccount, TelematicsProvider, User, Vehicle, VehiclePosition, VehicleStatus
from app.db.session import get_session
from app.services.access import require_carrier_or_admin

log = logging.getLogger(__name__)
router = APIRouter(prefix="/fleet", tags=["fleet"])
templates = Jinja2Templates(directory="app/templates")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_or_create_traccar_account(
    company_id: int,
    session: AsyncSession,
) -> TelematicsAccount:
    """Return the Traccar TelematicsAccount for the company, creating it if needed."""
    result = await session.execute(
        select(TelematicsAccount).where(
            TelematicsAccount.company_id == company_id,
            TelematicsAccount.provider == TelematicsProvider.TRACCAR,
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        account = TelematicsAccount(
            company_id=company_id,
            provider=TelematicsProvider.TRACCAR,
            is_active=True,
        )
        session.add(account)
        await session.flush()
    return account


async def _ensure_traccar_group(
    account: TelematicsAccount,
    company_name: str,
    session: AsyncSession,
) -> int:
    """Make sure a Traccar group exists for this company; return its group_id."""
    settings = get_settings()
    if not settings.traccar_admin_password:
        raise HTTPException(
            status_code=503,
            detail="GPS telematics not configured (TRACCAR_ADMIN_PASSWORD missing).",
        )

    if account.traccar_group_id:
        return account.traccar_group_id

    async with TraccarAdapter(
        settings.traccar_base_url,
        settings.traccar_admin_email,
        settings.traccar_admin_password,
    ) as tc:
        group = await tc.create_group(company_name)

    account.traccar_group_id = group.group_id
    await session.flush()
    return group.group_id


# ---------------------------------------------------------------------------
# GET /fleet
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def fleet_list(
    request: Request,
    current_user: User = Depends(require_carrier_or_admin),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    if not current_user.company_id:
        return RedirectResponse("/onboarding", status_code=302)

    # Load all platform vehicles for this company
    result = await session.execute(
        select(Vehicle).where(Vehicle.company_id == current_user.company_id)
    )
    vehicles = list(result.scalars().all())

    # Collect latest GPS position per vehicle 
    vehicle_ids = [v.id for v in vehicles]
    positions: dict[int, VehiclePosition] = {}
    if vehicle_ids:
        pos_result = await session.execute(
            select(VehiclePosition)
            .where(VehiclePosition.vehicle_id.in_(vehicle_ids))
            .order_by(VehiclePosition.vehicle_id, VehiclePosition.recorded_at.desc())
            .distinct(VehiclePosition.vehicle_id)
        )
        for vp in pos_result.scalars().all():
            positions[vp.vehicle_id] = vp

    settings = get_settings()
    traccar_configured = bool(settings.traccar_admin_password)

    return templates.TemplateResponse(
        "fleet/index.html",
        {
            "request": request,
            "current_user": current_user,
            "vehicles": vehicles,
            "positions": positions,
            "traccar_configured": traccar_configured,
            "traccar_base_url": settings.traccar_base_url,
        },
    )


# ---------------------------------------------------------------------------
# GET /fleet/new — registration form
# ---------------------------------------------------------------------------

@router.get("/new", response_class=HTMLResponse)
async def fleet_new_form(
    request: Request,
    current_user: User = Depends(require_carrier_or_admin),
) -> HTMLResponse:
    if not current_user.company_id:
        return RedirectResponse("/onboarding", status_code=302)

    return templates.TemplateResponse(
        "fleet/new_vehicle.html",
        {"request": request, "current_user": current_user, "errors": {}},
    )


# ---------------------------------------------------------------------------
# POST /fleet/new — create vehicle + register in Traccar
# ---------------------------------------------------------------------------

@router.post("/new")
async def fleet_new_submit(
    request: Request,
    plate: str = Form(...),
    body_type: str = Form(...),
    capacity_tons: float = Form(...),
    traccar_unique_id: str = Form(""),
    current_user: User = Depends(require_carrier_or_admin),
    session: AsyncSession = Depends(get_session),
):
    if not current_user.company_id:
        return RedirectResponse("/onboarding", status_code=302)

    plate = plate.strip().upper()
    traccar_unique_id = traccar_unique_id.strip()

    if not plate:
        return templates.TemplateResponse(
            "fleet/new_vehicle.html",
            {
                "request": request,
                "current_user": current_user,
                "errors": {"plate": "Обов'язкове поле"},
            },
            status_code=422,
        )

    settings = get_settings()

    # ── 1. Create Vehicle row ──────────────────────────────────────────────
    vehicle = Vehicle(
        source="platform",
        external_id=None,
        status=VehicleStatus.AVAILABLE,
        company_id=current_user.company_id,
        body_type=body_type,
        capacity_tons=capacity_tons,
        # Store plate in raw_payload for now (no dedicated column)
        raw_payload={"plate": plate},
    )
    session.add(vehicle)
    await session.flush()  # get vehicle.id

    # ── 2. Register in Traccar (optional — only when configured) ───────────
    if traccar_unique_id and settings.traccar_admin_password:
        try:
            account = await _get_or_create_traccar_account(current_user.company_id, session)
            company_name = (
                current_user.company.name if current_user.company else f"company-{current_user.company_id}"
            )
            group_id = await _ensure_traccar_group(account, company_name, session)

            async with TraccarAdapter(
                settings.traccar_base_url,
                settings.traccar_admin_email,
                settings.traccar_admin_password,
            ) as tc:
                device = await tc.create_device(
                    name=plate,
                    unique_id=traccar_unique_id,
                    group_id=group_id,
                )

            vehicle.traccar_device_id = device.device_id
            vehicle.traccar_unique_id = traccar_unique_id
        except Exception:
            log.exception("Failed to register vehicle %s in Traccar", plate)
            # Don't abort — vehicle is saved, Traccar link can be added later

    await session.commit()
    return RedirectResponse("/fleet/", status_code=303)


# ---------------------------------------------------------------------------
# POST /fleet/{vehicle_id}/link — link to Traccar after the fact
# ---------------------------------------------------------------------------

@router.post("/{vehicle_id}/link")
async def fleet_link_traccar(
    vehicle_id: int,
    traccar_unique_id: str = Form(...),
    current_user: User = Depends(require_carrier_or_admin),
    session: AsyncSession = Depends(get_session),
):
    vehicle = await session.get(Vehicle, vehicle_id)
    if vehicle is None or vehicle.company_id != current_user.company_id:
        raise HTTPException(404, "Vehicle not found")

    settings = get_settings()
    if not settings.traccar_admin_password:
        raise HTTPException(503, "Traccar not configured")

    unique_id = traccar_unique_id.strip()
    plate = (vehicle.raw_payload or {}).get("plate", f"vehicle-{vehicle.id}")

    account = await _get_or_create_traccar_account(current_user.company_id, session)
    company_name = (
        current_user.company.name if current_user.company else f"company-{current_user.company_id}"
    )
    group_id = await _ensure_traccar_group(account, company_name, session)

    async with TraccarAdapter(
        settings.traccar_base_url,
        settings.traccar_admin_email,
        settings.traccar_admin_password,
    ) as tc:
        device = await tc.create_device(
            name=plate,
            unique_id=unique_id,
            group_id=group_id,
        )

    vehicle.traccar_device_id = device.device_id
    vehicle.traccar_unique_id = unique_id
    await session.commit()

    return RedirectResponse("/fleet/", status_code=303)


# ---------------------------------------------------------------------------
# POST /fleet/{vehicle_id}/delete — remove from fleet + Traccar
# ---------------------------------------------------------------------------

@router.post("/{vehicle_id}/delete")
async def fleet_delete_vehicle(
    vehicle_id: int,
    current_user: User = Depends(require_carrier_or_admin),
    session: AsyncSession = Depends(get_session),
):
    vehicle = await session.get(Vehicle, vehicle_id)
    if vehicle is None or vehicle.company_id != current_user.company_id:
        raise HTTPException(404, "Vehicle not found")

    settings = get_settings()
    # Remove from Traccar first (best-effort)
    if vehicle.traccar_device_id and settings.traccar_admin_password:
        try:
            async with TraccarAdapter(
                settings.traccar_base_url,
                settings.traccar_admin_email,
                settings.traccar_admin_password,
            ) as tc:
                await tc.delete_device(vehicle.traccar_device_id)
        except Exception:
            log.warning("Failed to delete Traccar device %d", vehicle.traccar_device_id)

    await session.delete(vehicle)
    await session.commit()
    return RedirectResponse("/fleet/", status_code=303)
