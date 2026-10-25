"""Real API/archive. Optional package 6 below is a labelled contract fixture."""

from datetime import date, datetime, timezone
import json
import shutil
from importlib.resources import files
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.common.dates import add_calendar_months
from app.common.errors import AppError
from app.common.models import ApplicabilityResult, CheckResult, Finding, RuleRef, UserContext
from app.cases.projection import project_readiness
from app.rules.registry import FileRuleRegistry
from test_eligibility import trip
from test_profile import input_profile, login


@pytest.fixture
def case_client(client, monkeypatch):
    class SnapshotClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 25, 13, 4, tzinfo=timezone.utc)
    monkeypatch.setattr("app.eligibility.service.datetime", SnapshotClock)
    monkeypatch.setattr("app.rules.registry.datetime", SnapshotClock)
    session, headers = login(client, "sample_cases")
    assert client.put("/api/v1/profile", json=input_profile(), headers=headers).status_code == 200
    return client, headers, UserContext(user_id=session["user"]["id"])


def create_case(client, headers, country="FR", **trip_changes):
    answers = {"schengen_prior_stays": []} if country == "FR" else {}
    response = client.post("/api/v1/cases", headers=headers, json={
        "scenario_id": country + "_TOURISM", "trip": trip(**trip_changes).model_dump(mode="json"), "answers": answers,
    })
    assert response.status_code == 201, response.text
    return response.json()


def upload_passport(client, headers):
    content = (Path("demo/documents") / "good_passport.pdf").read_bytes()
    response = client.post("/api/v1/documents", headers=headers,
                           files={"file": ("renamed-SAMPLE.pdf", content, "application/pdf")}, data={"document_type": "passport"})
    assert response.status_code == 201, response.text
    return response.json()


def confirm_passport(client, headers, document, expiry="2027-04-30"):
    response = client.post(f"/api/v1/documents/{document['id']}/extract", headers=headers)
    assert response.status_code == 200, response.text
    document = response.json()
    response = client.patch(f"/api/v1/documents/{document['id']}/fields", headers=headers, json={
        "expected_facts_revision": document["facts_revision"], "fields": [
            {"key": field["key"], "value": expiry if field["key"] == "expires_on" else field["value"], "user_confirmed": True}
            for field in document["fields"]
        ],
    })
    assert response.status_code == 200, response.text
    return response.json()


class CompleteFixtureRegistry(FileRuleRegistry):
    """Small complete checklist ONLY for readiness formula tests, never production."""
    def get_scenario(self, scenario_id, **kwargs):
        scenario = super().get_scenario(scenario_id, **kwargs)
        if scenario_id == "FR_TOURISM":
            scenario.requirements = [item for item in scenario.requirements if item.id in {"FR.passport", "FR.form"}]
            scenario.coverage, scenario.unverified_conditions, scenario.data_quality = "complete", [], "source_checked"
        return scenario


class FixtureChecker:
    """Package 6 contract fixture: only the declared FR expiry check, not real 6."""
    def __init__(self):
        self.signatures = []
    def check_case(self, snapshot):
        self.signatures.append(snapshot.input_signature)
        findings = []
        passports = [doc for doc in snapshot.documents if doc.document_type == "passport"]
        if not passports or not passports[0].fields:
            findings.append(Finding(code="fixture_unconfirmed", severity="verify", message="CONTRACT FIXTURE: no confirmed facts", next_action="Confirm fields", origin="technical"))
        else:
            values = {field.key: field.value for field in passports[0].fields}
            if date.fromisoformat(values["expires_on"]) < add_calendar_months(snapshot.case.trip.ends_on, 3):
                findings.append(Finding(code="fixture_passport_expiry", severity="blocking", message="CONTRACT FIXTURE: passport expires too early",
                                        next_action="Correct expiry", document_ids=[passports[0].document_id],
                                        rule_ref=RuleRef(id="FR.passport.validity", version=snapshot.scenario.version), source_refs=["FR_ARRIVAL"], origin="rule"))
        return CheckResult(id=uuid4(), case_id=snapshot.case.id, evaluated_at=datetime.now(timezone.utc),
                           input_signature=snapshot.input_signature, findings=findings, stale=False)


