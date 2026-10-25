"""Real Uvicorn: 4/5/6/7/9, two accounts, PDF, route, outcome and restart.

All personal data are SAMPLE. No AI, flight search or external API is called.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from rules_profile_http_smoke import sign_in


@contextmanager
def server(port, data_dir):
    origin = f"http://127.0.0.1:{port}"
    environment = {**os.environ, "VISAPILOT_DATA_DIR": str(data_dir), "VISAPILOT_ALLOWED_ORIGINS": json.dumps([origin]),
                   "VISAPILOT_COOKIE_SECURE": "false", "VISAPILOT_AI_ENABLED": "false"}
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.itinerary.application:create_app", "--factory", "--host", "127.0.0.1",
             "--port", str(port), "--workers", "1", "--no-access-log"],
            cwd=ROOT, env=environment, stdout=log, stderr=log,
        )
        try:
            deadline = time.monotonic() + 10
            with httpx.Client(trust_env=False, timeout=0.5) as probe:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Smoke server exited before startup")
                    try:
                        if probe.get(origin + "/api/v1/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    raise RuntimeError("Smoke server startup timeout")
            yield
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    # Saved by the actual pure engine in the example generator, never a flight booking.
    route = json.loads((ROOT / "app/itinerary/examples/airside_tr_route.json").read_text())
    profile = {"given_name": "ALEXEI", "surname": "SAMPLEOV", "birth_date": "1996-03-14", "citizenship": "RU",
               "residence_country": "RU", "has_other_citizenship": False, "has_foreign_residence_permit": False,
               "passport_kind": "biometric", "passport_issued_on": "2022-04-15", "passport_expires_on": "2032-04-15"}
    with tempfile.TemporaryDirectory(prefix="visapilot-route-smoke-") as directory:
        data_dir = Path(directory) / "data"
        with httpx.Client(base_url=origin, headers={"Origin": origin}, timeout=30, trust_env=False) as alice, \
                httpx.Client(base_url=origin, headers={"Origin": origin}, timeout=30, trust_env=False) as bob:
            with server(port, data_dir):
                ah, bh = sign_in(alice, "sample_alice"), sign_in(bob, "sample_bob")
                assert alice.put("/api/v1/profile", headers=ah, json=profile).status_code == 200
                case = alice.post("/api/v1/cases", headers=ah, json={"scenario_id": "FR_TOURISM",
                    "trip": {"purpose": "tourism", "starts_on": "2026-11-01", "ends_on": "2026-11-10",
                             "application_country": "RU", "accommodation_type": "hotel", "funding_type": "self"},
                    "answers": {"schengen_prior_stays": []}})
                assert case.status_code == 201, case.text
                case_path = "/api/v1/cases/" + case.json()["id"]
                content = (ROOT / "demo/documents/good_passport.pdf").read_bytes()
                upload = alice.post("/api/v1/documents", headers=ah, files={"file": ("SAMPLE.pdf", content, "application/pdf")},
                                    data={"document_type": "passport"})
                assert upload.status_code == 201
                document_path = "/api/v1/documents/" + upload.json()["id"]
                document = alice.post(document_path + "/extract", headers=ah).json()
                assert document["extraction_state"] == "extracted"
                confirmed = alice.patch(document_path + "/fields", headers=ah, json={"expected_facts_revision": document["facts_revision"],
                    "fields": [{"key": f["key"], "value": f["value"], "user_confirmed": True} for f in document["fields"]]})
                assert confirmed.status_code == 200
                assert alice.put(case_path + "/documents/" + document["id"], headers=ah).status_code == 200
                assert alice.put(case_path + "/route", headers=ah, json=route).status_code == 200
                assert bob.put(case_path + "/route", headers=bh, json=route).status_code == 404
                assert alice.post(case_path + "/route/check").status_code == 403
                response = alice.post(case_path + "/route/check", headers=ah)
                assert response.status_code == 200 and response.json()["status"] == "no_known_blockers", response.text
                assert response.json()["flight_availability"] == "not_checked"
                check = alice.post(case_path + "/checks", headers=ah)
                assert check.status_code == 200 and check.json()["input_signature"]["route_revision"] == 1, check.text
                assert not check.json()["stale"]
                for event in [
                    {"event_type": "decision_received", "occurred_at": "2026-10-25T09:00:00Z", "outcome": "refused"},
                    {"event_type": "passport_returned", "occurred_at": "2026-10-25T10:00:00Z"},
                ]:
                    result = alice.post(case_path + "/events", headers=ah, json=event)
                    assert result.status_code == 201, result.text
                assert alice.get(case_path).json()["process"]["outcome"] == "refused"
            with server(port, data_dir):
                persisted = alice.get(case_path)
                assert persisted.status_code == 200 and persisted.json()["process"]["outcome"] == "refused"
                assert not persisted.json()["readiness"]["ready"]
                assert alice.get(document_path + "/file").content == content
                assert not alice.get(case_path + "/checks").json()["stale"]
                assert alice.post(case_path + "/route/check", headers=ah).json()["status"] == "no_known_blockers"
                unknown = json.loads(json.dumps(route))
                unknown["transfers"][0]["rechecks_baggage"] = None
                assert alice.put(case_path + "/route", headers=ah, json=unknown).status_code == 200
                assert alice.post(case_path + "/route/check", headers=ah).json()["status"] == "needs_verification"
                assert alice.get(case_path + "/notifications").status_code == 200
                assert alice.delete(case_path, headers=ah).status_code == 204
                assert alice.post(case_path + "/route/check", headers=ah).status_code == 404
                assert alice.get(document_path + "/file").content == content  # Personal archive survives case deletion.
                assert alice.post("/api/v1/auth/logout", headers=ah).status_code == 204
                assert alice.post(case_path + "/route/check", headers=ah).status_code == 401
    print("HTTP package 7 passed: real cases/archive/PDF/checks/transit/events, ownership/CSRF, unknown baggage, process restart and logout; no AI/booking calls")


if __name__ == "__main__":
    main()
