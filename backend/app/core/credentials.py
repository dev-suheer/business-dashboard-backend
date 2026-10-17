import json
import logging
import threading
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from app.core import security
from app.core.config import backend_path, settings

logger = logging.getLogger(__name__)


class CredentialError(RuntimeError):
    """The credential file is missing, unreadable or malformed."""


class Credential(BaseModel):
    """The one and only login account.

    Only the hash is ever stored; the plaintext password exists nowhere on disk.
    """

    username: str = Field(min_length=1, max_length=255)
    password_hash: str = Field(min_length=1)


def credentials_path() -> Path:
    """Resolve CREDENTIALS_FILE against ./backend/ so relative paths behave."""
    return backend_path(settings.CREDENTIALS_FILE)


def _read(path: Path) -> Credential:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise CredentialError(
            f"No credential file at {path}. Create one with: "
            "uv run python app/create_credentials.py"
        )
    try:
        return Credential.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise CredentialError(f"Credential file at {path} is malformed: {exc}")


class _CredentialCache:
    """The parsed credential, re-read whenever the file changes on disk.

    Each worker process holds its own copy. Caching forever would leave workers
    disagreeing after a password change — some holding the old hash, some the
    new — so a login would succeed or fail depending on which worker answered.
    Keying on the file's identity (inode, mtime, size) costs one stat() per
    lookup and makes a change take effect in every worker immediately.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._signature: tuple[int, int, int] | None = None
        self._credential: Credential | None = None

    def get(self) -> Credential:
        path = credentials_path()
        try:
            st = path.stat()
        except FileNotFoundError:
            raise CredentialError(
                f"No credential file at {path}. Create one with: "
                "uv run python app/create_credentials.py"
            )
        signature = (st.st_ino, st.st_mtime_ns, st.st_size)
        with self._lock:
            if self._credential is None or signature != self._signature:
                self._credential = _read(path)
                self._signature = signature
            return self._credential

    def clear(self) -> None:
        with self._lock:
            self._signature = None
            self._credential = None


_cache = _CredentialCache()


def get_credential() -> Credential:
    """The account, re-read from disk only when the file has changed."""
    return _cache.get()


def clear_credential_cache() -> None:
    _cache.clear()


def write_credential(username: str, password: str) -> Path:
    """Hash `password` and persist the account, replacing any existing one."""
    credential = Credential(
        username=username, password_hash=security.get_password_hash(password)
    )
    path = credentials_path()
    path.write_text(credential.model_dump_json(indent=2) + "\n", encoding="utf-8")
    # Owner-only: the hash is not a secret in the way a password is, but there is
    # no reason for it to be world-readable either.
    path.chmod(0o600)
    clear_credential_cache()
    return path


def authenticate(username: str, password: str) -> Credential | None:
    """Verify a login attempt against the stored account.

    The hash is always verified, even when the username is wrong, so a bad
    username and a bad password take the same amount of time.
    """
    credential = get_credential()
    verified, updated_hash = security.verify_password(
        password, credential.password_hash
    )
    if not verified or username != credential.username:
        return None
    if updated_hash is not None:
        # pwdlib asked us to migrate the hash to a stronger scheme.
        _persist_hash_upgrade(credential, updated_hash)
    return credential


def _persist_hash_upgrade(credential: Credential, updated_hash: str) -> None:
    upgraded = credential.model_copy(update={"password_hash": updated_hash})
    try:
        path = credentials_path()
        path.write_text(upgraded.model_dump_json(indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        # A read-only deployment must not fail an otherwise valid login.
        logger.warning("Could not persist password hash upgrade", exc_info=True)
        return
    clear_credential_cache()
