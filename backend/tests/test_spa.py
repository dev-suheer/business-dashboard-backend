"""The React build served by the API process, Express-style."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.spa import IMMUTABLE, REVALIDATE

INDEX = '<!doctype html><div id="root">APP-SHELL</div>'


@pytest.fixture
def build(tmp_path: Path) -> Path:
    """A minimal Vite-shaped build: index.html plus a hashed asset."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX)
    (dist / "assets" / "index-a1b2c3.js").write_text("console.log('app');" * 100)
    (dist / "favicon.svg").write_text("<svg/>")
    (dist / ".gitkeep").write_text("")
    return dist


@pytest.fixture
def web(build: Path) -> Iterator[TestClient]:
    with TestClient(create_app(frontend_dist=build)) as client:
        yield client


def test_root_serves_index(web: TestClient) -> None:
    response = web.get("/")
    assert response.status_code == 200
    assert "APP-SHELL" in response.text
    assert response.headers["cache-control"] == REVALIDATE


@pytest.mark.parametrize(
    "route", ["/orders", "/orders/SG-SH-05290", "/reports/2026/08"]
)
def test_client_side_routes_fall_back_to_index(web: TestClient, route: str) -> None:
    """A refresh on a React Router URL must not 404."""
    response = web.get(route)
    assert response.status_code == 200
    assert "APP-SHELL" in response.text
    assert response.headers["cache-control"] == REVALIDATE


def test_real_files_are_served(web: TestClient) -> None:
    response = web.get("/favicon.svg")
    assert response.status_code == 200
    assert response.text == "<svg/>"


def test_hashed_assets_are_cached_for_a_year(web: TestClient) -> None:
    response = web.get("/assets/index-a1b2c3.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == IMMUTABLE


def test_a_missing_asset_is_a_real_404_not_the_app_shell(web: TestClient) -> None:
    """Returning index.html for a missing .js would break the page confusingly."""
    response = web.get("/assets/index-deadbeef.js")
    assert response.status_code == 404
    assert "APP-SHELL" not in response.text


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_unknown_api_paths_stay_json_404s(web: TestClient, method: str) -> None:
    """An API client must never receive a 200 full of HTML."""
    response = web.request(method, "/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_api_routes_still_win(web: TestClient) -> None:
    assert web.get("/api/v1/utils/health-check/").json() is True
    assert web.get("/api/v1/login/me").status_code == 401


def test_docs_still_served_by_the_api(web: TestClient) -> None:
    response = web.get("/docs")
    assert response.status_code == 200
    assert "swagger" in response.text.lower()
    assert "APP-SHELL" not in response.text


def test_dotfiles_are_never_served(web: TestClient) -> None:
    assert web.get("/.gitkeep").status_code == 404
    assert web.get("/.env").status_code == 404


def test_path_traversal_is_refused(web: TestClient) -> None:
    for attempt in (
        "/../credentials.json",
        "/%2e%2e/%2e%2e/etc/passwd",
        "/assets/../../x",
    ):
        response = web.get(attempt)
        assert "root:" not in response.text
        assert "password_hash" not in response.text


def test_a_new_build_is_picked_up_without_a_restart(
    web: TestClient, build: Path
) -> None:
    (build / "index.html").write_text('<div id="root">BUILD-V2</div>')
    assert "BUILD-V2" in web.get("/").text


def test_missing_build_is_a_clear_503_and_the_api_still_works(tmp_path: Path) -> None:
    with TestClient(create_app(frontend_dist=tmp_path / "not-copied-yet")) as client:
        response = client.get("/")
        assert response.status_code == 503
        assert "frontend/dist" in response.text
        # The server started and the API is unaffected.
        assert client.get("/api/v1/utils/health-check/").json() is True


def test_security_headers_on_static_and_api(web: TestClient) -> None:
    for path in ("/", "/api/v1/utils/health-check/"):
        headers = web.get(path).headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_large_responses_are_gzipped(web: TestClient) -> None:
    response = web.get("/assets/index-a1b2c3.js", headers={"Accept-Encoding": "gzip"})
    assert response.headers.get("content-encoding") == "gzip"
