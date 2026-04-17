"""Unit tests for the Lardi adapter.

These test only the pure-Python normalization logic — no network calls.
We feed in a sample Lardi response and verify the NormalizedLoad output.
"""

from __future__ import annotations

from decimal import Decimal

from app.adapters.lardi import LardiAdapter


SAMPLE_CARGO = {
    "id": 12345,
    "cargoName": "Яблука свіжі",
    "waypointListSource": [
        {"country": {"sign": "UA"}, "town": {"name": "Київ", "postCode": "01001"}}
    ],
    "waypointListTarget": [
        {"country": {"sign": "PL"}, "town": {"name": "Warszawa", "postCode": "00-001"}}
    ],
    "size": {"weight": 20.0, "volume": 86.0},
    "bodyTypes": [{"name": "refrigerator"}, {"name": "tent"}],
    "payment": {"price": 45000, "currency": {"sign": "UAH"}, "vat": True},
    "contact": {"id": 9876, "name": "Petro Ivanchuk", "phone": {"number": "+380501234567"}},
}

SAMPLE_LORRY = {
    "id": 67890,
    "waypointListSource": [{"country": {"sign": "UA"}, "town": {"name": "Львів"}}],
    "waypointListTarget": [{"country": {"sign": "DE"}, "town": {"name": "Berlin"}}],
    "size": {"weight": 22.0, "volume": 90.0},
    "bodyTypes": [{"name": "tent"}],
    "contact": {"id": 1234, "name": "ТОВ Шляхбуд", "phone": {"number": "+380671112233"}},
}


def test_normalize_cargo():
    adapter = LardiAdapter.__new__(LardiAdapter)
    adapter.source = "lardi"
    result = adapter._to_normalized_load(SAMPLE_CARGO)

    assert result.source == "lardi"
    assert result.external_id == "12345"
    assert result.cargo_name == "Яблука свіжі"
    assert result.origin.country == "UA"
    assert result.origin.city == "Київ"
    assert result.destination.country == "PL"
    assert result.destination.city == "Warszawa"
    assert result.weight_tons == Decimal("20.0")
    assert result.volume_m3 == Decimal("86.0")
    assert "refrigerator" in result.body_types
    assert result.price_amount == Decimal("45000")
    assert result.price_currency == "UAH"
    assert result.owner_name == "Petro Ivanchuk"
    assert result.owner_phone == "+380501234567"
    assert result.owner_external_id == 9876


def test_normalize_vehicle():
    adapter = LardiAdapter.__new__(LardiAdapter)
    adapter.source = "lardi"
    result = adapter._to_normalized_vehicle(SAMPLE_LORRY)

    assert result.source == "lardi"
    assert result.external_id == "67890"
    assert result.origin.country == "UA"
    assert result.origin.city == "Львів"
    assert result.destination.country == "DE"
    assert result.body_type == "tent"
    assert result.capacity_tons == Decimal("22.0")
    assert result.carrier_name == "ТОВ Шляхбуд"
    assert result.carrier_phone == "+380671112233"


def test_empty_waypoints_dont_crash():
    adapter = LardiAdapter.__new__(LardiAdapter)
    adapter.source = "lardi"
    result = adapter._to_normalized_load({"id": 1})
    assert result.origin.country is None
    assert result.destination.country is None


def test_build_search_body():
    from app.adapters.base import SearchFilter

    flt = SearchFilter(
        countries_from=["UA"],
        countries_to=["PL", "DE"],
        per_page=30,
    )
    body = LardiAdapter._build_search_body(flt)
    assert body["filter"]["directionFrom"] == [{"countrySign": "UA"}]
    assert {"countrySign": "PL"} in body["filter"]["directionTo"]
    assert body["options"]["perPage"] == 30
