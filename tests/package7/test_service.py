"""Actual packages 4/5/6/7/9 over HTTP; no seeded cases or replacement checker."""

import json
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.common.errors import AppError
from app.common.models import Route, UserContext
from app.itinerary.application import create_app
from app.itinerary.engine import TransitChecker
from .conftest import NOW

PASSWORD = "SAMPLE-itinerary-password"


def login(client, username):
    body = {"username": username, "password": PASSWORD}
    assert client.post("/api/v1/auth/register", headers={"Origin": "http://testserver"}, json=body).status_code == 201
    response = client.post("/api/v1/auth/login", headers={"Origin": "http://testserver"}, json=body)
    assert response.status_code == 200
    return response.json(), {"X-CSRF-Token": response.json()["csrf_token"]}


def new_case(client, headers, country="FR"):
    response = client.post("/api/v1/cases", headers=headers, json={
        "scenario_id": country + "_TOURISM",
        "trip": {"purpose": "tourism", "starts_on": "2026-11-01", "ends_on": "2026-11-10",
                 "application_country": "RU", "accommodation_type": "hotel", "funding_type": "self"},
        "answers": {"schengen_prior_stays": []} if country == "FR" else {},
    })
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def setup_itinerary(settings, profile):
    app = create_app(settings, clock=lambda: NOW)
    with TestClient(app) as alice, TestClient(app) as bob:
        session, ah = login(alice, "sample_route_alice")
        other, bh = login(bob, "sample_route_bob")
        data = profile.model_dump(mode="json", exclude={"id", "revision"})
        assert alice.put("/api/v1/profile", headers=ah, json=data).status_code == 200
        assert bob.put("/api/v1/profile", headers=bh, json=data).status_code == 200
        case, foreign = new_case(alice, ah), new_case(bob, bh)
        yield app, alice, bob, session, other, ah, bh, case, foreign


def owner(session):
    return UserContext(user_id=session["user"]["id"])


