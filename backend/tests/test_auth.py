from datetime import timedelta
from http.cookies import SimpleCookie

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.credentials import authenticate, get_credential
from app.core.security import create_access_token
from tests.conftest import TEST_PASSWORD, TEST_USERNAME

LOGIN = f"{settings.API_V1_STR}/login/access-token"
LOGOUT = f"{settings.API_V1_STR}/logout"
ME = f"{settings.API_V1_STR}/login/me"
RECONCILE = f"{settings.API_V1_STR}/reconcile"


def _set_cookie(response: object) -> SimpleCookie:
    jar = SimpleCookie()
    jar.load(response.headers["set-cookie"])  # type: ignore[attr-defined]
    return jar


def test_login_sets_an_httponly_one_hour_cookie(client: TestClient) -> None:
    response = client.post(
        LOGIN, data={"username": TEST_USERNAME, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200
    assert response.json() == {"username": TEST_USERNAME}

    morsel = _set_cookie(response)[settings.SESSION_COOKIE_NAME]
    assert morsel["httponly"], "the token must be unreachable from JavaScript"
    assert morsel["max-age"] == "3600", "the session must last exactly one hour"
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


def test_reconcile_is_unreachable_without_the_cookie(client: TestClient) -> None:
    assert client.post(RECONCILE).status_code == 401


def test_expired_token_is_401_not_403(client: TestClient) -> None:
    expired = create_access_token(TEST_USERNAME, expires_delta=timedelta(minutes=-5))
    client.cookies.set(settings.SESSION_COOKIE_NAME, expired)
    assert client.get(ME).status_code == 401


def test_garbage_cookie_is_rejected(client: TestClient) -> None:
    client.cookies.set(settings.SESSION_COOKIE_NAME, "not-a-jwt")
    assert client.get(ME).status_code == 401


def test_token_for_another_subject_is_rejected(client: TestClient) -> None:
    # A token minted before the credential file was rewritten must not work.
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


def test_credentials_are_stored_only_as_a_hash() -> None:
    credential = get_credential()
    assert TEST_PASSWORD not in credential.password_hash
    assert credential.password_hash.startswith("$argon2")


def test_authenticate_accepts_and_rejects() -> None:
    assert authenticate(TEST_USERNAME, TEST_PASSWORD) is not None
    assert authenticate(TEST_USERNAME, "wrong") is None
    assert authenticate("wrong", TEST_PASSWORD) is None


def test_a_password_change_takes_effect_without_a_restart(credential_file) -> None:
    """Every worker must agree on the password as soon as the file changes."""
    import json

    from app.core.security import get_password_hash

    assert authenticate(TEST_USERNAME, TEST_PASSWORD) is not None
    credential_file.write_text(
        json.dumps(
            {
                "username": TEST_USERNAME,
                "password_hash": get_password_hash("a-new-password"),
            }
        )
    )
    assert authenticate(TEST_USERNAME, "a-new-password") is not None
    assert authenticate(TEST_USERNAME, TEST_PASSWORD) is None


def test_a_renamed_account_invalidates_existing_sessions(
    auth_client: TestClient, credential_file
) -> None:
    """A session is bound to the username; renaming the account ends it."""
    import json

    from app.core.security import get_password_hash

    assert auth_client.get(ME).status_code == 200
    credential_file.write_text(
        json.dumps(
            {"username": "someone-new", "password_hash": get_password_hash("x" * 12)}
        )
    )
    assert auth_client.get(ME).status_code == 401


def test_a_deleted_credential_file_fails_loudly(credential_file) -> None:
    import pytest

    from app.core.credentials import CredentialError

    credential_file.unlink()
    with pytest.raises(CredentialError, match="create_credentials"):
        get_credential()
