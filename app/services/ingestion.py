"""Ingestion service.

Takes normalized data from exchange adapters and persists it in our database,
doing upserts on (source, external_id). This module is the only place that
knows how to convert NormalizedLoad -> Load ORM object.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import (
    ExchangeAdapter,
    NormalizedLoad,
    NormalizedVehicle,
    SearchFilter,
)
from app.db.models import Carrier, Load, LoadStatus, PriceSample, Vehicle, VehicleStatus

log = logging.getLogger(__name__)


class IngestionService:
    """Pulls fresh proposals from an adapter and upserts them into the DB."""

    def __init__(self, adapter: ExchangeAdapter, session: AsyncSession) -> None:
        self._adapter = adapter
        self._session = session

    async def ingest_loads(self, flt: SearchFilter) -> int:
        """Fetch loads from the exchange, upsert them, return count of new rows."""
        proposals = await self._adapter.search_loads(flt)
        new_count = 0
        for p in proposals:
            if await self._upsert_load(p):
                new_count += 1
        await self._session.commit()
        log.info("Ingested %d loads from %s (%d new)",
                 len(proposals), self._adapter.source, new_count)
        return new_count

    async def ingest_vehicles(self, flt: SearchFilter) -> int:
        proposals = await self._adapter.search_vehicles(flt)
        new_count = 0
        for p in proposals:
            if await self._upsert_vehicle(p):
                new_count += 1
        await self._session.commit()
        log.info("Ingested %d vehicles from %s (%d new)",
                 len(proposals), self._adapter.source, new_count)
        return new_count

    # ---------- internals ----------

    async def _upsert_load(self, n: NormalizedLoad) -> bool:
        """Return True if a new row was created (else it was an update)."""
        existing = await self._session.scalar(
            select(Load).where(Load.source == n.source, Load.external_id == n.external_id)
        )
        if existing:
            # Minimal update: refresh the raw payload and timestamp only.
            existing.raw_payload = n.raw_payload
            return False

        load = Load(
            source=n.source,
            external_id=n.external_id,
            status=LoadStatus.NEW,
            from_country=n.origin.country,
            from_city=n.origin.city,
            from_postcode=n.origin.postcode,
            from_lat=n.origin.lat,
            from_lon=n.origin.lon,
            to_country=n.destination.country,
            to_city=n.destination.city,
            to_postcode=n.destination.postcode,
            to_lat=n.destination.lat,
            to_lon=n.destination.lon,
            cargo_name=n.cargo_name,
            weight_tons=n.weight_tons,
            volume_m3=n.volume_m3,
            body_types=n.body_types or None,
            is_adr=n.is_adr,
            adr_class=n.adr_class,
            pickup_date_from=n.pickup_date_from,
            pickup_date_to=n.pickup_date_to,
            price_amount=n.price_amount,
            price_currency=n.price_currency,
            price_is_vat_included=n.price_is_vat_included,
            owner_name=n.owner_name,
            owner_phone=n.owner_phone,
            owner_lardi_id=(
                int(n.owner_external_id) if n.owner_external_id and str(n.owner_external_id).isdigit() else None
            ),
            raw_payload=n.raw_payload,
        )
        self._session.add(load)

        # ── Collect price sample for the Pricing Agent ─────────────────────
        if n.price_amount and n.price_currency:
            body_type = (n.body_types[0] if n.body_types else None)
            sample = PriceSample(
                source="lardi",
                source_id=n.external_id,
                from_country=n.origin.country,
                from_city=n.origin.city,
                to_country=n.destination.country,
                to_city=n.destination.city,
                price_amount=n.price_amount,
                price_currency=n.price_currency,
                weight_tons=n.weight_tons,
                body_type=body_type,
            )
            self._session.add(sample)

        return True

    async def _upsert_vehicle(self, n: NormalizedVehicle) -> bool:
        existing = await self._session.scalar(
            select(Vehicle).where(
                Vehicle.source == n.source, Vehicle.external_id == n.external_id
            )
        )
        if existing:
            existing.raw_payload = n.raw_payload
            return False

        carrier = await self._get_or_create_carrier(n)

        vehicle = Vehicle(
            source=n.source,
            external_id=n.external_id,
            status=VehicleStatus.AVAILABLE,
            carrier_id=carrier.id if carrier else None,
            from_country=n.origin.country,
            from_city=n.origin.city,
            from_lat=n.origin.lat,
            from_lon=n.origin.lon,
            to_country=n.destination.country,
            to_city=n.destination.city,
            body_type=n.body_type,
            capacity_tons=n.capacity_tons,
            capacity_m3=n.capacity_m3,
            available_from=n.available_from,
            available_to=n.available_to,
            raw_payload=n.raw_payload,
        )
        self._session.add(vehicle)
        return True

    async def _get_or_create_carrier(self, n: NormalizedVehicle) -> Carrier | None:
        """Create a Carrier row on first sight, or return the existing one."""
        if not n.carrier_external_id or not str(n.carrier_external_id).isdigit():
            return None

        ext_id = int(n.carrier_external_id)
        carrier = await self._session.scalar(
            select(Carrier).where(Carrier.lardi_user_id == ext_id)
        )
        if carrier:
            return carrier

        carrier = Carrier(
            name=n.carrier_name or f"Lardi user #{ext_id}",
            phone=n.carrier_phone,
            lardi_user_id=ext_id,
        )
        self._session.add(carrier)
        await self._session.flush()   # populate carrier.id
        return carrier
