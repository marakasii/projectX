import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def test_without_frontend_root_opens_live_swagger(settings, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 307 and response.headers["location"] == "/docs"
        assert client.get("/").status_code == 200


def test_existing_frontend_is_served(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200 and '<html' in response.text


def test_frontend_bundle_api_spa_refresh_and_private_paths(settings, tmp_path):
    # Independently built frontend fixture; no replacement of the team's UI.
    build = tmp_path / "frontend-build"
    build.mkdir()
    (build / "index.html").write_text('<!doctype html><title>SAMPLE frontend fixture</title>')
    (build / "app.js").write_text('console.log("SAMPLE frontend fixture")')
    settings.frontend_dist = build
    with TestClient(create_app(settings)) as client:
        assert "SAMPLE frontend fixture" in client.get("/").text
        assert "SAMPLE frontend fixture" in client.get("/cabinet/cases/sample").text
        assert "javascript" in client.get("/app.js").headers["content-type"]
        assert client.get("/api/v1/health").json()["status"] == "ok"
        assert client.get("/api/v1/destinations").status_code == 200
        assert client.get("/docs").status_code == 200
        for path in ("/api/v1/not-real", "/local_data/uploads/sample.pdf", "/.env", "/.git/config", "/missing.js"):
            response = client.get(path)
            assert response.status_code == 404 and "SAMPLE frontend fixture" not in response.text


def test_invalid_or_private_frontend_directory_is_rejected(settings, tmp_path):
    settings.frontend_dist = tmp_path / "missing-build"
    with pytest.raises(ValueError, match="index.html"):
        create_app(settings)
    settings.frontend_dist = settings.data_dir
    settings.data_dir.mkdir(parents=True)
    (settings.data_dir / "index.html").write_text("private")
    with pytest.raises(ValueError, match="private data"):
        create_app(settings)