def install_fixture(client):
    registry = CompleteFixtureRegistry()
    service = client.app.state.case_service
    service.registry = registry
    service.evaluator.registry = registry
    service.checker = FixtureChecker()
    return service


@pytest.mark.parametrize("country", ["FR", "ES", "JP", "IN", "EG", "ZA", "US", "CA", "BR", "AR", "AU", "NZ"])
def test_all_twelve_cases_use_real_applicability_and_partial_coverage(case_client, country):
    client, headers, _ = case_client
    case = create_case(client, headers, country)
    assert case["applicability"] and not case["stale"]
    assert case["readiness"]["percent"] is None and not case["readiness"]["ready"]
    assert case["trip"]["destination_country"] == country
    assert case["process"]["outcome"] == ("not_applicable" if country in ("ZA", "BR", "AR") else "unknown")
    assert client.get(f"/api/v1/cases/{case['id']}").json() == case
    assert len(client.get("/api/v1/cases").json()["items"]) == 1


def test_patch_merges_fields_validates_and_is_idempotent(case_client):
    client, headers, _ = case_client
    case = create_case(client, headers)
    url = f"/api/v1/cases/{case['id']}"
    response = client.patch(url, headers=headers, json={"trip": {"ends_on": "2027-02-01"}})
    assert response.status_code == 200
    changed = response.json()
    assert changed["revision"] == 2 and changed["trip"]["starts_on"] == case["trip"]["starts_on"]
    assert client.patch(url, headers=headers, json={"trip": {"ends_on": "2027-02-01"}}).json()["revision"] == 2
    for body in ({"trip": {"starts_on": "2028-01-01"}}, {"answers": {"other": True}}, {"trip": None}, {"answers": None}, {"owner_id": str(uuid4())}):
        assert client.patch(url, headers=headers, json=body).status_code == 422
    assert client.get(url).json()["revision"] == 2
    mismatch = client.post("/api/v1/cases", headers=headers, json={"scenario_id": "FR_TOURISM", "trip": {"destination_country": "JP"}})
    assert mismatch.status_code == 422


def test_same_passport_two_dates_actual_archive_invalidation_and_delete(case_client):
    client, headers, _ = case_client
    service = install_fixture(client)
    first = create_case(client, headers)
    second = create_case(client, headers, starts_on="2027-06-01", ends_on="2027-06-10")
    document = upload_passport(client, headers)
    for case in (first, second):
        attached = client.put(f"/api/v1/cases/{case['id']}/documents/{document['id']}", headers=headers)
        assert attached.status_code == 200, attached.text
        assert attached.json()["checklist"][0]["state"] in ("received", "needs_verification")
        assert attached.json()["checklist"][0]["verification_method"] is None
    document = confirm_passport(client, headers, document)
    first = client.get(f"/api/v1/cases/{first['id']}").json()
    second = client.get(f"/api/v1/cases/{second['id']}").json()
    assert first["checklist"][0]["state"] == "satisfied"
    assert second["checklist"][0]["state"] == "issues"
    assert first["readiness"]["percent"] == 50 and second["readiness"]["percent"] == 0
    closed = client.patch(f"/api/v1/cases/{first['id']}/checklist/FR.form", headers=headers, json={"confirmed": True}).json()
    assert closed["readiness"]["ready"] and closed["readiness"]["percent"] == 100
    assert closed["process"]["percent"] == 0 and closed["process"]["outcome"] == "unknown"
    # Repeated attachment is idempotent and cannot reset a valid confirmation.
    assert client.put(f"/api/v1/cases/{first['id']}/documents/{document['id']}", headers=headers).json()["readiness"]["ready"]
    # Late events change process only, not check signatures or readiness.
    checks_before = len(service.checker.signatures)
    event = client.post(f"/api/v1/cases/{first['id']}/events", headers=headers,
                        json={"event_type": "submitted", "occurred_at": "2026-10-01T10:00:00Z"})
    assert event.status_code == 201
    with_event = client.get(f"/api/v1/cases/{first['id']}").json()
    assert with_event["readiness"]["ready"] and with_event["process"]["percent"] == 20
    assert len(service.checker.signatures) == checks_before
    # Change a confirmed field: archive calls the real rebuilder for both cases.
    revised = client.patch(f"/api/v1/documents/{document['id']}/fields", headers=headers,
                           json={"expected_facts_revision": document["facts_revision"], "fields": [{"key": "expires_on", "value": "2026-11-01", "user_confirmed": True}]})
    assert revised.status_code == 200
    for case in (first, second):
        current = client.get(f"/api/v1/cases/{case['id']}").json()
        assert not current["readiness"]["ready"] and current["readiness"]["blocking_count"] > 0
    assert client.delete(f"/api/v1/documents/{document['id']}", headers=headers).status_code == 204
    for case in (first, second):
        current = client.get(f"/api/v1/cases/{case['id']}").json()
        assert current["attached_document_ids"] == [] and current["checklist"][0]["state"] in ("missing", "needs_verification")
        assert not current["readiness"]["ready"]


