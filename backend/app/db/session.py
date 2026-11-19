"""The database connection.

One engine per process, created lazily so that importing the app (as the test
suite does for every category) never needs a database. Sessions are short-lived:
one per request or per unit of work.
"""

from collections.abc import Iterator
from functools import cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


@cache
def get_engine(url: str | None = None) -> Engine:
    # pool_pre_ping: a connection the database dropped while idle is replaced
    # rather than failing the first request that picks it up.
    return create_engine(url or settings.DATABASE_URL, pool_pre_ping=True)


@cache
def _session_factory(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(get_engine(url), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, closed when it is done."""
    with _session_factory()() as session:
        yield session
