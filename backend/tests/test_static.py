"""Single-origin frontend serving.

The deployment target is one container serving API, WebSocket and UI. That only
works if the static mount cannot shadow the API, and if SPA deep links resolve
without turning genuinely missing assets into confusing HTML responses.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.static import mount_frontend


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>app</title>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    return tmp_path


@pytest.fixture
def client(dist):
    app = FastAPI()

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    assert mount_frontend(app, dist) is True
    return TestClient(app)


def test_serves_index_at_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "<!doctype html>" in response.text


def test_serves_real_assets(client):
    response = client.get("/assets/app.js")
    assert response.status_code == 200
    assert "console.log" in response.text


def test_api_routes_are_not_shadowed_by_the_catch_all_mount(client):
    """Registration order matters: a mount at "/" must not swallow /api."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_spa_deep_links_fall_back_to_index(client):
    """A client-side route the server has never heard of is a deep link."""
    response = client.get("/some/deep/route")
    assert response.status_code == 200
    assert "<!doctype html>" in response.text


def test_missing_assets_still_404(client):
    """Serving index.html for a missing .js produces a baffling MIME error.

    An honest 404 is far easier to debug, so paths that look like files keep it.
    """
    for path in ["/assets/nope.js", "/missing.css", "/favicon.png"]:
        assert client.get(path).status_code == 404, path


def test_mount_is_skipped_when_no_build_exists(tmp_path):
    """A credential-free `uvicorn backend.main:app` must still start."""
    app = FastAPI()
    assert mount_frontend(app, tmp_path / "does-not-exist") is False
