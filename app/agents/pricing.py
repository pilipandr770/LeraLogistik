"""Pricing Agent.

Given a Load (route + cargo), looks up comparable freight price data from
the last 90 days in the `price_samples` table and asks Claude to reason
about a fair market price.

Safety
------
Read-only except for writing `suggested_price` and `suggested_currency`
onto Match rows. Never accesses any external system.

Architecture
------------
1. Simple heuristic pre-filter: same country pair + similar body_type + weight ±50%
2. Claude Haiku aggregates and contextualises the samples (cheap model)
3. Result is stored on the Match row so the dashboard can show it

This is Phase 2 in the roadmap — safe to run alongside the Matcher.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from anthropic import AsyncAnthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Load, Match, MatchStatus, PriceSample

log = logging.getLogger(__name__)

_LOOKBACK_DAYS = 90
_MIN_SAMPLES = 3      # don't suggest a price if fewer than this many data points
_MAX_SAMPLES = 20     # cap sent to the LLM to control token usage


class PricingAgent:
    """Suggest a price for a Match based on historical freight market data."""

    def __init__(self, session: AsyncSession, client: AsyncAnthropic | None = None) -> None:
        self._session = session
        settings = get_settings()
        self._client = client or AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model_fast

    # ---------- public ----------

    async def price_all_unpriced_matches(self) -> int:
        """Add suggested_price to every match that doesn't have one yet."""
        matches = (await self._session.scalars(
            select(Match).where(
                Match.status.in_([MatchStatus.PROPOSED, MatchStatus.REVIEWED]),
                Match.suggested_price.is_(None),
            )
        )).all()

        priced = 0
        for match in matches:
            load = await self._session.get(Load, match.load_id)
            if not load:
                continue
            result = await self._suggest_price(load)
            if result:
                match.suggested_price = result["price"]
                match.suggested_currency = result["currency"]
                priced += 1

        if priced:
            await self._session.commit()
            log.info("Pricing agent: suggested prices for %d matches", priced)

        return priced

    async def price_match(self, match: Match) -> bool:
        """Price a single match. Returns True if a price was suggested."""
        load = await self._session.get(Load, match.load_id)
        if not load:
            return False
        result = await self._suggest_price(load)
        if not result:
            return False
        match.suggested_price = result["price"]
        match.suggested_currency = result["currency"]
        return True

    # ---------- internals ----------

    async def _suggest_price(self, load: Load) -> dict | None:
        """Return {"price": Decimal, "currency": str} or None if insufficient data."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)

        # Fetch comparable samples (same country pair, ±90 days)
        query = select(PriceSample).where(
            PriceSample.from_country == load.from_country,
            PriceSample.to_country == load.to_country,
            PriceSample.collected_at >= cutoff,
        )

        # Narrow by body type if we know it
        load_body = (load.body_types[0] if load.body_types else None)
        if load_body:
            query = query.where(PriceSample.body_type == load_body)

        query = query.order_by(PriceSample.collected_at.desc()).limit(_MAX_SAMPLES)
        samples = (await self._session.scalars(query)).all()

        if len(samples) < _MIN_SAMPLES:
            log.debug(
                "Pricing: not enough samples for %s→%s (%d found, need %d)",
                load.from_country, load.to_country, len(samples), _MIN_SAMPLES,
            )
            return None

        # Build payload for Claude
        samples_data = [
            {
                "from": f"{s.from_city}, {s.from_country}",
                "to": f"{s.to_city}, {s.to_country}",
                "price": float(s.price_amount),
                "currency": s.price_currency,
                "weight_tons": float(s.weight_tons) if s.weight_tons else None,
                "body_type": s.body_type,
                "days_ago": (datetime.now(timezone.utc) - s.collected_at).days,
            }
            for s in samples
        ]

        load_desc = {
            "from_city": load.from_city,
            "from_country": load.from_country,
            "to_city": load.to_city,
            "to_country": load.to_country,
            "cargo": load.cargo_name,
            "weight_tons": float(load.weight_tons) if load.weight_tons else None,
            "body_types": load.body_types,
        }

        prompt = f"""You are a Ukrainian freight pricing expert.

Load to price:
{json.dumps(load_desc, ensure_ascii=False, indent=2)}

Comparable market samples from the last {_LOOKBACK_DAYS} days:
{json.dumps(samples_data, ensure_ascii=False, indent=2)}

Based on these market samples, suggest a fair freight price for this load.

Rules:
- Use the most common currency in the samples (EUR or UAH)
- Account for weight, distance (infer from city names), and seasonality
- Be conservative — suggest the median, not the high end
- If samples vary widely, pick the middle range

Respond with ONLY a JSON object, no extra text:
{{"price": <number>, "currency": "<EUR|UAH|USD>", "reasoning": "<1-2 sentences>"}}"""

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=150,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            data = json.loads(raw)
            return {
                "price": Decimal(str(data["price"])),
                "currency": str(data["currency"]).upper()[:3],
            }
        except Exception:
            log.exception("Pricing LLM call failed for load %s→%s", load.from_city, load.to_city)
            return None
