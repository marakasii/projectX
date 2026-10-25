"""One real server: 1/11/2/3/4/5/9, same-origin frontend fixture and restart.

The temporary HTML is a hosting fixture, not the team's frontend. Packages
6/7/8 are explicitly unavailable; no test checker or AI is injected here.
"""

import socket
import sys
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from auth_http_smoke import server
from rules_profile_http_smoke import SAMPLE_PROFILE, SAMPLE_COMPARISON, sign_in


def main():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="visapilot-cases-events-") as directory:
        data = str(Path(directory) / "data")
        build = Path(directory) / "frontend-build"
        build.mkdir()
        (build / "index.html").write_text('<!doctype html><title>SAMPLE frontend hosting fixture</title>')
        env = {"VISAPILOT_FRONTEND_DIST": str(build)}
        with httpx.Client(base_url=origin, headers={"Origin": origin}, timeout=30, trust_env=False) as alice, \
                httpx.Client(base_url=origin, headers={"Origin": origin}, timeout=30, trust_env=False) as bob:
            with server(port, data, environment_overrides=env):
                assert "SAMPLE frontend hosting fixture" in alice.get("/").text
                assert alice.get("/docs").status_code == 200
                ah, bh = sign_in(alice, "sample_alice"), sign_in(bob, "sample_bob")
                assert alice.put("/api/v1/profile", headers=ah, json=SAMPLE_PROFILE).status_code == 200
                cases = []
                for destination in alice.get("/api/v1/destinations").json()["items"]:
                    body = {"scenario_id": destination["scenario_id"], "trip": SAMPLE_COMPARISON["trip"],
                            "answers": {"schengen_prior_stays": []} if destination["country"] == "FR" else {}}
                    response = alice.post("/api/v1/cases", headers=ah, json=body)
                    assert response.status_code == 201, response.text
                    assert not response.json()["readiness"]["ready"]
                    cases.append(response.json())
                case = next(item for item in cases if item["scenario_id"] == "FR_TOURISM")
                case_url = f"/api/v1/cases/{case['id']}"
                assert alice.get("/cabinet/cases/" + case["id"]).status_code == 200
                assert bob.get(case_url).status_code == 404
                sample = (ROOT / "demo/documents/good_passport.pdf").read_bytes()
                uploaded = alice.post("/api/v1/documents", headers=ah, files={"file": ("SAMPLE-renamed.pdf", sample)}, data={"document_type": "passport"})
                assert uploaded.status_code == 201, uploaded.text
                doc_url = "/api/v1/documents/" + uploaded.json()["id"]
                assert alice.get(doc_url + "/file").content == sample
                assert bob.get(doc_url + "/file").status_code == 404
                assert bob.put(case_url + "/documents/" + uploaded.json()["id"], headers=bh).status_code == 404
                document = alice.post(doc_url + "/extract", headers=ah).json()
                assert len(document["fields"]) == 6
                confirmed = alice.patch(doc_url + "/fields", headers=ah, json={
                    "expected_facts_revision": document["facts_revision"],
                    "fields": [{"key": field["key"], "value": field["value"], "user_confirmed": True} for field in document["fields"]],
                })
                assert confirmed.status_code == 200
                attached = alice.put(case_url + "/documents/" + document["id"], headers=ah)
                assert attached.status_code == 200, attached.text
                assert not attached.json()["readiness"]["ready"]
                for suffix in ("/checks", "/route/check", "/letters"):
                    kwargs = {"json": {}} if suffix == "/letters" else {}
                    assert alice.post(case_url + suffix, headers=ah, **kwargs).status_code == 503
                assert alice.post(case_url + "/events", json={"event_type": "submitted", "occurred_at": "2026-10-01T10:00:00Z"}).status_code == 403
                returned = alice.post(case_url + "/events", headers=ah, json={"event_type": "passport_returned", "occurred_at": "2026-10-01T10:00:00Z"})
                assert returned.status_code == 201
                assert alice.get(case_url).json()["process"]["outcome"] == "unknown"
                assert alice.post(case_url + "/events", headers=ah, json={"event_type": "decision_received", "occurred_at": "2026-10-01T10:00:00Z"}).status_code == 422
                assert alice.post(case_url + "/events", headers=ah, json={"event_type": "decision_received", "outcome": "refused", "occurred_at": "2026-10-01T09:00:00Z"}).status_code == 201
                assert bob.get(case_url + "/events").status_code == 404
                assert alice.get(case_url + "/notifications").status_code == 200
                free = next(item for item in cases if item["scenario_id"] == "BR_TOURISM")
                free_url = f"/api/v1/cases/{free['id']}/events"
                assert alice.post(free_url, headers=ah, json={"event_type": "submitted", "occurred_at": "2026-10-01T10:00:00Z"}).status_code == 422
                assert alice.post(free_url, headers=ah, json={"event_type": "entry_preparation_completed", "occurred_at": "2026-10-01T10:00:00Z"}).status_code == 201
                for path in ("/local_data/uploads/SAMPLE.pdf", "/api/v1/not-real", "/.env"):
                    assert alice.get(path).status_code == 404
            with server(port, data, environment_overrides=env):
                assert len(alice.get("/api/v1/cases").json()["items"]) == 12
                assert alice.get(case_url).json()["process"]["outcome"] == "refused"
                assert len(alice.get(case_url + "/events").json()["items"]) == 2
                assert alice.get(doc_url + "/file").content == sample
                assert alice.delete(doc_url, headers=ah).status_code == 204
                assert alice.get(case_url).json()["attached_document_ids"] == []
                assert not alice.get(case_url).json()["readiness"]["ready"]
                assert alice.delete(case_url, headers=ah).status_code == 204
                assert alice.get(case_url).status_code == 404
                assert alice.post("/api/v1/auth/logout", headers=ah).status_code == 204
                assert alice.get("/api/v1/cases").status_code == 401
    print("HTTP application smoke passed: 12 cases, shared archive/extraction, ownership/CSRF, timeline/outcome, SPA/API origin, restart, invalidation and logout; 6/7/8 remain unavailable")


if __name__ == "__main__":
    main()
