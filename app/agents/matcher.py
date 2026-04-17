"""Matcher agent.

For each open Load, find the best candidate Vehicles. The heavy lifting is
done in pure Python (geographical + physical feasibility), and only the
top-N viable candidates are sent to Claude for scoring and reasoning.

This keeps LLM spend predictable: we never feed the model hundreds of
obviously-wrong candidates.

Safety
------
This agent does NOT send messages, change exchange state, or spend money.
It only writes rows into the `matches` table. A human (via dashboard) or
a downstream agent decides what to do with them.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from decimal import Decimal

from anthropic import AsyncAnthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Load, LoadStatus, Match, MatchStatus, Vehicle, VehicleStatus

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    vehicle: Vehicle
    rough_distance_km: float
    body_type_ok: bool
    capacity_ok: bool


class MatcherAgent:
    """Suggest vehicle candidates for loads."""

    # How many candidates to prefilter before sending to the LLM.
    MAX_CANDIDATES_TO_SCORE = 10

    def __init__(self, session: AsyncSession, client: AsyncAnthropic | None = None) -> None:
        self._session = session
        settings = get_settings()
        self._client = client or AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model_fast   # Haiku — cheap and fast for scoring

    # ---------- public entry points ----------

    async def match_all_new_loads(self) -> int:
        """Find matches for every Load in status=NEW. Returns total Match rows created."""
        loads = await self._session.scalars(
            select(Load).where(Load.status == LoadStatus.NEW)
        )
        total = 0
        for load in loads.all():
            total += await self.match_load(load)
        return total

    async def match_load(self, load: Load) -> int:
        """Suggest matches for a single load. Returns number of matches created."""
        candidates = await self._prefilter_candidates(load)
        if not candidates:
            log.info("No viable vehicles for load #%d", load.id)
            return 0

        # Score with the LLM
        scored = await self._score_candidates_with_llm(load, candidates)

        created = 0
        for item in scored:
            # Skip if we've already matched this pair
            existing = await self._session.scalar(
                select(Match).where(
                    Match.load_id == load.id, Match.vehicle_id == item["vehicle_id"]
                )
            )
            if existing:
                continue

            match = Match(
                load_id=load.id,
                vehicle_id=item["vehicle_id"],
                status=MatchStatus.PROPOSED,
                score=item["score"],
                reasoning=item["reasoning"],
                deadhead_km=Decimal(str(item["deadhead_km"])) if item.get("deadhead_km") else None,
            )
            self._session.add(match)
            created += 1

        if created:
            load.status = LoadStatus.MATCHED

        await self._session.commit()
        log.info("Load #%d: created %d matches", load.id, created)
        return created

    # ---------- pre-filtering (no LLM) ----------

    async def _prefilter_candidates(self, load: Load) -> list[Candidate]:
        """Hard-filter obviously-incompatible vehicles; rank by rough distance."""
        vehicles = await self._session.scalars(
            select(Vehicle).where(Vehicle.status == VehicleStatus.AVAILABLE)
        )

        viable: list[Candidate] = []
        for v in vehicles.all():
            # Country must match (MVP: only same-direction)
            if load.from_country and v.from_country and load.from_country != v.from_country:
                continue

            body_ok = self._body_type_compatible(load.body_types, v.body_type)
            capacity_ok = self._capacity_ok(load.weight_tons, v.capacity_tons)

            if not (body_ok and capacity_ok):
                continue

            distance = self._rough_distance_km(
                load.from_lat, load.from_lon, v.from_lat, v.from_lon
            )

            viable.append(Candidate(
                vehicle=v,
                rough_distance_km=distance,
                body_type_ok=body_ok,
                capacity_ok=capacity_ok,
            ))

        # Cheaper candidates are closer ones
        viable.sort(key=lambda c: c.rough_distance_km)
        return viable[: self.MAX_CANDIDATES_TO_SCORE]

    @staticmethod
    def _body_type_compatible(load_types: list[str] | None, vehicle_type: str | None) -> bool:
        """If the load specifies body types, the vehicle must match one of them.

        If the load has no restriction, anything fits.
        """
        if not load_types:
            return True
        if not vehicle_type:
            return False
        vt = vehicle_type.lower()
        return any(t.lower() in vt or vt in t.lower() for t in load_types)

    @staticmethod
    def _capacity_ok(load_weight: Decimal | None, vehicle_cap: Decimal | None) -> bool:
        if load_weight is None or vehicle_cap is None:
            return True   # insufficient data -> don't filter out
        return vehicle_cap >= load_weight

    @staticmethod
    def _rough_distance_km(
        lat1: Decimal | None, lon1: Decimal | None,
        lat2: Decimal | None, lon2: Decimal | None,
    ) -> float:
        """Haversine distance. Returns a large number if coordinates missing."""
        if None in (lat1, lon1, lat2, lon2):
            return 9999.0
        # Convert to float
        la1, lo1, la2, lo2 = map(lambda x: math.radians(float(x)), (lat1, lon1, lat2, lon2))
        dlat = la2 - la1
        dlon = lo2 - lo1
        a = math.sin(dlat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlon / 2) ** 2
        return 2 * 6371.0 * math.asin(math.sqrt(a))

    # ---------- LLM scoring ----------

    async def _score_candidates_with_llm(
        self, load: Load, candidates: list[Candidate]
    ) -> list[dict]:
        """Ask Claude to score each (load, vehicle) pair 0..100 with reasoning.

        We ask for a structured JSON response to make parsing reliable.
        """
        prompt = self._build_prompt(load, candidates)

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=2000,
            system=self._system_prompt(),
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text if response.content else ""
        try:
            parsed = json.loads(self._extract_json(text))
        except json.JSONDecodeError as exc:
            log.warning("Matcher LLM returned invalid JSON: %s", exc)
            return []

        return parsed.get("matches", [])

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are a freight-matching assistant for a Ukrainian logistics broker. "
            "You score how well a given vehicle can fulfil a given cargo proposal on a 0-100 scale.\n"
            "\n"
            "Scoring principles:\n"
            " - 80-100: excellent fit (same city or <50 km deadhead, right body type, right capacity)\n"
            " - 50-79: workable (100-300 km deadhead OR slight body-type flexibility needed)\n"
            " - 20-49: weak fit (long deadhead, borderline capacity)\n"
            " - 0-19: poor fit\n"
            "\n"
            "You ALWAYS respond with a single JSON object and nothing else:\n"
            '{"matches": [{"vehicle_id": <int>, "score": <int>, "reasoning": "<short string in Ukrainian>", '
            '"deadhead_km": <number or null>}]}\n'
            "Return one entry per candidate."
        )

    @staticmethod
    def _build_prompt(load: Load, candidates: list[Candidate]) -> str:
        load_block = {
            "id": load.id,
            "from": f"{load.from_country}, {load.from_city}",
            "to": f"{load.to_country}, {load.to_city}",
            "cargo": load.cargo_name,
            "weight_tons": float(load.weight_tons) if load.weight_tons else None,
            "volume_m3": float(load.volume_m3) if load.volume_m3 else None,
            "body_types": load.body_types,
            "is_adr": load.is_adr,
        }
        candidate_blocks = [
            {
                "vehicle_id": c.vehicle.id,
                "from": f"{c.vehicle.from_country}, {c.vehicle.from_city}",
                "to": f"{c.vehicle.to_country}, {c.vehicle.to_city}",
                "body_type": c.vehicle.body_type,
                "capacity_tons": float(c.vehicle.capacity_tons) if c.vehicle.capacity_tons else None,
                "rough_distance_km": round(c.rough_distance_km, 1),
            }
            for c in candidates
        ]

        return (
            f"LOAD:\n{json.dumps(load_block, ensure_ascii=False, indent=2)}\n\n"
            f"CANDIDATE VEHICLES:\n{json.dumps(candidate_blocks, ensure_ascii=False, indent=2)}"
        )

    @staticmethod
    def _extract_json(text: str) -> str:
        """Pull the first {...} block out of the model's text response."""
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            return "{}"
        return text[start : end + 1]
