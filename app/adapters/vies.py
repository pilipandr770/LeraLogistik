"""EU VAT VIES validation adapter.

Uses the official EU Commission VIES REST API — completely free, no API key needed.
Reference: https://ec.europa.eu/taxation_customs/vies/

VIES (VAT Information Exchange System) allows validation of EU company VAT numbers.
Checks that the company is currently registered for VAT in their EU member state.

Trust score contribution:
  VAT valid + company name returned:  +30
  VAT number not found / inactive:     0  (warning shown)
  Country not in EU:                   0  (use ЄДРПОУ instead)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

_VIES_BASE = "https://ec.europa.eu/taxation_customs/vies/rest-api"

# All EU member states as of 2024 (ISO-3166-1 alpha-2)
EU_MEMBER_STATES: frozenset[str] = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
    "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
    "PL", "PT", "RO", "SK", "SI", "ES", "SE",
})


@dataclass
class ViesResult:
    country_code: str
    vat_number: str
    is_valid: bool
    company_name: str | None
    company_address: str | None
    raw: dict = field(default_factory=dict)


class ViesAdapter:
    """Validate EU company VAT numbers via the official VIES REST API."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "ViesAdapter":
        self._client = httpx.AsyncClient(
            base_url=_VIES_BASE,
            timeout=15.0,
            headers={"Accept": "application/json"},
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client:
            await self._client.aclose()

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=5))
    async def validate(self, country_code: str, vat_number: str) -> ViesResult | None:
        """Validate a VAT number via VIES.

        Returns None if:
        - Country is not an EU member state
        - API is unreachable

        Returns a ViesResult with is_valid=False if the number is simply not found.
        """
        country = country_code.upper().strip()
        if country not in EU_MEMBER_STATES:
            log.debug("Country %r not in EU — VIES check not applicable", country)
            return None

        # Strip country prefix if user accidentally included it (e.g. "PL1234567890")
        vat = vat_number.upper().strip()
        if vat.startswith(country):
            vat = vat[len(country):].strip()

        assert self._client is not None  # guaranteed by __aenter__
        try:
            resp = await self._client.get(f"/ms/{country}/vat/{vat}")
            resp.raise_for_status()
            data: dict = resp.json()

            return ViesResult(
                country_code=country,
                vat_number=vat,
                is_valid=data.get("isValid", False),
                company_name=data.get("name"),
                company_address=data.get("address"),
                raw=data,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (400, 404):
                # VIES returns 400 for malformed numbers, treat as "not valid"
                return ViesResult(
                    country_code=country,
                    vat_number=vat,
                    is_valid=False,
                    company_name=None,
                    company_address=None,
                )
            log.warning("VIES API error %s: %s", exc.response.status_code, exc)
            return None
        except Exception:  # noqa: BLE001
            log.warning("VIES lookup failed for %r %r", country, vat, exc_info=True)
            return None
