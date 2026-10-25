from datetime import datetime, timedelta, timezone

import pytest

from app.common.models import ExistingPermission, Route
from app.itinerary.engine import check_route, validate_route
from .conftest import NOW


def evaluate(profile, route, registry):
    return check_route(profile, route, registry.get_ruleset(registry.transit_version, as_of=NOW.date()), evaluated_at=NOW)


def codes(result):
    return {finding.code for finding in result.findings}


@pytest.mark.parametrize("country,airport", [("TR", "IST"), ("AE", "DXB")])
def test_scoped_airside_uses_real_official_rules(profile, route_factory, registry, country, airport):
    route = route_factory(country, airport)
    before = (profile.model_dump(), route.model_dump(), registry.get_ruleset(registry.transit_version).model_dump())
    result = evaluate(profile, route, registry)
    assert result.status == "no_known_blockers" and result.covered_countries == [country]
    assert result.uncovered_countries == [] and result.flight_availability == "not_checked"
    finding = next(f for f in result.findings if f.code == "airside_visa_not_required")
    assert finding.rule_ref and finding.source_refs and finding.origin == "rule"
    assert all(f.severity == "info" for f in result.findings)
    assert before == (profile.model_dump(), route.model_dump(), registry.get_ruleset(registry.transit_version).model_dump())


@pytest.mark.parametrize("key", ["airport", "arrival_at", "departure_at", "ticket_type", "leaves_airside", "rechecks_baggage"])
def test_null_never_means_safe_or_false(profile, route_factory, registry, key):
    route = route_factory()
    setattr(route.transfers[0], key, None)
    result = evaluate(profile, route, registry)
    assert result.status == "needs_verification" and "airside_visa_not_required" not in codes(result)
    assert result.uncovered_countries == ["TR"]
    assert any(f"route.transfers.0.{key}" in f.field_paths for f in result.findings)


@pytest.mark.parametrize("country,airport", [("DE", "FRA"), ("TR", "SAW"), ("AE", "AUH")])
def test_country_does_not_imply_every_airport_is_covered(profile, route_factory, registry, country, airport):
    result = evaluate(profile, route_factory(country, airport), registry)
    assert result.status == "needs_verification" and result.uncovered_countries == [country]
    assert result.covered_countries == [] and "transit_scope_uncovered" in codes(result)


@pytest.mark.parametrize("key", ["citizenship", "residence_country", "has_other_citizenship", "has_foreign_residence_permit", "passport_kind", "birth_date"])
def test_incomplete_supported_profile(profile, route_factory, registry, key):
    setattr(profile, key, None)
    result = evaluate(profile, route_factory(), registry)
    assert result.status == "needs_verification" and "transit_profile_scope" in codes(result)
    assert "airside_visa_not_required" not in codes(result)


@pytest.mark.parametrize("key,value", [("citizenship", "US"), ("residence_country", "AE"), ("has_other_citizenship", True),
                                       ("has_foreign_residence_permit", True), ("birth_date", "2016-01-01")])
def test_unsupported_profile_does_not_receive_ru_rule(profile, route_factory, registry, key, value):
    profile = type(profile).model_validate({**profile.model_dump(mode="json"), key: value})
    result = evaluate(profile, route_factory(), registry)
    assert result.status == "needs_verification" and result.covered_countries == []


@pytest.mark.parametrize("country,airport", [("TR", "IST"), ("AE", "DXB")])
def test_separate_tickets_stay_unknown_even_with_false_baggage(profile, route_factory, registry, country, airport):
    route = route_factory(country, airport)
    route.transfers[0].ticket_type = "separate"
    result = evaluate(profile, route, registry)
    assert result.status == "needs_verification" and "separate_tickets_unverified" in codes(result)
    if country == "AE":
        assert "dxb_transfer_procedure" in codes(result)


@pytest.mark.parametrize("country,airport", [("TR", "IST"), ("AE", "DXB")])
def test_landside_keeps_uncovered_history_and_entry_conditions(profile, route_factory, registry, country, airport):
    route = route_factory(country, airport)
    route.transfers[0].leaves_airside = True
    result = evaluate(profile, route, registry)
    assert result.status == "needs_verification" and result.uncovered_countries == [country]
    assert "airside_visa_not_required" not in codes(result)
    assert "entry_history_unverified" in codes(result) if country == "AE" else "entry_conditions_uncovered" in codes(result)


@pytest.mark.parametrize("expiry,blocked", [("2027-04-30", True), ("2027-05-01", False)])
def test_known_ae_entry_passport_rule_only_for_confirmed_exit(profile, route_factory, registry, expiry, blocked):
    route = route_factory("AE", "DXB")
    route.transfers[0].leaves_airside = True
    profile.passport_expires_on = datetime.fromisoformat(expiry).date()
    result = evaluate(profile, route, registry)
    assert (result.status == "blocked") is blocked
    assert ("ae_entry_passport_validity" in codes(result)) is blocked
    if blocked:
        finding = next(f for f in result.findings if f.code == "ae_entry_passport_validity")
        assert finding.source_refs == ["AE_MOFA_MOSCOW"] and finding.rule_ref.version == registry.transit_version


def test_baggage_inference_does_not_create_formal_entry_barrier(profile, route_factory, registry):
    route = route_factory("AE", "DXB")
    route.transfers[0].rechecks_baggage = True
    profile.passport_expires_on = datetime(2027, 1, 1).date()
    result = evaluate(profile, route, registry)
    assert result.status == "needs_verification" and "transfer_airside_conflict" in codes(result)
    assert "ae_entry_passport_validity" not in codes(result)


