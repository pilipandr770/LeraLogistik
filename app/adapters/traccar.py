"""Traccar GPS tracking adapter.

Traccar is an open-source GPS tracking server (Apache 2.0).
We self-host it via Docker — no external SaaS dependency.

GitHub:  https://github.com/traccar/traccar
API:     https://www.traccar.org/traccar-api/
OpenAPI: http://localhost:8082/api/swagger  (when running locally)

Architecture on our platform
-----------------------------
1. We run Traccar in Docker (see docker-compose.yml).
2. Each carrier company gets ONE Traccar user account (created by us when
   they register their fleet).
3. Each vehicle gets ONE Traccar "device" — identified by a unique
   identifier (IMEI, phone number, or any string for the mobile app).
4. The carrier installs "Traccar Client" on their phone OR fits a hardware
   GPS tracker (Teltonika FMB, GT06, Queclink, etc.) in the truck.
5. Our APScheduler polls Traccar every 30s → writes VehiclePosition rows.

Authentication
--------------
Traccar uses HTTP Basic auth (email:password) for all API calls.
We keep ONE admin account for our server operations, plus per-company
accounts for the carrier's own login to the Traccar UI if they want it.

The admin credentials live in .env:
    TRACCAR_BASE_URL      = http://localhost:8082
    TRACCAR_ADMIN_EMAIL   = admin@trucklink.ua
    TRACCAR_ADMIN_PASSWORD= change-me
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import httpx

log = logging.getLogger(__name__)

_TIMEOUT = 15.0
_POSITIONS_BATCH = 100   # Traccar supports up to ??? per request; 100 is safe


@dataclass
class TraccarDevice:
    """One GPS device registered in Traccar."""
    device_id: int          # Traccar internal ID
    name: str               # human label, e.g. "Volvo FH-2021 АА1234КВ"
    unique_id: str          # IMEI, phone or any unique string
    status: str             # "online" | "offline" | "unknown"
    last_update: Optional[datetime] = None
    group_id: Optional[int] = None   # optional carrier group


@dataclass
class TraccarPosition:
    """Latest GPS fix for one device, as returned by Traccar REST API."""
    device_id: int
    lat: float
    lon: float
    speed: float            # knots (Traccar native unit!)
    speed_kmh: float        # converted
    course: int             # heading degrees 0-359
    altitude: float
    accuracy: float
    address: Optional[str]
    fixed_at: datetime      # GPS fix timestamp
    server_time: datetime   # when Traccar received it


@dataclass
class TraccarGroup:
    """Device group — we create one per carrier company."""
    group_id: int
    name: str


class TraccarAdapter:
    """Adapter for the self-hosted Traccar REST API.

    Usage
    -----
    async with TraccarAdapter(base_url, email, password) as tc:
        devices = await tc.list_devices()
        positions = await tc.get_positions([d.device_id for d in devices])
        new_dev = await tc.create_device("Volvo АА1234КВ", imei="123456789012345")

    The adapter is intentionally read-heavy on the position side
    (used by our poller) and write-light (device registration is manual).
    """

    def __init__(self, base_url: str, email: str, password: str) -> None:
        self._base = base_url.rstrip("/")
        self._auth = (email, password)
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "TraccarAdapter":
        self._client = httpx.AsyncClient(
            base_url=self._base,
            auth=self._auth,
            timeout=_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Server info (health check)
    # ------------------------------------------------------------------

    async def server_info(self) -> dict:
        """GET /api/server — returns Traccar server info. Good for health checks."""
        try:
            r = await self._c().get("/api/server")
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            log.error("Traccar server_info failed: %s", e)
            return {}

    # ------------------------------------------------------------------
    # Devices
    # ------------------------------------------------------------------

    async def list_devices(self, group_id: int | None = None) -> list[TraccarDevice]:
        """GET /api/devices — list all devices (optionally filtered by group)."""
        params: dict = {}
        if group_id:
            params["groupId"] = group_id
        try:
            r = await self._c().get("/api/devices", params=params)
            r.raise_for_status()
            return [_parse_device(d) for d in r.json()]
        except httpx.HTTPError as e:
            log.error("Traccar list_devices failed: %s", e)
            return []

    async def create_device(
        self,
        name: str,
        unique_id: str,
        group_id: int | None = None,
    ) -> TraccarDevice | None:
        """POST /api/devices — register a new GPS device.

        unique_id is the IMEI for hardware trackers, or any alphanumeric
        string for the Traccar Client (phone) app.
        """
        payload: dict = {"name": name, "uniqueId": unique_id}
        if group_id:
            payload["groupId"] = group_id
        try:
            r = await self._c().post("/api/devices", json=payload)
            r.raise_for_status()
            return _parse_device(r.json())
        except httpx.HTTPError as e:
            log.error("Traccar create_device failed: %s", e)
            return None

    async def delete_device(self, device_id: int) -> bool:
        """DELETE /api/devices/{id}"""
        try:
            r = await self._c().delete(f"/api/devices/{device_id}")
            return r.status_code == 204
        except httpx.HTTPError:
            return False

    # ------------------------------------------------------------------
    # Positions (the hot path — called every 30s by our scheduler)
    # ------------------------------------------------------------------

    async def get_positions(
        self, device_ids: list[int]
    ) -> dict[int, TraccarPosition]:
        """GET /api/positions — fetch latest position for each device.

        Returns mapping device_id → TraccarPosition.
        Traccar returns the most recent stored position per device.
        For real-time, subscribe via WebSocket (see subscribe_realtime).
        """
        if not device_ids:
            return {}

        # Traccar accepts ?deviceId=1&deviceId=2&... (repeated params)
        params = [("deviceId", str(did)) for did in device_ids]
        try:
            r = await self._c().get("/api/positions", params=params)
            r.raise_for_status()
            result: dict[int, TraccarPosition] = {}
            for raw in r.json():
                pos = _parse_position(raw)
                if pos:
                    result[pos.device_id] = pos
            return result
        except httpx.HTTPError as e:
            log.error("Traccar get_positions failed: %s", e)
            return {}

    async def get_position_history(
        self,
        device_id: int,
        from_dt: datetime,
        to_dt: datetime,
    ) -> list[TraccarPosition]:
        """GET /api/positions with from/to for route replay."""
        params = {
            "deviceId": device_id,
            "from": from_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": to_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            r = await self._c().get("/api/positions", params=params)
            r.raise_for_status()
            return [p for raw in r.json() if (p := _parse_position(raw))]
        except httpx.HTTPError as e:
            log.error("Traccar get_position_history failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Groups (one per carrier company)
    # ------------------------------------------------------------------

    async def create_group(self, name: str) -> TraccarGroup | None:
        """POST /api/groups — create a device group for a carrier company."""
        try:
            r = await self._c().post("/api/groups", json={"name": name})
            r.raise_for_status()
            data = r.json()
            return TraccarGroup(group_id=data["id"], name=data["name"])
        except httpx.HTTPError as e:
            log.error("Traccar create_group failed: %s", e)
            return None

    async def list_groups(self) -> list[TraccarGroup]:
        try:
            r = await self._c().get("/api/groups")
            r.raise_for_status()
            return [TraccarGroup(group_id=g["id"], name=g["name"]) for g in r.json()]
        except httpx.HTTPError as e:
            log.error("Traccar list_groups failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Users (per-company Traccar accounts — optional, for carrier UI access)
    # ------------------------------------------------------------------

    async def create_user(
        self,
        name: str,
        email: str,
        password: str,
        readonly: bool = True,
    ) -> dict | None:
        """POST /api/users — create a read-only Traccar account for a carrier."""
        payload = {
            "name": name,
            "email": email,
            "password": password,
            "readonly": readonly,
            "disabled": False,
        }
        try:
            r = await self._c().post("/api/users", json=payload)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            log.error("Traccar create_user failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _c(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(
                "TraccarAdapter must be used as async context manager"
            )
        return self._client


# ------------------------------------------------------------------
# Parsing helpers
# ------------------------------------------------------------------

def _parse_device(data: dict) -> TraccarDevice:
    last_update = None
    if raw_ts := data.get("lastUpdate"):
        try:
            last_update = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except ValueError:
            pass
    return TraccarDevice(
        device_id=data["id"],
        name=data.get("name", ""),
        unique_id=data.get("uniqueId", ""),
        status=data.get("status", "unknown"),
        last_update=last_update,
        group_id=data.get("groupId") or None,
    )


def _parse_position(data: dict) -> TraccarPosition | None:
    try:
        speed_knots = float(data.get("speed", 0))
        speed_kmh = round(speed_knots * 1.852, 1)

        fixed_at = datetime.fromisoformat(
            data["fixTime"].replace("Z", "+00:00")
        )
        server_time = datetime.fromisoformat(
            data["serverTime"].replace("Z", "+00:00")
        )
        return TraccarPosition(
            device_id=data["deviceId"],
            lat=float(data["latitude"]),
            lon=float(data["longitude"]),
            speed=speed_knots,
            speed_kmh=speed_kmh,
            course=int(data.get("course", 0)),
            altitude=float(data.get("altitude", 0)),
            accuracy=float(data.get("accuracy", 0)),
            address=data.get("address"),
            fixed_at=fixed_at,
            server_time=server_time,
        )
    except (KeyError, ValueError, TypeError) as e:
        log.debug("Traccar _parse_position error: %s | raw: %s", e, data)
        return None
