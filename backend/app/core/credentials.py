"""The logins: each an ``account`` row, its username and its password's hash.

They are seeded from ``app/seed/users.json`` by a migration; the password itself
is stored nowhere.
"""

import logging
from functools import cache

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core import security
from app.db.models import Account

logger = logging.getLogger(__name__)


@cache
def _decoy_hash() -> str:
    """A hash to verify against when no account has the username, so a bad
    username takes as long as a bad password."""
    return security.get_password_hash("no account has this password")


def find_account(session: Session, username: str) -> Account | None:
    return session.scalar(select(Account).where(Account.username == username))


def authenticate(session: Session, username: str, password: str) -> Account | None:
    """The account the username and password are for, or None.

    The hash is always verified, even when the username is wrong, so a bad
    username and a bad password take the same amount of time.
    """
    account = find_account(session, username)
    verified, updated_hash = security.verify_password(
        password, account.password_hash if account is not None else _decoy_hash()
    )
    if account is None or not verified:
        return None
    if updated_hash is not None:
        # pwdlib asked us to migrate the hash to a stronger scheme.
        try:
            account.password_hash = updated_hash
            session.commit()
        except SQLAlchemyError:
            # A failed upgrade must not fail an otherwise valid login.
            session.rollback()
            logger.warning("Could not persist password hash upgrade", exc_info=True)
    return account
