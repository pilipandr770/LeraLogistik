"""Navixy GPS telematics adapter.

Navixy is a popular fleet telematics platform in Ukraine / CIS.
REST API v3 docs: https://developers.navixy.com/api-reference/

Authentication:
    POST /v3/user/auth  →  {"hash": "<session_hash>"}
    Every subsequent request sends  ?hash=<session_hash>  as a query param.

Session hash expires in 24 h of inactivity.  We obtain it once per
TelematicsAccount and cache it in the account record (hash field).

Environment variables (optional — only needed when carrier links their account):
    NAVIXY_API_BASE  default https://api.eu.navixy.com/v2
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import httpx

log = logging.getLogger(__name__)

_DEFAULT_BASE = "https://api.eu.navixy.com/v2"
_TIMEOUT = 15.0


@dataclass
class NavixyTracker:
    """Lightweight representation of one tracker/vehicle in Navixy."""

    tracker_id: int
    label: str               # user-defined vehicle label
    source_id: int
    is_blocked: bool


@dataclass
class NavixyPosition:
    """Last known position of a single tracker."""

    tracker_id: int
    lat: float
    lng: float
    speed: float             # km/h
    heading: int             # degrees 0-359
    satellite_count: int
    recorded_at: datetime
    address: Optional[str] = None  # reverse-geocoded address if available


class NavixyAdapter:
    """Adapter for the Navixy REST API v2 (fleet telematics).

    Usage
    -----
    async with NavixyAdapter(api_base) as navixy:
        hash_ = await navixy.authenticate(login, password)
        trackers = await navixy.list_trackers(hash_)
        positions = await navixy.get_last_positions(hash_, [t.tracker_id for t in trackers])
    """

    def __init__(self, api_base: str = _DEFAULT_BASE) -> None:
        self._base = api_base.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Context manager helpers
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "NavixyAdapter":
        self._client = httpx.AsyncClient(
            base_url=self._base,
            timeout=_TIMEOUT,
            headers={"Content-Type": "application/json"},
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def authenticate(self, login: str, password: str) -> str | None:
        """Return a session hash on success, None on failure.

        POST /user/auth  { login, password }  →  { hash }
        """
        try:
            resp = await self._get_client().post(
                "/user/auth",
                json={"login": login, "password": password},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("success"):
                return data["hash"]
            log.warning("Navixy auth failed for %s: %s", login, data.get("status"))
            return None
        except httpx.HTTPError as exc:
            log.error("Navixy authenticate HTTP error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Tracker listing
    # ------------------------------------------------------------------

    async def list_trackers(self, session_hash: str) -> list[NavixyTracker]:
        """Return all trackers (vehicles) visible to this account.

        POST /tracker/list  →  list[tracker]
        """
        try:
            resp = await self._get_client().post(
                "/tracker/list",
                json={"hash": session_hash},
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success"):
                log.warning("Navixy tracker/list returned failure: %s", data.get("status"))
                return []
            return [
                NavixyTracker(
                    tracker_id=t["id"],
                    label=t.get("label", ""),
                    source_id=t.get("source", {}).get("id", 0),
                    is_blocked=t.get("is_blocked", False),
                )
                for t in data.get("list", [])
            ]
        except httpx.HTTPError as exc:
            log.error("Navixy list_trackers HTTP error: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Last known positions (bulk)
    # ------------------------------------------------------------------

    async def get_last_positions(
        self, session_hash: str, tracker_ids: list[int]
    ) -> dict[int, NavixyPosition]:
        """Return a mapping tracker_id → NavixyPosition for all requested ids.

        POST /tracker/get_last_gps_point  (one call per tracker — Navixy v2 limitation)
        We fan-out requests concurrently.
        """
        if not tracker_ids:
            return {}

        import asyncio

        results: dict[int, NavixyPosition] = {}

        async def _fetch_one(tid: int) -> None:
            pos = await self._get_single_position(session_hash, tid)
            if pos:
                results[tid] = pos

        await asyncio.gather(*[_fetch_one(tid) for tid in tracker_ids])
        return results

    async def _get_single_position(
        self, session_hash: str, tracker_id: int
    ) -> NavixyPosition | None:
        """Fetch last GPS point for one tracker."""
        try:
            resp = await self._get_client().post(
                "/tracker/get_last_gps_point",
                json={"hash": session_hash, "tracker_id": tracker_id},
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success"):
                return None
            pt = data.get("last_point", {})
            if not pt.get("lat"):
                return None
            recorded_raw = pt.get("get_time") or pt.get("server_time", "")
            recorded_at = _parse_navixy_ts(recorded_raw)
            return NavixyPosition(
                tracker_id=tracker_id,
                lat=float(pt["lat"]),
                lng=float(pt["lng"]),
                speed=float(pt.get("speed", 0)),
                heading=int(pt.get("heading", 0)),
                satellite_count=int(pt.get("satellites", 0)),
                recorded_at=recorded_at,
                address=pt.get("address"),
            )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.debug("Navixy _get_single_position error for tracker %d: %s", tracker_id, exc)
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(
                "NavixyAdapter must be used as an async context manager: "
                "async with NavixyAdapter() as navixy: ..."
            )
        return self._client


def _parse_navixy_ts(raw: str) -> datetime:
    """Parse Navixy timestamp strings like '2024-01-15 10:30:45' into datetime."""
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return datetime.utcnow()