def test_without_package6_confirmed_file_is_not_automatically_satisfied(case_client):
    client, headers, _ = case_client
    case = create_case(client, headers)
    document = confirm_passport(client, headers, upload_passport(client, headers))
    response = client.put(f"/api/v1/cases/{case['id']}/documents/{document['id']}", headers=headers)
    assert response.status_code == 200
    passport = next(item for item in response.json()["checklist"] if item["requirement_id"] == "FR.passport")
    assert passport["state"] == "needs_verification" and not response.json()["readiness"]["ready"]
    assert client.patch(f"/api/v1/cases/{case['id']}/checklist/FR.passport", headers=headers, json={"confirmed": True}).status_code == 422
    assert client.patch(f"/api/v1/cases/{case['id']}/checklist/FR.appointment", headers=headers, json={"confirmed": True}).status_code == 422


def test_delete_case_keeps_archive_and_detach_does_not_delete_file(case_client):
    client, headers, _ = case_client
    case = create_case(client, headers)
    document = upload_passport(client, headers)
    link = f"/api/v1/cases/{case['id']}/documents/{document['id']}"
    assert client.put(link, headers=headers).status_code == 200
    assert client.delete(link, headers=headers).status_code == 204
    assert client.get(f"/api/v1/documents/{document['id']}/file").status_code == 200
    client.put(link, headers=headers)
    assert client.delete(f"/api/v1/cases/{case['id']}", headers=headers).status_code == 204
    assert client.get(f"/api/v1/cases/{case['id']}").status_code == 404
    assert client.get(f"/api/v1/documents/{document['id']}/file").status_code == 200


def test_two_accounts_cannot_read_mutate_attach_or_notify_foreign_resources(case_client, settings):
    alice, ah, _ = case_client
    a_case, a_document = create_case(alice, ah), upload_passport(alice, ah)
    with TestClient(alice.app) as bob:
        _, bh = login(bob, "sample_other")
        b_case = create_case(bob, bh)
        for suffix in ("", "/events", "/notifications"):
            assert bob.get(f"/api/v1/cases/{a_case['id']}" + suffix).status_code == 404
        assert bob.patch(f"/api/v1/cases/{a_case['id']}", headers=bh, json={}).status_code == 404
        assert bob.delete(f"/api/v1/cases/{a_case['id']}", headers=bh).status_code == 404
        for case_id in (a_case["id"], b_case["id"]):
            assert bob.put(f"/api/v1/cases/{case_id}/documents/{a_document['id']}", headers=bh).status_code == 404
        assert alice.put(f"/api/v1/cases/{b_case['id']}/documents/{a_document['id']}", headers=ah).status_code == 404


def test_profile_or_trip_change_invalidates_old_manual_confirmation(case_client):
    client, headers, _ = case_client
    case = create_case(client, headers)
    url = f"/api/v1/cases/{case['id']}/checklist/FR.form"
    assert client.patch(url, headers=headers, json={"confirmed": True}).status_code == 200
    assert client.patch(url, headers=headers, json={"confirmed": True}).json()["revision"] == case["revision"]
    client.put("/api/v1/profile", headers=headers, json=input_profile(surname="CHANGED"))
    changed = client.get(f"/api/v1/cases/{case['id']}").json()
    assert changed["profile_revision"] == 3
    assert next(item for item in changed["checklist"] if item["id"] == "FR.form")["state"] != "satisfied"
    client.patch(url, headers=headers, json={"confirmed": True})
    changed = client.patch(f"/api/v1/cases/{case['id']}", headers=headers, json={"trip": {"ends_on": "2027-02-01"}}).json()
    assert next(item for item in changed["checklist"] if item["id"] == "FR.form")["state"] != "satisfied"