def test_calendar_months_and_dubai_local_date(profile, route_factory, registry):
    route = route_factory("AE", "DXB")
    route.legs[0].departure_at = datetime.fromisoformat("2027-08-30T16:00:00Z")
    route.legs[0].arrival_at = route.transfers[0].arrival_at = datetime.fromisoformat("2027-08-30T21:00:00Z")
    route.legs[1].departure_at = route.transfers[0].departure_at = datetime.fromisoformat("2027-08-31T02:00:00Z")
    route.legs[1].arrival_at = datetime.fromisoformat("2027-08-31T05:00:00Z")
    route.transfers[0].leaves_airside = True
    profile.passport_expires_on = datetime(2028, 2, 28).date()
    result = evaluate(profile, route, registry)
    finding = next(f for f in result.findings if f.code == "ae_entry_passport_validity")
    assert "2028-02-29" in finding.message


@pytest.mark.parametrize("status", ["needs_verification", "conflicting", "expired", "future"])
def test_bad_sources_never_give_formal_block_or_clean_pass(profile, route_factory, registry, status):
    route = route_factory("AE", "DXB")
    route.transfers[0].leaves_airside = True
    profile.passport_expires_on = datetime(2027, 1, 1).date()
    ruleset = registry.get_ruleset(registry.transit_version, as_of=NOW.date())
    source = next(s for s in ruleset.sources if s.id == "AE_MOFA_MOSCOW")
    if status == "expired":
        source.checked_at = NOW - timedelta(days=10)
        source.review_after = (NOW - timedelta(days=1)).date()
    elif status == "future":
        source.checked_at = NOW + timedelta(days=1)
    else:
        source.status = status
    result = check_route(profile, route, ruleset, evaluated_at=NOW)
    assert result.status == "needs_verification" and "transit_source_unverified" in codes(result)
    assert not any(f.severity == "blocking" for f in result.findings)


def test_rule_expiry_affects_airside_too(profile, route_factory, registry):
    ruleset = registry.get_ruleset(registry.transit_version, as_of=NOW.date())
    result = check_route(profile, route_factory(), ruleset, evaluated_at=NOW + timedelta(days=8))
    assert result.status == "needs_verification" and "transit_source_unverified" in codes(result)


@pytest.mark.parametrize("mutation", ["empty", "no_transfer", "extra_transfer", "country", "airport", "time", "domestic", "long", "missing_leg_field"])
def test_partial_or_inconsistent_routes_never_pass(profile, route_factory, registry, mutation):
    route = route_factory()
    if mutation == "empty":
        route = Route(legs=[], transfers=[])
    elif mutation == "no_transfer":
        route.transfers = []
    elif mutation == "extra_transfer":
        route.transfers.append(route.transfers[0].model_copy(deep=True))
    elif mutation == "country":
        route.legs[1].departure_country = "AE"
    elif mutation == "airport":
        route.legs[1].departure_airport = "SAW"
    elif mutation == "time":
        route.transfers[0].arrival_at += timedelta(minutes=1)
    elif mutation == "domestic":
        route.legs[1].arrival_country = "TR"
        route.legs[1].arrival_airport = "AYT"
    elif mutation == "long":
        route.transfers[0].departure_at += timedelta(days=1)
        route.legs[1].departure_at += timedelta(days=1)
        route.legs[1].arrival_at += timedelta(days=1)
    else:
        route.legs[0].departure_country = None
    assert evaluate(profile, route, registry).status == "needs_verification"


def test_permission_list_is_not_proof_of_entry(profile, route_factory, registry):
    route = route_factory("AE", "DXB")
    route.transfers[0].leaves_airside = True
    route.transfers[0].permissions = [ExistingPermission(country="AE", kind="SAMPLE-unknown", expires_on="2030-01-01")]
    result = evaluate(profile, route, registry)
    assert result.status == "needs_verification" and "transit_permissions_not_validated" in codes(result)
    route.transfers[0].leaves_airside = False
    result = evaluate(profile, route, registry)
    assert result.status == "no_known_blockers"  # Airside rule does not depend on that permission.


def test_unknown_rules_fail_closed(profile, route_factory, registry):
    ruleset = registry.get_ruleset(registry.transit_version, as_of=NOW.date())
    ruleset.rules[0].kind = "invented"
    result = check_route(profile, route_factory(), ruleset, evaluated_at=NOW)
    assert result.status == "needs_verification" and "transit_rules_invalid" in codes(result)


def test_timezones_compare_instants_not_wall_clock(profile, route_factory, registry):
    route = route_factory()
    route.transfers[0].arrival_at = datetime.fromisoformat("2026-11-01T13:00:00+03:00")
    assert evaluate(profile, route, registry).status == "no_known_blockers"


@pytest.mark.parametrize("mutation", ["airport", "leg_order", "connection_order", "transfer_order", "too_many"])
def test_invalid_shape_or_chronology_rejected(route_factory, mutation):
    route = route_factory()
    if mutation == "airport":
        route.transfers[0].airport = "istanbul"
    elif mutation == "leg_order":
        route.legs[0].arrival_at = route.legs[0].departure_at
    elif mutation == "connection_order":
        route.legs[1].departure_at = route.legs[0].arrival_at
    elif mutation == "transfer_order":
        route.transfers[0].departure_at = route.transfers[0].arrival_at
    else:
        route.legs *= 13
    with pytest.raises(ValueError):
        validate_route(route)


def test_out_of_range_time_zone_conversion_rejected(route_factory):
    route = route_factory()
    route.legs[0].departure_at = datetime.fromisoformat("0001-01-01T00:00:00+14:00")
    with pytest.raises(ValueError):
        validate_route(route)
