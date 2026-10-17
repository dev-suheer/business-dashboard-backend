from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import CurrentUser
from app.core import security
from app.core.config import settings
from app.core.credentials import authenticate
from app.models import Message, UserPublic

router = APIRouter(tags=["login"])


@router.post("/login/access-token")
def login_access_token(
    response: Response,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> UserPublic:
    """
    Log in the single account and set the session cookie.

    The token is returned only as an HttpOnly cookie, never in the body.
    """
    credential = authenticate(username=form_data.username, password=form_data.password)
    if not credential:
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    token = security.create_access_token(
        credential.username,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    security.set_session_cookie(response, token)
    return UserPublic(username=credential.username)


@router.post("/logout")
def logout(response: Response) -> Message:
    """
    Clear the session cookie.

    Deliberately unauthenticated: logging out with an already-expired cookie
    should still clear it rather than fail.
    """
    security.clear_session_cookie(response)
    return Message(message="Logged out")


@router.get("/login/me")
def read_current_user(current_user: CurrentUser) -> UserPublic:
    """
    Return the logged-in account, confirming the session cookie is still valid.
    """
    return current_user
