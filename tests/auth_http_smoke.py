"""Optional real-process smoke: python tests/auth_http_smoke.py (uses loopback)."""

import os
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import httpx


@contextmanager
def server(port, data_dir, *, environment_overrides=None):
    origin = f"http://127.0.0.1:{port}"
    environment = {
        **os.environ,
        "VISAPILOT_DATA_DIR": data_dir,
        "VISAPILOT_ALLOWED_ORIGINS": f'["{origin}"]',
        "VISAPILOT_COOKIE_SECURE": "false",
        "VISAPILOT_SESSION_COOKIE_NAME": "visapilot_session",
        "VISAPILOT_SESSION_TTL_SECONDS": "43200",
        **(environment_overrides or {}),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--workers", "1", "--no-access-log"],
        cwd=Path(__file__).resolve().parents[1], env=environment,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            if process.poll() is not None:
                raise RuntimeError("Smoke server did not start")
            try:
                if httpx.get(origin + "/api/v1/health", timeout=0.5).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("Smoke server startup timed out")
            time.sleep(0.05)
        yield
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main():
    with socket.socket() as address:
        address.bind(("127.0.0.1", 0))
        port = address.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="visapilot-auth-") as data_dir:
        with httpx.Client(base_url=origin, headers={"Origin": origin}, timeout=5) as alice:
            with server(port, data_dir):
                for name in ("sample_alice", "sample_bob"):
                    response = alice.post("/api/v1/auth/register", json={
                        "username": name, "password": "SAMPLE-Password-123",
                    })
                    assert response.status_code == 201
                response = alice.post("/api/v1/auth/login", json={
                    "username": "sample_alice", "password": "SAMPLE-Password-123",
                })
                assert response.status_code == 200
                session = response.json()
                token = alice.cookies.get("visapilot_session")
                assert token not in response.text
                assert alice.get("/api/v1/auth/me").json() == session
                assert alice.post("/api/v1/auth/logout").status_code == 403
                assert alice.get("/openapi.json").status_code == 200
            # The same browser cookie/CSRF must work in a new Uvicorn process.
            with server(port, data_dir):
                assert alice.get("/api/v1/auth/me").json() == session
                response = alice.post(
                    "/api/v1/auth/logout", headers={"X-CSRF-Token": session["csrf_token"]},
                )
                assert response.status_code == 204 and not response.content
                assert alice.get("/api/v1/auth/me").status_code == 401
                with httpx.Client(base_url=origin) as replay:
                    assert replay.get(
                        "/api/v1/auth/me", headers={"Cookie": f"visapilot_session={token}"},
                    ).status_code == 401
    print("HTTP auth smoke passed: two accounts, login/me, CSRF, process restart, logout and replay rejection")


if __name__ == "__main__":
    main()
