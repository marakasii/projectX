"""Synthetic traveller and manually entered segments; no live booking or AI."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.common.models import ApplicantProfile, Route
from app.itinerary.registry import TransitRuleRegistry

NOW = datetime(2026, 10, 25, 14, 10, tzinfo=timezone.utc)


@pytest.fixture
def registry():
    return TransitRuleRegistry()


@pytest.fixture
def profile():
    return ApplicantProfile(
        id=uuid4(), revision=1, given_name="ALEXEI", surname="SAMPLEOV", birth_date="1996-03-14",
        citizenship="RU", residence_country="RU", has_other_citizenship=False, has_foreign_residence_permit=False,
        passport_kind="biometric", passport_issued_on="2022-04-15", passport_expires_on="2032-04-15",
    )


@pytest.fixture
def route_factory():
    def make(country="TR", airport="IST"):
        return Route.model_validate({
            "legs": [
                {"departure_country": "RU", "arrival_country": country, "departure_airport": "DME", "arrival_airport": airport,
                 "departure_at": "2026-11-01T06:00:00Z", "arrival_at": "2026-11-01T10:00:00Z"},
                {"departure_country": country, "arrival_country": "FR", "departure_airport": airport, "arrival_airport": "CDG",
                 "departure_at": "2026-11-01T12:00:00Z", "arrival_at": "2026-11-01T15:00:00Z"},
            ],
            "transfers": [
                {"country": country, "airport": airport, "arrival_at": "2026-11-01T10:00:00Z",
                 "departure_at": "2026-11-01T12:00:00Z", "ticket_type": "single", "leaves_airside": False,
                 "rechecks_baggage": False, "permissions": None},
            ],
        })
    return make
