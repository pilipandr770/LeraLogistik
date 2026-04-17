"""Verification orchestration service.

Runs all available automated checks for a company and computes trust_score.

Scoring model (max 100 points):
  Source                    Check type   Points
  ─────────────────────────────────────────────
  OpenDataBot (UA)          edrpou       +35  (if company found & active)
  EU VIES                   vies         +30  (if VAT valid)
  Email verified            email        +15  (applied via update_email_verified)
  Phone verified (future)   phone        +15
  Profile complete          profile       +5  (logo + description set)

When trust_score >= 35 the company gets is_verified = True (at least one
official registry confirmed it exists).

All checks return None on API failure instead of raising, so a missing API
key or a temporary outage never blocks registration — it just leaves the
company at a lower score flagged for manual review.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.edrpou import EdrpouAdapter
from app.adapters.vies import ViesAdapter
from app.config import get_settings
from app.db.models import Company, VerificationCheck, VerificationStatus

log = logging.getLogger(__name__)

_VERIFIED_THRESHOLD = 35  # minimum trust_score to earn "verified" badge


class VerificationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_all(self, company: Company) -> Company:
        """Run all applicable checks, update company.trust_score in-place.

        Flushes (but does NOT commit) — the caller owns the transaction.
        """
        settings = get_settings()
        total_delta = 0

        # --- 1. ЄДРПОУ check (Ukraine) ---
        if company.edrpou:
            delta, check = await self._check_edrpou(company, settings.opendatabot_api_key)
            self._session.add(check)
            total_delta += delta

        # --- 2. EU VAT / VIES check ---
        if company.vat_number and company.vat_country:
            delta, check = await self._check_vies(company)
            self._session.add(check)
            total_delta += delta

        # --- 3. Email bonus (email_verified flag already set by auth flow) ---
        if company.users and company.users[0].is_email_verified:
            total_delta += 15

        self._apply_score(company, total_delta)
        await self._session.flush()
        return company

    async def update_email_verified(self, company: Company) -> None:
        """Call after user confirms their email to add the email bonus."""
        check = VerificationCheck(
            company_id=company.id,
            check_type="email",
            source="email_confirmation",
            status=VerificationStatus.PASSED,
            score_delta=15,
            details={"email": company.email},
        )
        self._session.add(check)
        self._apply_score(company, 15)
        await self._session.flush()

    # ------------------------------------------------------------------
    # Internal check runners
    # ------------------------------------------------------------------

    async def _check_edrpou(
        self, company: Company, api_key: str
    ) -> tuple[int, VerificationCheck]:
        async with EdrpouAdapter(api_key=api_key) as adapter:
            result = await adapter.lookup(company.edrpou or "")

        if result is None:
            return 0, VerificationCheck(
                company_id=company.id,
                check_type="edrpou",
                source="opendatabot",
                status=VerificationStatus.MANUAL_REQUIRED,
                score_delta=0,
                details={"reason": "API unavailable or key not configured"},
            )

        if result.is_active:
            # Auto-populate company fields from registry data
            if not company.legal_name and result.legal_name:
                company.legal_name = result.legal_name
            if not company.address and result.address:
                company.address = result.address

            return 35, VerificationCheck(
                company_id=company.id,
                check_type="edrpou",
                source="opendatabot",
                status=VerificationStatus.PASSED,
                score_delta=35,
                details={
                    "legal_name": result.legal_name,
                    "director": result.director,
                    "address": result.address,
                    "registration_date": result.registration_date,
                    "activity": result.activity_type,
                },
                raw_response=result.raw,
            )

        # Company exists in registry but is inactive (liquidated / bankrupt)
        return 0, VerificationCheck(
            company_id=company.id,
            check_type="edrpou",
            source="opendatabot",
            status=VerificationStatus.FAILED,
            score_delta=0,
            details={"reason": "Company is not active in the Ukrainian registry"},
            raw_response=result.raw,
        )

    async def _check_vies(self, company: Company) -> tuple[int, VerificationCheck]:
        async with ViesAdapter() as adapter:
            result = await adapter.validate(
                company.vat_country or "", company.vat_number or ""
            )

        if result is None:
            return 0, VerificationCheck(
                company_id=company.id,
                check_type="vies",
                source="vies_api",
                status=VerificationStatus.MANUAL_REQUIRED,
                score_delta=0,
                details={"reason": "VIES API unavailable or country not in EU"},
            )

        if result.is_valid:
            if not company.legal_name and result.company_name:
                company.legal_name = result.company_name

            return 30, VerificationCheck(
                company_id=company.id,
                check_type="vies",
                source="vies_api",
                status=VerificationStatus.PASSED,
                score_delta=30,
                details={
                    "company_name": result.company_name,
                    "address": result.company_address,
                },
                raw_response=result.raw,
            )

        return 0, VerificationCheck(
            company_id=company.id,
            check_type="vies",
            source="vies_api",
            status=VerificationStatus.FAILED,
            score_delta=0,
            details={"reason": "VAT number not found or inactive in VIES"},
            raw_response=result.raw,
        )

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _apply_score(self, company: Company, delta: int) -> None:
        company.trust_score = min(100, company.trust_score + delta)
        if company.trust_score >= _VERIFIED_THRESHOLD and not company.is_verified:
            company.is_verified = True
            company.verified_at = datetime.now(timezone.utc)
            log.info(
                "Company %r (id=%s) reached verified status (score=%s)",
                company.name,
                company.id,
                company.trust_score,
            )
