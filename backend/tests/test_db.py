"""The database: migrations apply cleanly to an empty database, and back out."""

from alembic import command
from sqlalchemy import create_engine, text

from tests.conftest import alembic_config


def _current_revision(url: str) -> str | None:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return connection.execute(
                text("select version_num from alembic_version")
            ).scalar()
    finally:
        engine.dispose()


def test_migrations_apply_from_scratch_and_back_out(test_database_url: str) -> None:
    config = alembic_config(test_database_url)
    command.downgrade(config, "base")
    assert _current_revision(test_database_url) is None

    command.upgrade(config, "head")
    assert _current_revision(test_database_url) is not None


def test_the_three_categories_are_seeded(test_database_url: str) -> None:
    engine = create_engine(test_database_url)
    try:
        with engine.connect() as connection:
            keys = connection.execute(
                text("select key from category order by id")
            ).scalars()
            assert list(keys) == ["eshop", "travel", "food"]
    finally:
        engine.dispose()