def test_revision_race_and_late_check_cannot_publish_old_positive(case_client):
    client, headers, owner = case_client
    client.app.state.case_service.checker = None  # Isolate publication from automatic package 6.
    case = create_case(client, headers)
    service = client.app.state.case_service
    snapshot = service.snapshot(owner, UUID(case["id"]))
    old_check = CheckResult(id=uuid4(), case_id=case["id"], evaluated_at=datetime.now(timezone.utc),
                            input_signature=snapshot.input_signature, findings=[], stale=False)
    client.patch(f"/api/v1/cases/{case['id']}", headers=headers, json={"trip": {"ends_on": "2027-02-01"}})
    with pytest.raises(AppError) as late:
        service.record_check(owner, old_check)
    assert late.value.status_code == 409
    class RacingChecker(FixtureChecker):
        def check_case(self, snapshot):
            with service.database.transaction() as connection:
                connection.execute("UPDATE cases SET revision = revision + 1, stale = 1, projection_json = NULL WHERE id = ?", (case["id"],))
            return super().check_case(snapshot)
    service.checker = RacingChecker()
    response = client.get(f"/api/v1/cases/{case['id']}")
    assert response.status_code == 409 and response.json()["error"]["code"] == "input_changed"
    with service.database.connection() as connection:
        assert connection.execute("SELECT projection_json FROM cases WHERE id = ?", (case["id"],)).fetchone()[0] is None
        assert connection.execute("SELECT COUNT(*) FROM check_results").fetchone()[0] == 0


def test_empty_denominator_is_unknown_and_recommendations_do_not_count():
    applicability = ApplicabilityResult(status="applicable", requirements=[], barriers=[], missing_answers=[])
    readiness = project_readiness([], applicability=applicability, coverage_complete=True, findings=[])
    assert readiness.percent is None and not readiness.ready


def test_rules_version_change_rebases_case_and_keeps_retired_answers_reviewable(case_client, tmp_path):
    client, headers, _ = case_client
    case = create_case(client, headers)
    url = f"/api/v1/cases/{case['id']}"
    assert client.patch(url + "/checklist/FR.form", headers=headers, json={"confirmed": True}).status_code == 200
    root = tmp_path / "rules"
    shutil.copytree(files("data.rules.destinations"), root)
    old, new = "2026-10-25.1", "fixture-next"
    shutil.copytree(root / old, root / new)
    (root / "manifest.json").write_text(json.dumps({"schema_version": 1, "active_version": new, "versions": [old, new]}))
    for path in (root / new).glob("*.json"):
        content = json.loads(path.read_text())
        if path.name == "manifest.json":
            content["version"] = new
        else:
            content["scenario"]["version"] = new
            if path.name == "FR.json":
                content["scenario"]["questions"] = []
                content["rules"] = [rule for rule in content["rules"] if rule["id"] != "FR.stay"]
            for rule in content["rules"]:
                rule["version"] = new
        path.write_text(json.dumps(content))
    registry = FileRuleRegistry(root)
    service = client.app.state.case_service
    service.registry = service.evaluator.registry = registry
    migrated = client.get(url)
    assert migrated.status_code == 200, migrated.text
    assert migrated.json()["ruleset_version"] == new and migrated.json()["answers"] == {}
    assert any(finding["code"] == "answers_schema_changed" for finding in migrated.json()["applicability"]["unknown_conditions"])
    assert next(item for item in migrated.json()["checklist"] if item["id"] == "FR.form")["state"] != "satisfied"
    with service.database.connection() as connection:
        original = json.loads(connection.execute("SELECT answers_json FROM cases WHERE id = ?", (case["id"],)).fetchone()[0])
        assert original == {"schengen_prior_stays": []}
    saved = client.patch(url, headers=headers, json={"answers": {}})
    assert saved.status_code == 200
    assert not any(finding["code"] == "answers_schema_changed" for finding in saved.json()["applicability"]["unknown_conditions"])