def save_route(client, headers, case, route):
    path = f"/api/v1/cases/{case['id']}/route"
    response = client.put(path, headers=headers, json=route.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    assert response.json() == route.model_dump(mode="json")
    assert response.headers["cache-control"] == "no-store"
    return path


def row(app, table, case_id):
    with app.state.database.connection() as connection:
        return dict(connection.execute(f"SELECT * FROM {table} WHERE {'id' if table == 'cases' else 'case_id'} = ?",
                                       (case_id,)).fetchone())


@pytest.mark.parametrize("country", ["FR", "ES", "JP", "IN", "EG", "ZA", "US", "CA", "BR", "AR", "AU", "NZ"])
def test_all_twelve_real_cases_accept_transit_and_document_checker(setup_itinerary, route_factory, country):
    app, alice, _, session, _, ah, _, _, _ = setup_itinerary
    case = new_case(alice, ah, country)
    route = route_factory()
    route.legs[-1].arrival_country = country
    route.legs[-1].arrival_airport = {"FR": "CDG", "ES": "MAD", "JP": "NRT", "IN": "DEL", "EG": "CAI", "ZA": "JNB",
                                     "US": "JFK", "CA": "YYZ", "BR": "GRU", "AR": "EZE", "AU": "SYD", "NZ": "AKL"}[country]
    path = save_route(alice, ah, case, route)
    result = alice.post(path + "/check", headers=ah)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "no_known_blockers" and result.json()["flight_availability"] == "not_checked"
    updated = alice.get(f"/api/v1/cases/{case['id']}").json()
    assert updated["stale"] is False and not updated["readiness"]["ready"]
    assert updated["readiness"]["percent"] is None  # Partial destination catalog stays partial.
    assert app.state.itinerary_service.latest(owner(session), UUID(case["id"])).model_dump(mode="json") == result.json()
    check = alice.get(f"/api/v1/cases/{case['id']}/checks").json()
    assert not check["stale"] and check["input_signature"]["route_revision"] == 1


def test_owner_csrf_logout_and_client_cannot_assign_other_user(setup_itinerary, route_factory):
    app, alice, bob, session, _, ah, bh, case, foreign = setup_itinerary
    path = f"/api/v1/cases/{case['id']}/route"
    body = route_factory().model_dump(mode="json")
    absent = f"/api/v1/cases/{uuid4()}/route"
    assert bob.put(path, headers=bh, json=body).status_code == 404
    assert bob.put(path, headers=bh, json=body).json() == bob.put(absent, headers=bh, json=body).json()
    assert bob.post(path + "/check", headers=bh).status_code == 404
    assert alice.put(path, json=body).status_code == 403
    assert alice.post(path + "/check").status_code == 403
    assert alice.put(path, headers=ah, json={**body, "owner_id": session["user"]["id"]}).status_code == 422
    assert alice.post(path + "/check", headers=ah).status_code == 404
    save_route(alice, ah, case, route_factory())
    result = alice.post(path + "/check", headers=ah, json={"route": route_factory("DE", "FRA").model_dump(mode="json"), "ruleset": {}})
    assert result.status_code == 200 and result.json()["covered_countries"] == ["TR"]
    assert alice.delete(f"/api/v1/cases/{foreign['id']}", headers=ah).status_code == 404
    assert alice.post("/api/v1/auth/logout", headers=ah).status_code == 204
    assert alice.post(path + "/check", headers=ah).status_code == 401
    assert alice.put(path, json=body, headers=ah).status_code == 401


def test_idempotent_save_and_real_route_revision_rebuild(setup_itinerary, route_factory):
    app, alice, _, session, _, ah, _, case, _ = setup_itinerary
    route = route_factory()
    path = save_route(alice, ah, case, route)
    first = row(app, "routes", case["id"])
    first_case = row(app, "cases", case["id"])
    assert first["revision"] == 1 and first["result_json"] is None
    assert first_case["revision"] == case["revision"] + 1 and not first_case["stale"]
    assert alice.post(path + "/check", headers=ah).status_code == 200
    save_route(alice, ah, case, route)
    assert row(app, "routes", case["id"])["revision"] == 1
    assert row(app, "cases", case["id"])["revision"] == first_case["revision"]
    assert app.state.itinerary_service.latest(owner(session), UUID(case["id"])).status == "no_known_blockers"
    route.transfers[0].rechecks_baggage = None
    save_route(alice, ah, case, route)
    changed = row(app, "routes", case["id"])
    assert changed["revision"] == 2 and changed["stale"] and changed["result_json"] is None
    assert alice.post(path + "/check", headers=ah).json()["status"] == "needs_verification"
    snapshot = app.state.case_service.snapshot(owner(session), UUID(case["id"]))
    assert snapshot.input_signature.route_revision == 2


@pytest.mark.parametrize("mutation", ["profile", "trip", "algorithm", "transit_version", "tomorrow", "document"])
def test_all_result_dependencies_invalidate_internal_latest(setup_itinerary, route_factory, mutation):
    app, alice, _, session, _, ah, _, case, _ = setup_itinerary
    path = save_route(alice, ah, case, route_factory())
    assert alice.post(path + "/check", headers=ah).status_code == 200
    if mutation == "profile":
        profile = alice.get("/api/v1/profile").json()
        profile.pop("id")
        profile.pop("revision")
        profile["given_name"] = "SAMPLE"
        assert alice.put("/api/v1/profile", headers=ah, json=profile).status_code == 200
    elif mutation == "trip":
        assert alice.patch(f"/api/v1/cases/{case['id']}", headers=ah, json={"trip": {"ends_on": "2026-11-11"}}).status_code == 200
    elif mutation == "algorithm":
        with app.state.database.transaction() as connection:
            signature = json.loads(row(app, "routes", case["id"])["input_signature_json"])
            signature["algorithm_version"] = "old"
            connection.execute("UPDATE routes SET input_signature_json = ? WHERE case_id = ?", (json.dumps(signature), case["id"]))
    elif mutation == "transit_version":
        app.state.itinerary_service.registry.transit_version = "transit:next"
    elif mutation == "tomorrow":
        app.state.itinerary_service.clock = lambda: NOW + timedelta(days=1)
    else:
        # A real archive document and attachment, through actual package 4.
        from pathlib import Path
        response = alice.post("/api/v1/documents", headers=ah,
                              files={"file": ("SAMPLE.pdf", Path("demo/documents/good_passport.pdf").read_bytes(), "application/pdf")},
                              data={"document_type": "passport"})
        assert response.status_code == 201
        assert alice.put(f"/api/v1/cases/{case['id']}/documents/{response.json()['id']}", headers=ah).status_code == 200
    with pytest.raises(AppError) as error:
        app.state.itinerary_service.latest(owner(session), UUID(case["id"]))
    assert error.value.status_code == 409 and error.value.error.code == "route_checks_stale"


@pytest.mark.parametrize("mutation", ["profile", "route", "delete"])
def test_racing_changes_cannot_publish_old_result(setup_itinerary, route_factory, mutation):
    app, alice, _, session, _, ah, _, case, _ = setup_itinerary
    route = route_factory()
    path = save_route(alice, ah, case, route)
    service = app.state.itinerary_service
    class RacingChecker:
        def check_route(self, profile, actual_route, ruleset):
            result = TransitChecker(lambda: NOW).check_route(profile, actual_route, ruleset)
            if mutation == "route":
                changed = actual_route.model_copy(deep=True)
                changed.transfers[0].ticket_type = "separate"
                service.save(owner(session), UUID(case["id"]), changed)
            else:
                with app.state.database.transaction() as connection:
                    if mutation == "delete":
                        connection.execute("DELETE FROM cases WHERE id = ?", (case["id"],))
                    else:
                        connection.execute("UPDATE profiles SET revision = revision + 1 WHERE owner_id = ?", (session["user"]["id"],))
            return result
    service.checker = RacingChecker()
    response = alice.post(path + "/check", headers=ah)
    assert response.status_code in (404, 409)
    if mutation != "delete":
        assert row(app, "routes", case["id"])["result_json"] is None


def test_route_and_case_survive_restart(settings, setup_itinerary, route_factory):
    app, alice, _, session, _, ah, _, case, _ = setup_itinerary
    path = save_route(alice, ah, case, route_factory("AE", "DXB"))
    expected = alice.post(path + "/check", headers=ah).json()
    cookies = dict(alice.cookies)
    with TestClient(create_app(settings, clock=lambda: NOW)) as restarted:
        restarted.cookies.update(cookies)
        assert restarted.get(f"/api/v1/cases/{case['id']}").status_code == 200
        service = restarted.app.state.itinerary_service
        assert service.get_route(owner(session), UUID(case["id"])).model_dump(mode="json") == route_factory("AE", "DXB").model_dump(mode="json")
        assert service.latest(owner(session), UUID(case["id"])).model_dump(mode="json") == expected
        assert restarted.post(path + "/check", headers=ah).json() == expected


def test_real_manual_event_keeps_route_and_checks_current(setup_itinerary, route_factory):
    app, alice, _, session, _, ah, _, case, _ = setup_itinerary
    path = save_route(alice, ah, case, route_factory())
    assert alice.post(path + "/check", headers=ah).status_code == 200
    checks = alice.get(f"/api/v1/cases/{case['id']}/checks").json()
    event = alice.post(f"/api/v1/cases/{case['id']}/events", headers=ah,
                       json={"event_type": "passport_returned", "occurred_at": "2026-10-25T10:00:00Z", "note": "SAMPLE: returned"})
    assert event.status_code == 201, event.text
    current = alice.get(f"/api/v1/cases/{case['id']}").json()
    assert current["process"]["outcome"] == "unknown" and not current["readiness"]["ready"]
    assert alice.get(f"/api/v1/cases/{case['id']}/checks").json() == checks
    assert app.state.itinerary_service.latest(owner(session), UUID(case["id"])).status == "no_known_blockers"


def test_contract_schemas_and_security_match_shared_models(settings):
    app = create_app(settings)
    schema = app.openapi()
    paths = schema["paths"]
    for path, method, model in [("/api/v1/cases/{case_id}/route", "put", "Route"),
                                 ("/api/v1/cases/{case_id}/route/check", "post", "RouteResult")]:
        operation = paths[path][method]
        assert operation["x-implementation"] == "implemented" and operation["x-package"] == 7
        assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/" + model)
        assert operation["security"]
    assert "get" not in paths["/api/v1/cases/{case_id}/route"]


def test_real_pdf_checks_correction_and_manual_outcome(setup_itinerary, route_factory):
    from pathlib import Path

    app, alice, _, session, _, ah, _, case, _ = setup_itinerary
    path = save_route(alice, ah, case, route_factory())
    sample = Path("demo/documents/good_passport.pdf").read_bytes()
    uploaded = alice.post("/api/v1/documents", headers=ah,
                          files={"file": ("SAMPLE.pdf", sample, "application/pdf")}, data={"document_type": "passport"})
    assert uploaded.status_code == 201
    document_path = "/api/v1/documents/" + uploaded.json()["id"]
    extracted = alice.post(document_path + "/extract", headers=ah)
    assert extracted.status_code == 200 and extracted.json()["extraction_state"] == "extracted"
    document = extracted.json()
    confirmed = alice.patch(document_path + "/fields", headers=ah, json={
        "expected_facts_revision": document["facts_revision"],
        "fields": [{"key": f["key"], "value": "2027-01-01" if f["key"] == "expires_on" else f["value"],
                    "user_confirmed": True} for f in document["fields"]],
    })
    assert confirmed.status_code == 200
    attached = alice.put(f"/api/v1/cases/{case['id']}/documents/{document['id']}", headers=ah)
    assert attached.status_code == 200, attached.text
    check_path = f"/api/v1/cases/{case['id']}/checks"
    bad = alice.post(check_path, headers=ah)
    assert bad.status_code == 200, bad.text
    assert any(f["code"] == "passport_validity" and f["document_ids"] == [document["id"]] for f in bad.json()["findings"])
    assert bad.json()["input_signature"]["route_revision"] == 1
    assert not bad.json()["stale"]
    corrected = alice.patch(document_path + "/fields", headers=ah, json={
        "expected_facts_revision": confirmed.json()["facts_revision"],
        "fields": [{"key": "expires_on", "value": "2032-04-15", "user_confirmed": True}],
    })
    assert corrected.status_code == 200, corrected.text
    # Actual DocumentArchive callback -> CaseService -> real package 6 adapter.
    current = alice.get(check_path).json()
    assert current["id"] != bad.json()["id"] and not current["stale"]
    assert not any(f["code"] == "passport_validity" and f["document_ids"] for f in current["findings"])
    assert current["input_signature"]["facts_revisions"][document["id"]] == corrected.json()["facts_revision"]
    assert alice.get(document_path + "/file").content == sample
    assert alice.post(path + "/check", headers=ah).json()["status"] == "no_known_blockers"
    decision = alice.post(f"/api/v1/cases/{case['id']}/events", headers=ah, json={
        "event_type": "decision_received", "occurred_at": "2026-10-25T09:00:00Z", "outcome": "refused",
    })
    assert decision.status_code == 201
    returned = alice.post(f"/api/v1/cases/{case['id']}/events", headers=ah, json={
        "event_type": "passport_returned", "occurred_at": "2026-10-25T10:00:00Z",
    })
    assert returned.status_code == 201
    actual = alice.get(f"/api/v1/cases/{case['id']}").json()
    assert actual["process"]["outcome"] == "refused" and not actual["readiness"]["ready"]


def test_profile_refresh_before_route_check_keeps_new_result_current(setup_itinerary, route_factory):
    app, alice, _, session, _, ah, _, case, _ = setup_itinerary
    path = save_route(alice, ah, case, route_factory())
    data = alice.get("/api/v1/profile").json()
    data.pop("id")
    data.pop("revision")
    data["given_name"] = "SAMPLE"
    assert alice.put("/api/v1/profile", headers=ah, json=data).status_code == 200
    assert alice.post(path + "/check", headers=ah).json()["status"] == "no_known_blockers"
    assert app.state.itinerary_service.latest(owner(session), UUID(case["id"])).status == "no_known_blockers"


def test_route_destination_must_match_real_case(setup_itinerary, route_factory):
    _, alice, _, _, _, ah, _, case, _ = setup_itinerary
    route = route_factory()
    route.legs[-1].arrival_country = "JP"
    route.legs[-1].arrival_airport = "NRT"
    path = save_route(alice, ah, case, route)
    result = alice.post(path + "/check", headers=ah).json()
    assert result["status"] == "needs_verification"
    assert any(f["code"] == "route_destination_mismatch" for f in result["findings"])
