from datetime import timedelta
from http.cookies import SimpleCookie

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.credentials import authenticate, find_account
from app.core.security import create_access_token, get_password_hash
from app.db.session import get_engine
from tests.conftest import RENAMED_USERNAME, TEST_PASSWORD, TEST_USERNAME

LOGIN = f"{settings.API_V1_STR}/login/access-token"
LOGOUT = f"{settings.API_V1_STR}/logout"
ME = f"{settings.API_V1_STR}/login/me"
RUNS = f"{settings.API_V1_STR}/runs"


def _set_cookie(response: object) -> SimpleCookie:
    jar = SimpleCookie()
    jar.load(response.headers["set-cookie"])  # type: ignore[attr-defined]
    return jar


def test_login_sets_an_httponly_six_hour_cookie(client: TestClient) -> None:
    response = client.post(
        LOGIN, data={"username": TEST_USERNAME, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200
    assert response.json() == {"username": TEST_USERNAME}

    morsel = _set_cookie(response)[settings.SESSION_COOKIE_NAME]
    assert morsel["httponly"], "the token must be unreachable from JavaScript"
    assert morsel["max-age"] == "21600", "the session must last exactly six hours"
    assert morsel["samesite"].lower() == settings.COOKIE_SAMESITE
    assert morsel["path"] == "/"


def test_token_is_never_returned_in_the_body(client: TestClient) -> None:
    response = client.post(
        LOGIN, data={"username": TEST_USERNAME, "password": TEST_PASSWORD}
    )
    assert "access_token" not in response.text


def test_wrong_password_is_rejected(client: TestClient) -> None:
    response = client.post(
        LOGIN, data={"username": TEST_USERNAME, "password": "not the password"}
    )
    assert response.status_code == 400
    assert settings.SESSION_COOKIE_NAME not in client.cookies


def test_wrong_username_is_rejected(client: TestClient) -> None:
    response = client.post(
        LOGIN, data={"username": "someone-else", "password": TEST_PASSWORD}
    )
    assert response.status_code == 400


def test_me_requires_the_cookie(client: TestClient) -> None:
    assert client.get(ME).status_code == 401


def test_me_returns_the_account_when_authenticated(auth_client: TestClient) -> None:
    response = auth_client.get(ME)
    assert response.status_code == 200
    assert response.json() == {"username": TEST_USERNAME}


def test_runs_are_unreachable_without_the_cookie(client: TestClient) -> None:
    assert client.post(RUNS).status_code == 401


def test_expired_token_is_401_not_403(client: TestClient) -> None:
    expired = create_access_token(TEST_USERNAME, expires_delta=timedelta(minutes=-5))
    client.cookies.set(settings.SESSION_COOKIE_NAME, expired)
    assert client.get(ME).status_code == 401


def test_garbage_cookie_is_rejected(client: TestClient) -> None:
    client.cookies.set(settings.SESSION_COOKIE_NAME, "not-a-jwt")
    assert client.get(ME).status_code == 401


def test_token_for_another_subject_is_rejected(client: TestClient) -> None:
    # A token for a username no account has must not work.
    stale = create_access_token("previous-admin", expires_delta=timedelta(minutes=30))
    client.cookies.set(settings.SESSION_COOKIE_NAME, stale)
    assert client.get(ME).status_code == 401


def test_logout_clears_the_cookie(auth_client: TestClient) -> None:
    response = auth_client.post(LOGOUT)
    assert response.status_code == 200
    morsel = _set_cookie(response)[settings.SESSION_COOKIE_NAME]
    assert morsel.value == ""
    assert morsel["path"] == "/", (
        "a mismatched path leaves the original cookie in place"
    )


def test_logout_works_without_a_session(client: TestClient) -> None:
    # Logging out with an already-expired cookie should still clear it.
    assert client.post(LOGOUT).status_code == 200


def _session(url: str) -> Session:
    return Session(get_engine(url), expire_on_commit=False)


def test_an_account_is_stored_only_as_a_hash(
    test_database_url: str, test_account: str
) -> None:
    with _session(test_database_url) as session:
        account = find_account(session, test_account)
    assert account is not None
    assert TEST_PASSWORD not in account.password_hash
    assert account.password_hash.startswith("$argon2")


def test_authenticate_accepts_and_rejects(
    test_database_url: str,
    test_account: str,  # noqa: ARG001
) -> None:
    with _session(test_database_url) as session:
        assert authenticate(session, TEST_USERNAME, TEST_PASSWORD) is not None
        assert authenticate(session, TEST_USERNAME, "wrong") is None
        assert authenticate(session, "wrong", TEST_PASSWORD) is None


def _set(url: str, sql: str, **values: str) -> None:
    with get_engine(url).begin() as connection:
        connection.execute(text(sql), values)


def test_a_password_change_takes_effect_without_a_restart(
    client: TestClient, test_database_url: str
) -> None:
    """Every worker reads the account from the database, so a new password
    holds at once."""
    login = {"username": TEST_USERNAME, "password": TEST_PASSWORD}
    assert client.post(LOGIN, data=login).status_code == 200
    _set(
        test_database_url,
        "update account set password_hash = :h where username = :u",
        h=get_password_hash("a-new-password"),
        u=TEST_USERNAME,
    )
    assert client.post(LOGIN, data=login).status_code == 400
    assert (
        client.post(
            LOGIN, data={"username": TEST_USERNAME, "password": "a-new-password"}
        ).status_code
        == 200
    )


def test_a_renamed_account_invalidates_existing_sessions(
    auth_client: TestClient, test_database_url: str
) -> None:
    """A session is bound to the username; renaming the account ends it."""
    assert auth_client.get(ME).status_code == 200
    _set(
        test_database_url,
        "update account set username = :new where username = :u",
        new=RENAMED_USERNAME,
        u=TEST_USERNAME,
    )
    assert auth_client.get(ME).status_code == 401


def test_a_removed_account_can_neither_sign_in_nor_stay_signed_in(
    auth_client: TestClient, test_database_url: str
) -> None:
    assert auth_client.get(ME).status_code == 200
    _set(test_database_url, "delete from account where username = :u", u=TEST_USERNAME)
    assert auth_client.get(ME).status_code == 401
    login = {"username": TEST_USERNAME, "password": TEST_PASSWORD}
    assert auth_client.post(LOGIN, data=login).status_code == 400


def test_the_seeded_account_is_in_the_database(test_database_url: str) -> None:
    """``app/seed/users.json`` is what a new database's logins are."""
    import json
    from pathlib import Path

    seed = json.loads(
        (
            Path(__file__).resolve().parents[1] / "app" / "seed" / "users.json"
        ).read_text()
    )
    assert seed and all(set(u) == {"username", "password_hash"} for u in seed)
    with _session(test_database_url) as session:
        for user in seed:
            account = find_account(session, user["username"])
            assert account is not None
            assert account.password_hash == user["password_hash"]
