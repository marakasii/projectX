import concurrent.futures
import json
import threading
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth.service import PASSWORDS, digest
from app.common.models import Credentials
from app.main import create_app

ORIGIN = {"Origin": "http://testserver"}
PASSWORD = "SAMPLE-Password-123"


def register(client, username="matvey"):
    return client.post(
        "/api/v1/auth/register", headers=ORIGIN,
        json={"username": username, "password": PASSWORD},
    )


def login(client, username="matvey", password=PASSWORD):
    return client.post(
        "/api/v1/auth/login", headers=ORIGIN,
        json={"username": username, "password": password},
    )


def test_registration_two_users_hashes_and_unknown_profile(client, caplog):
    first = register(client, "Matvey")
    second = register(client, "Vanya")
    assert first.status_code == second.status_code == 201
    assert first.json()["username"] == "matvey"
    assert first.json()["id"] != second.json()["id"]
    assert set(first.json()) == {"id", "username", "created_at"}
    assert "set-cookie" not in first.headers
    assert first.headers["cache-control"] == "no-store"
    assert client.get("/api/v1/auth/me").status_code == 401
    assert register(client, "mATVEY").status_code == 409
    with client.app.state.database.connection() as connection:
        users = connection.execute("SELECT * FROM users").fetchall()
        profiles = connection.execute("SELECT * FROM profiles").fetchall()
        assert len(users) == len(profiles) == 2
        for row in users:
            assert row["password_hash"].startswith("$argon2id$")
            assert PASSWORDS.verify(PASSWORD, row["password_hash"])
        assert {row["owner_id"] for row in profiles} == {row["id"] for row in users}
        assert all(json.loads(row["data_json"])["has_other_citizenship"] is None for row in profiles)
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert PASSWORD not in first.text + second.text + caplog.text
    assert PASSWORD not in repr(Credentials(username="matvey", password=PASSWORD))


def test_login_cookie_csrf_hash_storage_and_me(client, caplog):
    user = register(client).json()
    response = login(client, "MATVEY")
    assert response.status_code == 200
    payload = response.json()
    raw_token = client.cookies.get("visapilot_session")
    csrf = payload["csrf_token"]
    assert payload["user"] == user
    assert raw_token not in response.text
    assert len(raw_token) == 43 and len(csrf) == 64
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert "max-age=43200" in cookie and "path=/api/v1" in cookie
    assert "secure" not in cookie and "domain=" not in cookie
    with client.app.state.database.connection() as connection:
        row = connection.execute("SELECT * FROM sessions").fetchone()
        assert row["token_hash"] == digest(raw_token) and raw_token not in tuple(row)
        assert row["csrf_token_hash"] == digest(csrf) and csrf not in tuple(row)
        assert row["revoked_at"] is None
        assert datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["created_at"]) == timedelta(hours=12)
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json() == payload
    assert response.headers["cache-control"] == me.headers["cache-control"] == "no-store"
    assert PASSWORD not in response.text + me.text + caplog.text
    assert raw_token not in caplog.text


def test_wrong_credentials_match_and_failed_login_does_not_switch_user(client):
    register(client)
    good = login(client)
    token = client.cookies.get("visapilot_session")
    wrong = login(client, password="Incorrect-SAMPLE-password")
    missing = login(client, username="missing-user")
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json() == missing.json()
    assert "set-cookie" not in wrong.headers
    assert client.cookies.get("visapilot_session") == token
    assert client.get("/api/v1/auth/me").json() == good.json()


@pytest.mark.parametrize("path", ["register", "login"])
@pytest.mark.parametrize("origin", [
    None, "null", "http://evil.example", "http://testserver.evil.example",
    "https://testserver", "http://testserver:8000", "http://testserver/path",
    "http://name@testserver", "http://testserver:bad", "http://testserver,http://evil.example",
])
def test_origin_rejected_before_account_or_session_change(client, path, origin):
    headers = {} if origin is None else {"Origin": origin}
    response = client.post(
        f"/api/v1/auth/{path}", headers=headers,
        json={"username": "matvey", "password": PASSWORD},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"
    assert "set-cookie" not in response.headers
    with client.app.state.database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_explicit_default_port_and_duplicate_origin_headers(client):
    response = client.post(
        "/api/v1/auth/register", headers={"Origin": "http://testserver:80"},
        json={"username": "matvey", "password": PASSWORD},
    )
    assert response.status_code == 201
    response = client.post(
        "/api/v1/auth/login", headers=[("Origin", "http://testserver"), ("Origin", "http://evil.example")],
        json={"username": "matvey", "password": PASSWORD},
    )
    assert response.status_code == 403


def test_registration_does_not_switch_session_and_successful_login_rotates(client, settings):
    first_user = register(client).json()
    first_login = login(client).json()
    old_token = client.cookies.get("visapilot_session")
    other_user = register(client, "vanya").json()
    assert client.get("/api/v1/auth/me").json()["user"] == first_user
    assert client.cookies.get("visapilot_session") == old_token
    second = login(client, "vanya").json()
    assert second["user"] == other_user
    assert second["csrf_token"] != first_login["csrf_token"]
    assert client.cookies.get("visapilot_session") != old_token
    with TestClient(create_app(settings)) as replay:
        response = replay.get("/api/v1/auth/me", headers={"Cookie": f"visapilot_session={old_token}"})
        assert response.status_code == 401


def test_csrf_logout_and_replay(client, settings):
    register(client)
    payload = login(client).json()
    token = client.cookies.get("visapilot_session")
    for headers in ({}, {"X-CSRF-Token": "bad"}, {"X-CSRF-Token": "a" * 64}):
        denied = client.post("/api/v1/auth/logout", headers=headers)
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "csrf_failed"
        assert client.get("/api/v1/auth/me").status_code == 200
    csrf = payload["csrf_token"]
    denied = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf, "Origin": "http://evil.example"})
    assert denied.status_code == 403
    denied = client.post("/api/v1/auth/logout", headers=[("X-CSRF-Token", csrf), ("X-CSRF-Token", csrf)])
    assert denied.status_code == 403
    response = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 204 and response.content == b""
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert "path=/api/v1" in response.headers["set-cookie"].lower()
    assert client.cookies.get("visapilot_session") is None
    assert client.get("/api/v1/auth/me").status_code == 401
    with TestClient(create_app(settings)) as replay:
        assert replay.get("/api/v1/auth/me", headers={"Cookie": f"visapilot_session={token}"}).status_code == 401
    with client.app.state.database.connection() as connection:
        assert connection.execute("SELECT revoked_at FROM sessions").fetchone()[0] is not None


