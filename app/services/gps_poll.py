"""GPS position polling service.

Polls our self-hosted Traccar server and writes fresh VehiclePosition rows
for every vehicle that has a linked traccar_device_id.

Called by APScheduler every TRACCAR_POLL_INTERVAL_SECONDS (default 30s).

Flow
----
1. SELECT all vehicles WHERE traccar_device_id IS NOT NULL
2. Open TraccarAdapter once, call GET /api/positions for all device IDs in one request
3. For each returned position:
   a. Write a VehiclePosition row (upsert by vehicle_id + recorded_at)
   b. Update vehicle.updated_at (so "last seen" works in UI)
4. Update telematics_account.last_synced_at

Only positions newer than the vehicle's last recorded position are inserted,
to avoid duplicates on idling vehicles.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.traccar import TraccarAdapter
from app.db.models import TelematicsProvider, Vehicle, VehiclePosition
from app.db.session import AsyncSessionLocal

log = logging.getLogger(__name__)


class GPSPollService:
    """Pulls positions from Traccar and persists them."""

    def __init__(self, base_url: str, admin_email: str, admin_password: str) -> None:
        self._base_url = base_url
        self._email = admin_email
        self._password = admin_password

    async def poll(self) -> int:
        """Run one polling cycle. Returns count of new VehiclePosition rows."""
        async with AsyncSessionLocal() as session:
            return await self._poll_inner(session)

    async def _poll_inner(self, session: AsyncSession) -> int:
        # Fetch all vehicles with a linked Traccar device
        result = await session.execute(
            select(Vehicle).where(Vehicle.traccar_device_id.isnot(None))
        )
        vehicles: list[Vehicle] = list(result.scalars().all())

        if not vehicles:
            log.debug("GPS poll: no vehicles with traccar_device_id")
            return 0

        device_id_to_vehicle: dict[int, Vehicle] = {
            v.traccar_device_id: v  # type: ignore[index]
            for v in vehicles
            if v.traccar_device_id is not None
        }

        async with TraccarAdapter(self._base_url, self._email, self._password) as tc:
            positions = await tc.get_positions(list(device_id_to_vehicle.keys()))

        new_count = 0
        for device_id, pos in positions.items():
            vehicle = device_id_to_vehicle.get(device_id)
            if not vehicle:
                continue

            # Skip if we already have this exact fix time
            existing = await session.scalar(
                select(VehiclePosition)
                .where(
                    VehiclePosition.vehicle_id == vehicle.id,
                    VehiclePosition.recorded_at == pos.fixed_at,
                )
            )
            if existing:
                continue

            vp = VehiclePosition(
                vehicle_id=vehicle.id,
                lat=pos.lat,
                lon=pos.lon,
                speed_kmh=int(pos.speed_kmh),
                heading_deg=pos.course,
                address=pos.address,
                provider=TelematicsProvider.TRACCAR,
                provider_tracker_id=str(device_id),
                recorded_at=pos.fixed_at,
            )
            session.add(vp)
            new_count += 1

        if new_count:
            await session.commit()
            log.info("GPS poll: %d new positions for %d vehicles", new_count, len(vehicles))

        return new_count


# ---------------------------------------------------------------------------
# Module-level singleton accessed by main.py lifespan
# ---------------------------------------------------------------------------

_poll_service: GPSPollService | None = None


def init_gps_poll_service(base_url: str, email: str, password: str) -> None:
    """Called once at startup by main.py lifespan."""
    global _poll_service
    _poll_service = GPSPollService(base_url, email, password)
    log.info("GPS poll service initialized (Traccar: %s)", base_url)


async def run_gps_poll_job() -> None:
    """APScheduler entry point — module-level async function."""
    if _poll_service is None:
        log.debug("GPS poll skipped: service not initialized (TRACCAR_BASE_URL not set?)")
        return
    try:
        await _poll_service.poll()
    except Exception:
        log.exception("GPS poll job failed")
