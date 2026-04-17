"""Ukrainian company registry (ЄДРПОУ) adapter.

Uses OpenDataBot API (https://opendatabot.ua/api) when OPENDATABOT_API_KEY is set.
Falls back gracefully to a "manual check required" signal when the key is absent.

Trust score contribution:
  company found + active:    +35
  company found + inactive:   0  (warning shown to user)
  key not set / API down:     0  (manual_required)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

_OPENDATABOT_BASE = "https://api.opendatabot.ua"

# Status values that OpenDataBot returns for active companies
_ACTIVE_STATUSES = {"зареєстровано", "registered", "active"}


@dataclass
class EdrpouResult:
    edrpou: str
    legal_name: str | None
    short_name: str | None
    is_active: bool | None          # None means "could not determine"
    registration_date: str | None   # ISO date string from registry
    director: str | None
    address: str | None
    activity_type: str | None       # КВЕД — main business activity
    raw: dict = field(default_factory=dict)


class EdrpouAdapter:
    """Look up Ukrainian companies by their ЄДРПОУ (8-10 digit tax ID)."""

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "EdrpouAdapter":
        self._client = httpx.AsyncClient(
            base_url=_OPENDATABOT_BASE,
            timeout=10.0,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client:
            await self._client.aclose()

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4))
    async def lookup(self, edrpou: str) -> EdrpouResult | None:
        """Return company info, or None if not found / API unavailable.

        Returns None (not raises) for all error paths so callers don't need
        to catch exceptions — they just treat None as "manual check needed".
        """
        if not self._api_key:
            log.warning("OPENDATABOT_API_KEY not set — skipping ЄДРПОУ check")
            return None

        code = edrpou.strip()
        if not code.isdigit() or not (8 <= len(code) <= 10):
            log.debug("Invalid ЄДРПОУ format: %r", edrpou)
            return None

        code = code.zfill(8)

        assert self._client is not None  # guaranteed by __aenter__
        try:
            resp = await self._client.get(
                f"/v2/company/{code}",
                params={"token": self._api_key},
            )
            resp.raise_for_status()
            data: dict = resp.json()
            company: dict = data.get("data", {})
            status_raw: str = company.get("status", "").lower()

            return EdrpouResult(
                edrpou=code,
                legal_name=company.get("name"),
                short_name=company.get("shortName"),
                is_active=status_raw in _ACTIVE_STATUSES,
                registration_date=company.get("registrationDate"),
                director=company.get("director"),
                address=company.get("address"),
                activity_type=company.get("activity"),
                raw=data,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None  # company simply not found
            log.warning("OpenDataBot API error %s: %s", exc.response.status_code, exc)
            return None
        except Exception:  # noqa: BLE001
            log.warning("ЄДРПОУ lookup failed for %r", code, exc_info=True)
            return None
