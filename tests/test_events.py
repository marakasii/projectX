from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.common.models import Event, ProcessStageTemplate
from app.events.projection import project_process
from test_cases import case_client, create_case


def event(kind, outcome=None, occurred="2026-10-01T10:00:00Z"):
    return Event(id=uuid4(), case_id=uuid4(), event_type=kind, occurred_at=occurred,
                 received_at=datetime.now(timezone.utc), source="manual", outcome=outcome)


def test_later_event_completes_only_its_stage_and_return_never_infers_approval():
    templates = [ProcessStageTemplate(id=str(i), title=kind, event_types=[kind])
                 for i, kind in enumerate(("form_prepared", "submitted", "decision_received", "passport_returned"))]
    returned = event("passport_returned")
    process = project_process([returned], templates)
    assert process.percent == 25 and process.outcome == "unknown" and process.current_stage == "0"
    assert [stage.completed for stage in process.stages] == [False, False, False, True]
    assert project_process([returned, returned], templates).percent == 25
    refused = event("decision_received", "refused")
    assert project_process([returned, refused], templates).outcome == "refused"
    assert project_process([], []).percent is None


def test_conditional_stage_unknown_and_false_and_source_quality_are_separate():
    template = ProcessStageTemplate(id="interview", title="Interview", event_types=["appointment_booked"],
                                    applies_if={"field": "answers.interview", "op": "eq", "value": True}, source_ids=["fixture"])
    assert project_process([], [template]).stages[0].applicability == "unknown"
    context = {"profile": {}, "trip": {}, "answers": {"interview": False}}
    assert project_process([], [template], context=context).stages[0].applicability == "no"
    context["answers"]["interview"] = True
    assert project_process([event("appointment_booked")], [template], context=context).percent == 100
    unknown = project_process([event("appointment_booked")], [template], context=context, unverified_source_ids=["fixture"])
    assert unknown.percent is None and unknown.stages[0].applicability == "unknown"


def test_outcome_uses_occurrence_order_and_visa_free_has_no_decision():
    template = ProcessStageTemplate(id="decision", title="Decision", event_types=["decision_received"])
    first = event("decision_received", "approved", "2026-10-01T10:00:00Z")
    second = event("decision_received", "refused", "2026-10-02T10:00:00Z")
    assert project_process([second, first], [template]).outcome == "refused"
    free = ProcessStageTemplate(id="entry", title="Entry preparation", event_types=["entry_preparation_completed"])
    assert project_process([], [free]).outcome == "not_applicable"
    assert project_process([event("entry_preparation_completed")], [free]).percent == 100


def test_real_timeline_persists_manual_times_and_does_not_change_readiness(case_client):
    client, headers, _ = case_client
    case = create_case(client, headers)
    url = f"/api/v1/cases/{case['id']}/events"
    returned = client.post(url, headers=headers, json={"event_type": "passport_returned", "occurred_at": "2026-10-02T12:00:00+03:00", "note": "SAMPLE returned"})
    assert returned.status_code == 201, returned.text
    row = returned.json()
    assert row["source"] == "manual" and row["case_id"] == case["id"] and row["received_at"]
    projected = client.get(f"/api/v1/cases/{case['id']}").json()
    assert projected["process"]["outcome"] == "unknown" and projected["process"]["percent"] == 20
    assert projected["readiness"] == case["readiness"]
    refused = client.post(url, headers=headers, json={"event_type": "decision_received", "outcome": "refused", "occurred_at": "2026-10-01T12:00:00Z"})
    assert refused.status_code == 201
    assert client.get(f"/api/v1/cases/{case['id']}").json()["process"]["outcome"] == "refused"
    rows = client.get(url).json()["items"]
    assert [item["event_type"] for item in rows] == ["decision_received", "passport_returned"]
    notifications = client.get(f"/api/v1/cases/{case['id']}/notifications")
    assert notifications.status_code == 200
    assert any(item["code"] == "decision_refused" for item in notifications.json()["items"])
    assert any(item["code"] == "process_next_action" for item in notifications.json()["items"])


@pytest.mark.parametrize("body", [
    {"event_type": "decision_received"}, {"event_type": "decision_received", "outcome": "unknown"},
    {"event_type": "passport_returned", "outcome": "approved"},
    {"event_type": "entry_preparation_completed"},
    {"event_type": "submitted", "occurred_at": "2999-01-01T00:00:00Z"},
    {"event_type": "submitted", "source": "email"},
    {"event_type": "submitted", "occurred_at": "2026-10-01T10:00:00"},
])
def test_invalid_event_is_not_persisted(case_client, body):
    client, headers, _ = case_client
    case = create_case(client, headers)
    url = f"/api/v1/cases/{case['id']}/events"
    response = client.post(url, headers=headers, json={"occurred_at": "2026-10-01T10:00:00Z"} | body)
    assert response.status_code == 422, response.text
    assert client.get(url).json()["items"] == []


@pytest.mark.parametrize("country", ["BR", "AR", "ZA"])
def test_visa_free_preparation_and_rejection_of_fictitious_submission(case_client, country):
    client, headers, _ = case_client
    case = create_case(client, headers, country)
    url = f"/api/v1/cases/{case['id']}/events"
    body = {"event_type": "submitted", "occurred_at": "2026-10-01T10:00:00Z"}
    assert client.post(url, headers=headers, json=body).status_code == 422
    body["event_type"] = "entry_preparation_completed"
    assert client.post(url, headers=headers, json=body).status_code == 201
    projected = client.get(f"/api/v1/cases/{case['id']}").json()
    assert projected["process"]["outcome"] == "not_applicable" and projected["process"]["percent"] == 100
    assert not projected["readiness"]["ready"]


def test_foreign_case_and_csrf_rejected_before_event_write(case_client):
    client, headers, _ = case_client
    case = create_case(client, headers)
    body = {"event_type": "submitted", "occurred_at": "2026-10-01T10:00:00Z"}
    assert client.post(f"/api/v1/cases/{case['id']}/events", json=body).status_code == 403
    assert client.post(f"/api/v1/cases/{uuid4()}/events", headers=headers, json=body).status_code == 404
