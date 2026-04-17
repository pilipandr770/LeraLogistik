"""Adapter for the Lardi-Trans Public API v2.

Documentation: https://api.lardi-trans.com/v2/docs/index.html

Key endpoints we use in the MVP:
    POST /v2/proposals/search/cargo    - search cargo proposals
    POST /v2/proposals/search/lorry    - search vehicle proposals
    GET  /v2/proposals/view/cargo/{id} - view a single cargo proposal
    GET  /v2/proposals/view/lorry/{id} - view a single vehicle proposal
    GET  /v2/users/{id}                - fetch user (carrier) info

Auth: the API token is passed in the `Authorization` header as a plain string
(not "Bearer ..."), per the Lardi-Trans documentation example:

    curl -H 'Authorization: 29ZS7LMG9D2000000416' ...

Rate limits: not publicly documented. We use tenacity to retry with
exponential backoff, and the scheduler's polling interval is configurable.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.adapters.base import (
    ExchangeAdapter,
    NormalizedLoad,
    NormalizedPoint,
    NormalizedVehicle,
    SearchFilter,
)
from app.config import get_settings

log = logging.getLogger(__name__)


class LardiAdapter(ExchangeAdapter):
    """Concrete adapter for Lardi-Trans."""

    source = "lardi"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        settings = get_settings()
        self._base_url = settings.lardi_api_base_url.rstrip("/")
        self._language = settings.lardi_api_language
        self._token = settings.lardi_api_token
        self._client = client or httpx.AsyncClient(timeout=30.0)

    async def __aenter__(self) -> LardiAdapter:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self._client.aclose()

    # ---------- public methods ----------

    async def health_check(self) -> bool:
        """Lightweight check: fetch the countries reference.

        This endpoint is available on any active token and returns quickly.
        """
        try:
            await self._get("/references/countries")
            return True
        except Exception as exc:   # noqa: BLE001
            log.warning("Lardi health check failed: %s", exc)
            return False

    async def search_loads(self, flt: SearchFilter) -> list[NormalizedLoad]:
        """POST /v2/proposals/search/cargo"""
        body = self._build_search_body(flt)
        data = await self._post("/proposals/search/cargo", body)
        items = data.get("content", []) if isinstance(data, dict) else []
        return [self._to_normalized_load(item) for item in items]

    async def search_vehicles(self, flt: SearchFilter) -> list[NormalizedVehicle]:
        """POST /v2/proposals/search/lorry"""
        body = self._build_search_body(flt)
        data = await self._post("/proposals/search/lorry", body)
        items = data.get("content", []) if isinstance(data, dict) else []
        return [self._to_normalized_vehicle(item) for item in items]

    # ---------- HTTP helpers with retry ----------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        reraise=True,
    )
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = await self._client.get(
            f"{self._base_url}{path}",
            params={"language": self._language, **(params or {})},
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        reraise=True,
    )
    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        response = await self._client.post(
            f"{self._base_url}{path}",
            params={"language": self._language},
            headers=self._headers(),
            json=body,
        )
        response.raise_for_status()
        return response.json()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ---------- request body construction ----------

    @staticmethod
    def _build_search_body(flt: SearchFilter) -> dict[str, Any]:
        """Translate our SearchFilter into the Lardi request body.

        Lardi's filter object has many more fields than we need at the MVP.
        See docs: /structures/proposals/proposal-cargo-filter.html
        """
        filter_payload: dict[str, Any] = {}

        if flt.countries_from:
            filter_payload["directionFrom"] = [
                {"countrySign": c} for c in flt.countries_from
            ]
        if flt.countries_to:
            filter_payload["directionTo"] = [
                {"countrySign": c} for c in flt.countries_to
            ]
        if flt.date_from:
            filter_payload["dateFrom"] = flt.date_from.strftime("%Y-%m-%d")

        return {
            "filter": filter_payload,
            "options": {
                "page": flt.page,
                "perPage": flt.per_page,
            },
        }

    # ---------- normalization ----------

    def _to_normalized_load(self, item: dict[str, Any]) -> NormalizedLoad:
        """Map a raw Lardi 'cargo' record to our normalized form.

        Notes
        -----
        * Lardi nests route info in `waypointListSource` / `waypointListTarget`
          or (in some endpoints) in `directionFrom` / `directionTo`. We try
          both, falling back gracefully.
        * Field names may drift across API versions. Once you have a real
          token, log a raw response once and adjust the mapping.
        """
        from_point = self._extract_point(
            item.get("waypointListSource")
            or item.get("directionFrom")
            or item.get("from")
        )
        to_point = self._extract_point(
            item.get("waypointListTarget")
            or item.get("directionTo")
            or item.get("to")
        )

        size = item.get("size") or {}
        payment = item.get("payment") or {}
        owner = item.get("contact") or item.get("owner") or {}

        return NormalizedLoad(
            source=self.source,
            external_id=str(item.get("id", "")),
            origin=from_point,
            destination=to_point,
            cargo_name=item.get("cargoName") or item.get("name"),
            weight_tons=self._decimal(size.get("weight")),
            volume_m3=self._decimal(size.get("volume")),
            body_types=[bt.get("name") for bt in item.get("bodyTypes", []) if bt.get("name")],
            is_adr=bool(item.get("adr") or item.get("dangerous")),
            adr_class=(item.get("adr") or {}).get("className") if isinstance(item.get("adr"), dict) else None,
            pickup_date_from=None,   # TODO: parse dates from Lardi's format
            pickup_date_to=None,
            price_amount=self._decimal(payment.get("price")),
            price_currency=(payment.get("currency") or {}).get("sign"),
            price_is_vat_included=payment.get("vat"),
            owner_name=owner.get("name"),
            owner_phone=(owner.get("phone") or {}).get("number") if isinstance(owner.get("phone"), dict) else owner.get("phone"),
            owner_external_id=owner.get("id"),
            raw_payload=item,
        )

    def _to_normalized_vehicle(self, item: dict[str, Any]) -> NormalizedVehicle:
        """Map a raw Lardi 'lorry' record to our normalized form."""
        from_point = self._extract_point(
            item.get("waypointListSource")
            or item.get("directionFrom")
            or item.get("from")
        )
        to_point = self._extract_point(
            item.get("waypointListTarget")
            or item.get("directionTo")
            or item.get("to")
        )

        size = item.get("size") or {}
        owner = item.get("contact") or item.get("owner") or {}

        body_types = [bt.get("name") for bt in item.get("bodyTypes", []) if bt.get("name")]

        return NormalizedVehicle(
            source=self.source,
            external_id=str(item.get("id", "")),
            origin=from_point,
            destination=to_point,
            body_type=body_types[0] if body_types else None,
            capacity_tons=self._decimal(size.get("weight")),
            capacity_m3=self._decimal(size.get("volume")),
            available_from=None,   # TODO
            available_to=None,
            carrier_name=owner.get("name"),
            carrier_external_id=owner.get("id"),
            carrier_phone=(owner.get("phone") or {}).get("number") if isinstance(owner.get("phone"), dict) else owner.get("phone"),
            raw_payload=item,
        )

    @staticmethod
    def _extract_point(raw: Any) -> NormalizedPoint:
        """Pull a NormalizedPoint out of various Lardi shapes.

        Lardi returns either a list of waypoint dicts or a single dict,
        depending on the endpoint. We take the first waypoint if it's a list.
        """
        if not raw:
            return NormalizedPoint()

        if isinstance(raw, list):
            raw = raw[0] if raw else {}

        if not isinstance(raw, dict):
            return NormalizedPoint()

        town = raw.get("town") or {}
        country = raw.get("country") or town.get("country") or {}

        return NormalizedPoint(
            country=country.get("sign") if isinstance(country, dict) else None,
            city=town.get("name") if isinstance(town, dict) else None,
            postcode=town.get("postCode") if isinstance(town, dict) else None,
        )

    @staticmethod
    def _decimal(value: Any) -> Decimal | None:
        if value is None or value == "":
            return None
        try:
            return Decimal(str(value))
        except Exception:   # noqa: BLE001
            return None
