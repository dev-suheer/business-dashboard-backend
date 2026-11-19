from argparse import Namespace
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_password_hash
from app.db.session import get_engine, get_session
from app.main import app

FIXTURES = Path(__file__).parent / "fixtures"
WORKINGS = FIXTURES / "workings.xlsx"
VENDOR_STATEMENT = FIXTURES / "vendor_statement.xlsx"

TRAVEL_FIXTURES = FIXTURES / "travel"
TRAVEL_PORTAL = TRAVEL_FIXTURES / "portal.xlsx"
TRAVEL_WALLET = TRAVEL_FIXTURES / "wallet_history.xlsx"
# One weekly claim per file, as they arrive. Claim 13 keeps every tab it came
# with, including the client's own Analysis sheet, because that is what proves
# the schemas do not mistake a worked reconciliation for an input.
TRAVEL_CLAIMS = {
    n: TRAVEL_FIXTURES / f"claim_{n}.xlsx" for n in ("10", "11", "12", "13")
}

FOOD_FIXTURES = FIXTURES / "food"
FOOD_PORTAL = FOOD_FIXTURES / "portal.xlsx"
FOOD_VENDOR = FOOD_FIXTURES / "vendor.xlsx"

TEST_USERNAME = "tester"
TEST_PASSWORD = "correct horse battery"
# What a test may rename the tests' account to; removed with it.
RENAMED_USERNAME = "tester-renamed"


@pytest.fixture
def test_account(test_database_url: str) -> Iterator[str]:
    """The tests' own login, on the test database, with a password they know;
    removed after. The seeded account is left as it is."""
    engine = get_engine(test_database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "insert into account (username, password_hash) values (:u, :h) "
                "on conflict (username) do update set password_hash = :h"
            ),
            {"u": TEST_USERNAME, "h": get_password_hash(TEST_PASSWORD)},
        )
    yield TEST_USERNAME
    with engine.begin() as connection:
        connection.execute(
            text("delete from account where username in (:u, :renamed)"),
            {"u": TEST_USERNAME, "renamed": RENAMED_USERNAME},
        )


@pytest.fixture
def client(test_database_url: str, test_account: str) -> Iterator[TestClient]:  # noqa: ARG001
    """A client whose requests, sign-in included, use the test database: no
    test can read or change the real accounts."""
    engine = get_engine(test_database_url)

    def test_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = test_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture
def auth_client(client: TestClient) -> TestClient:
    """A client holding a live session cookie."""
    response = client.post(
        f"{settings.API_V1_STR}/login/access-token",
        data={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200, response.text
    assert settings.SESSION_COOKIE_NAME in client.cookies
    return client


ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def alembic_config(url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.cmd_opts = Namespace(x=[f"url={url}"])
    return config


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """The test database, migrated to head once per session.

    Tests that need it are skipped, not failed, on a machine with no Postgres.
    """
    url = settings.TEST_DATABASE_URL
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    command.upgrade(alembic_config(url), "head")
    return url


def _reset(url: str) -> None:
    """An empty run history, only the three seeded categories, and each of them
    back on version 1."""
    with get_engine(url).begin() as connection:
        connection.execute(text("TRUNCATE run_file, run RESTART IDENTITY CASCADE"))
        connection.execute(
            text(
                "UPDATE category SET current_version_id = NULL "
                "WHERE key NOT IN ('eshop', 'travel', 'food')"
            )
        )
        connection.execute(
            text(
                "DELETE FROM category_version WHERE category_id IN (SELECT id FROM "
                "category WHERE key NOT IN ('eshop', 'travel', 'food'))"
            )
        )
        connection.execute(
            text("DELETE FROM category WHERE key NOT IN ('eshop', 'travel', 'food')")
        )
        connection.execute(
            text(
                "UPDATE category c SET current_version_id = v.id FROM category_version v "
                "WHERE v.category_id = c.id AND v.number = 1"
            )
        )
        connection.execute(text("DELETE FROM category_version WHERE number > 1"))


@pytest.fixture
def db_session(test_database_url: str) -> Iterator[Session]:
    """A session on an empty run history, every category on version 1, and left so."""
    _reset(test_database_url)
    with Session(get_engine(test_database_url), expire_on_commit=False) as session:
        yield session
    _reset(test_database_url)


@pytest.fixture
def run_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run files go to a throwaway folder."""
    store = tmp_path / "store"
    monkeypatch.setattr(settings, "RUN_STORAGE_DIR", store)
    return store


@pytest.fixture
def runs_client(
    auth_client: TestClient,
    db_session: Session,
    run_store: Path,  # noqa: ARG001  (requested for its side effect)
) -> Iterator[TestClient]:
    """A logged-in client whose requests use the test database and run store."""
    engine = db_session.get_bind()

    def test_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = test_session
    yield auth_client
    app.dependency_overrides.pop(get_session, None)


FINANCE_HEADERS = (
    "Spin Event ID",
    "User ID",
    "Order ID",
    "Order Type",
    "Order Amount (SAR)",
    "Reward Amount (SAR)",
    "Reward Code",
    "Reward Type",
    "Outcome",
    "Spin Earned At (KSA)",
    "Reward Earned At (KSA)",
    "AOV Tier ID",
    "AOV Tier Name",
    "Vertical ID",
    "Vertical Name",
)


def write_finance_report(path: Path, order_ids: list[int], vertical: str) -> Path:
    """A spin report as the client sends it: a summary block above the table."""
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "CASHBACK"
    sheet["A1"] = "Total spins"
    sheet["B1"] = len(order_ids)
    for col, header in enumerate(FINANCE_HEADERS, start=1):
        sheet.cell(14, col, header)
    for offset, order_id in enumerate(order_ids):
        row = 15 + offset
        sheet.cell(row, 1, 61167 + offset)
        sheet.cell(row, 3, order_id)
        sheet.cell(row, 6, 4.13)
        sheet.cell(row, 7, "PERCENT_10")
        sheet.cell(row, 8, 0.1)
        sheet.cell(row, 9, "won")
        sheet.cell(row, 15, vertical)
    book.save(path)
    return path
