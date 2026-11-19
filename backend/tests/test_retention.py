"""Retention: off unless set, only a list unless told to apply, and then old
runs go whole: History row, uploads and report."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Run, RunFile
from app.prune_runs import main
from tests.conftest import FOOD_PORTAL, FOOD_VENDOR

RUNS = f"{settings.API_V1_STR}/runs"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _run(client: TestClient) -> int:
    files = {
        role: (path.name, path.read_bytes(), XLSX)
        for role, path in {"portal": FOOD_PORTAL, "vendor": FOOD_VENDOR}.items()
    }
    response = client.post(RUNS, data={"category": "food"}, files=files)
    assert response.status_code == 201, response.text
    number: int = response.json()["number"]
    return number


@pytest.fixture
def two_runs(
    runs_client: TestClient,
    db_session: Session,
    test_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[int, int]:
    """Run 1 started 40 days ago, run 2 today; a 30-day limit; the test database."""
    old, new = _run(runs_client), _run(runs_client)
    run = db_session.get(Run, old)
    assert run is not None
    run.started_at = datetime.now(UTC) - timedelta(days=40)
    db_session.commit()
    monkeypatch.setattr(settings, "DATABASE_URL", test_database_url)
    monkeypatch.setattr(settings, "RUN_RETENTION_DAYS", 30)
    return old, new


def _folder(store: Path, number: int) -> Path:
    return store / "runs" / f"{number:06d}"


@pytest.mark.usefixtures("two_runs")
def test_retention_is_off_unless_set(
    runs_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(settings, "RUN_RETENTION_DAYS", None)
    assert main(["--apply"]) == 0
    assert "Retention is off" in capsys.readouterr().out
    assert runs_client.get(RUNS).json()["total"] == 2


def test_without_apply_it_only_lists_the_old_runs(
    two_runs: tuple[int, int],
    runs_client: TestClient,
    run_store: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old, new = two_runs
    assert main([]) == 0
    out = capsys.readouterr().out
    assert f"Run {old:04d}" in out and f"Run {new:04d}" not in out
    assert "Nothing removed" in out
    assert runs_client.get(RUNS).json()["total"] == 2
    assert _folder(run_store, old).is_dir()


def test_apply_removes_the_old_runs_whole(
    two_runs: tuple[int, int],
    runs_client: TestClient,
    run_store: Path,
    db_session: Session,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old, new = two_runs
    assert main(["--apply"]) == 0
    assert "Removed 1 run(s) older than 30 days" in capsys.readouterr().out

    assert runs_client.get(f"{RUNS}/{old}").status_code == 404
    assert not _folder(run_store, old).exists()
    files = select(func.count()).select_from(RunFile).where(RunFile.run_id == old)
    assert db_session.scalar(files) == 0

    assert runs_client.get(f"{RUNS}/{new}/report").status_code == 200
    assert _folder(run_store, new).is_dir()


@pytest.mark.usefixtures("two_runs")
def test_a_limit_given_for_once_wins(
    runs_client: TestClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--days", "60", "--apply"]) == 0
    assert "No run is older than 60 days" in capsys.readouterr().out
    assert runs_client.get(RUNS).json()["total"] == 2


def test_a_limit_under_a_day_is_refused() -> None:
    with pytest.raises(SystemExit):
        main(["--days", "0"])