def test_csrf_from_another_session_is_rejected(client, settings):
    register(client)
    login(client)
    with TestClient(create_app(settings)) as other:
        register(other, "vanya")
        csrf = login(other, "vanya").json()["csrf_token"]
    response = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 403
    assert client.get("/api/v1/auth/me").status_code == 200


def test_session_expiry_is_absolute_and_malformed_cookie_is_unauthorized(client):
    register(client)
    login(client)
    with client.app.state.database.connection() as connection:
        expires_at = datetime.fromisoformat(connection.execute("SELECT expires_at FROM sessions").fetchone()[0])
    client.app.state.auth_service._clock = lambda: expires_at - timedelta(seconds=1)
    assert client.get("/api/v1/auth/me").status_code == 200
    client.app.state.auth_service._clock = lambda: expires_at
    assert client.get("/api/v1/auth/me").status_code == 401
    client.cookies.clear()
    response = client.get("/api/v1/auth/me", headers={"Cookie": "visapilot_session=bad-token"})
    assert response.status_code == 401


def test_session_and_csrf_survive_restart(settings):
    with TestClient(create_app(settings)) as first:
        register(first)
        payload = login(first).json()
        token = first.cookies.get("visapilot_session")
    with TestClient(create_app(settings)) as restarted:
        restarted.cookies.set("visapilot_session", token, path="/api/v1")
        assert restarted.get("/api/v1/auth/me").json() == payload
        # Stored CSRF hashes remain verifiable without a new server secret.
        response = restarted.post("/api/v1/auth/logout", headers={"X-CSRF-Token": payload["csrf_token"]})
        assert response.status_code == 204


def test_secure_cookie_and_custom_name(settings):
    settings.cookie_secure = True
    settings.session_cookie_name = "custom_session"
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        register(client)
        response = login(client)
        assert "secure" in response.headers["set-cookie"].lower()
        assert "custom_session=" in response.headers["set-cookie"]
        assert client.get("/api/v1/auth/me").status_code == 200
        scheme = client.get("/openapi.json").json()["components"]["securitySchemes"]["SessionCookie"]
        assert scheme["name"] == "custom_session"


def test_feature_and_pending_routes_require_real_session_and_csrf(client):
    assert client.get("/api/v1/documents").status_code == 401
    assert client.get("/api/v1/cases").status_code == 401
    assert client.post("/api/v1/cases", json={}).status_code == 401
    register(client)
    payload = login(client).json()
    assert client.get("/api/v1/documents").json() == {"items": []}
    assert client.post("/api/v1/cases", json={}).status_code == 403
    response = client.post(
        "/api/v1/cases", headers={"X-CSRF-Token": payload["csrf_token"]},
        json={"scenario_id": "JP_TOURISM", "trip": {}},
    )
    assert response.status_code == 201
    pending = client.post(f"/api/v1/cases/{response.json()['id']}/route/check", headers={"X-CSRF-Token": payload["csrf_token"]})
    assert pending.status_code == 503
    assert pending.json()["error"]["details"] == {"package": 7}


def test_hashing_does_not_block_health(client, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    original_hash = PASSWORDS.hash

    def slow_hash(password):
        started.set()
        assert release.wait(5)
        return original_hash(password)

    monkeypatch.setattr(PASSWORDS, "hash", slow_hash)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        operation = pool.submit(register, client)
        try:
            assert started.wait(2)
            assert client.get("/api/v1/health").status_code == 200
        finally:
            release.set()
        assert operation.result(timeout=5).status_code == 201
