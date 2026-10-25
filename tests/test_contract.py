import json
import re
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.common.models import (
    CaseCreate, EventCreate, Money, ProfileInput, Readiness, Transfer,
)
from app.main import create_app


def test_health_and_docs(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "demo_mode": True, "database": "ok", "schema_version": 1,
    }
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_openapi_covers_contract_and_excludes_private_fields(client):
    contract = Path("docs/contracts.md").read_text(encoding="utf-8")
    expected = {
        (method.lower(), "/api/v1" + path)
        for method, path in re.findall(r"\| (GET|POST|PUT|PATCH|DELETE) (\S+) \|", contract)
    }
    schema = client.get("/openapi.json").json()
    actual = {(method, path) for path, entry in schema["paths"].items() for method in entry}
    # The prose table abbreviates this one parameter; the URI shape is identical.
    expected = {(method, path.replace("/scenarios/{id}", "/scenarios/{scenario_id}"))
                for method, path in expected}
    assert actual == expected
    for path, entry in schema["paths"].items():
        for method, operation in entry.items():
            assert operation["responses"]["422"]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }
            if path != "/api/v1/health":
                implemented = operation["x-package"] in {11, 2, 3, 4, 5, 9}
                expected_state = "implemented" if implemented else "pending"
                assert operation["x-implementation"] == expected_state
                assert "503" in operation["responses"]
            public = path in {
                "/api/v1/health", "/api/v1/destinations", "/api/v1/scenarios/{scenario_id}",
                "/api/v1/auth/login", "/api/v1/auth/register",
            }
            assert bool(operation.get("security")) is not public
            if not public and method != "get":
                assert any(p["name"] == "X-CSRF-Token" for p in operation["parameters"])
    assert schema["components"]["securitySchemes"]["SessionCookie"] == {
        "type": "apiKey", "in": "cookie", "name": "visapilot_session",
    }
    components = schema["components"]["schemas"]
    for resource in ("User", "Document", "Case", "Letter"):
        properties = components[resource]["properties"]
        assert not {"password_hash", "token_hash", "owner_id", "storage_key", "raw_text"} & properties.keys()
    assert "multipart/form-data" in schema["paths"]["/api/v1/documents"]["post"]["requestBody"]["content"]
    assert set(schema["paths"]["/api/v1/documents/{document_id}/file"]["get"]["responses"]["200"]["content"]) == {"application/octet-stream"}
    assert set(schema["paths"]["/api/v1/letters/{letter_id}/file"]["get"]["responses"]["200"]["content"]) == {"text/plain"}
    for path, entry in schema["paths"].items():
        if "delete" in entry:
            assert "content" not in entry["delete"]["responses"]["204"]


def test_errors_do_not_echo_credentials_or_paths(client):
    response = client.post("/api/v1/auth/register", json={
        "username": "x", "password": "secret", "owner_id": "/private/secret/path",
    }, headers={"Origin": "http://testserver"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "secret" not in response.text
    assert "input" not in response.text
    missing = client.get("/local_data/uploads/private.pdf")
    assert missing.status_code == 404
    assert missing.json() == {"error": {"code": "not_found", "message": "Not Found", "details": {}}}
    assert client.post("/api/v1/health").json()["error"]["code"] == "method_not_allowed"


def test_unexpected_error_is_sanitized(settings):
    application = create_app(settings)

    @application.get("/broken", include_in_schema=False)
    def broken():
        raise RuntimeError("password=private /local/secret.db")

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/broken")
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "internal_error"
        assert "private" not in response.text
        assert "secret.db" not in response.text


def test_models_preserve_unknowns_and_wire_types():
    assert ProfileInput().has_other_citizenship is None
    assert ProfileInput(has_other_citizenship=False).has_other_citizenship is False
    assert Transfer(country="TR").rechecks_baggage is None
    assert Readiness().percent is None
    assert Readiness().ready is False
    assert json.loads(Money(amount="1234.50", currency="EUR").model_dump_json())["amount"] == "1234.50"
    with pytest.raises(ValidationError):
        ProfileInput(has_other_citizenship="false")
    with pytest.raises(ValidationError):
        Money(amount=1234.5, currency="EUR")
    with pytest.raises(ValidationError):
        CaseCreate(scenario_id="FR_TOURISM", trip={}, owner_id=str(uuid4()))
    with pytest.raises(ValidationError):
        EventCreate(event_type="submitted", occurred_at="2026-10-25T12:00:00")
    with pytest.raises(ValidationError):
        CaseCreate(scenario_id="FR_TOURISM", trip={"starts_on": "2027-02-02", "ends_on": "2027-02-01"})


def test_real_router_replaces_pending_routes_without_duplicates(settings):
    router = APIRouter()

    @router.get("/destinations")
    def fixture():
        return {"items": [], "fixture": True}

    application = create_app(settings, feature_routers={2: router})
    with TestClient(application) as client:
        assert client.get("/api/v1/destinations").json()["fixture"] is True
        assert "x-implementation" not in client.get("/openapi.json").json()["paths"]["/api/v1/destinations"]["get"]
