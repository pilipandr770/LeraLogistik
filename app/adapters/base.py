"""Abstract interface for a freight exchange adapter.

The rest of the application must never import a specific exchange's module
(Lardi, Della, Timocom, Trans.eu). It always goes through this interface.

Adding a new exchange = implementing this class in a new file. The rest of
the app (agents, dashboard, scheduler) does not change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


# --- Normalized data classes (exchange-agnostic) ---

@dataclass
class NormalizedPoint:
    """A waypoint — either pickup or delivery."""
    country: str | None = None   # ISO alpha-2
    city: str | None = None
    postcode: str | None = None
    lat: Decimal | None = None
    lon: Decimal | None = None


@dataclass
class NormalizedLoad:
    """An exchange-agnostic representation of a cargo proposal."""
    source: str                                # e.g. "lardi"
    external_id: str

    origin: NormalizedPoint = field(default_factory=NormalizedPoint)
    destination: NormalizedPoint = field(default_factory=NormalizedPoint)

    cargo_name: str | None = None
    weight_tons: Decimal | None = None
    volume_m3: Decimal | None = None
    body_types: list[str] = field(default_factory=list)
    is_adr: bool = False
    adr_class: str | None = None

    pickup_date_from: datetime | None = None
    pickup_date_to: datetime | None = None

    price_amount: Decimal | None = None
    price_currency: str | None = None
    price_is_vat_included: bool | None = None

    owner_name: str | None = None
    owner_phone: str | None = None
    owner_external_id: int | str | None = None

    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedVehicle:
    """An exchange-agnostic representation of a free-vehicle proposal."""
    source: str
    external_id: str

    origin: NormalizedPoint = field(default_factory=NormalizedPoint)
    destination: NormalizedPoint = field(default_factory=NormalizedPoint)

    body_type: str | None = None
    capacity_tons: Decimal | None = None
    capacity_m3: Decimal | None = None

    available_from: datetime | None = None
    available_to: datetime | None = None

    carrier_name: str | None = None
    carrier_external_id: int | str | None = None
    carrier_phone: str | None = None

    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchFilter:
    """Filters for searching proposals on an exchange."""
    countries_from: list[str] = field(default_factory=list)   # ISO alpha-2
    countries_to: list[str] = field(default_factory=list)
    body_types: list[str] = field(default_factory=list)
    min_weight_tons: Decimal | None = None
    max_weight_tons: Decimal | None = None
    date_from: datetime | None = None
    page: int = 0
    per_page: int = 50


# --- The interface ---

class ExchangeAdapter(ABC):
    """A contract that every freight-exchange adapter must honour."""

    source: str  # short name, e.g. "lardi"

    @abstractmethod
    async def search_loads(self, flt: SearchFilter) -> list[NormalizedLoad]:
        """Return cargo proposals matching the filter."""

    @abstractmethod
    async def search_vehicles(self, flt: SearchFilter) -> list[NormalizedVehicle]:
        """Return free-vehicle proposals matching the filter."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the exchange API is reachable and our token is valid."""
