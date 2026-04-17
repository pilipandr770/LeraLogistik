"""Tests for the non-LLM parts of the Matcher agent."""

from __future__ import annotations

from decimal import Decimal

from app.agents.matcher import MatcherAgent


def test_body_type_compatible():
    # No restriction on load -> anything fits
    assert MatcherAgent._body_type_compatible(None, "tent") is True
    assert MatcherAgent._body_type_compatible([], "tent") is True

    # Load demands refrigerator, vehicle has tent -> no
    assert MatcherAgent._body_type_compatible(["refrigerator"], "tent") is False

    # Exact match
    assert MatcherAgent._body_type_compatible(["tent"], "tent") is True

    # Case-insensitive substring match
    assert MatcherAgent._body_type_compatible(["Refrigerator"], "refrigerator") is True

    # Vehicle with no body type -> can't claim to fit a demand
    assert MatcherAgent._body_type_compatible(["tent"], None) is False


def test_capacity_ok():
    # Unknown values -> don't filter out
    assert MatcherAgent._capacity_ok(None, Decimal("20")) is True
    assert MatcherAgent._capacity_ok(Decimal("20"), None) is True

    # Vehicle can carry more -> ok
    assert MatcherAgent._capacity_ok(Decimal("18"), Decimal("22")) is True

    # Vehicle too small -> not ok
    assert MatcherAgent._capacity_ok(Decimal("25"), Decimal("20")) is False


def test_rough_distance_km():
    # Kyiv to Lviv is ~470 km by haversine
    d = MatcherAgent._rough_distance_km(
        Decimal("50.4501"), Decimal("30.5234"),
        Decimal("49.8397"), Decimal("24.0297"),
    )
    assert 400 < d < 550

    # Missing coords -> sentinel
    assert MatcherAgent._rough_distance_km(None, None, Decimal("1"), Decimal("1")) == 9999.0
