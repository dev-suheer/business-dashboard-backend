from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyCookie
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError

from app.core import security
from app.core.config import settings
from app.core.credentials import get_credential
from app.models import TokenPayload, UserPublic

# auto_error=False so a missing cookie produces our own 401 rather than a 403.
session_cookie = APIKeyCookie(name=settings.SESSION_COOKIE_NAME, auto_error=False)

TokenDep = Annotated[str | None, Depends(session_cookie)]

CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
)


def get_current_user(token: TokenDep) -> UserPublic:
    if not token:
        raise CREDENTIALS_EXCEPTION
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        token_data = TokenPayload(**payload)
    except (InvalidTokenError, ValidationError):
        # Covers an expired exp, a bad signature and a malformed payload alike.
        raise CREDENTIALS_EXCEPTION
    # There is exactly one account, so a token for any other subject is stale —
    # it was minted before the credential file was rewritten.
    if not token_data.sub or token_data.sub != get_credential().username:
        raise CREDENTIALS_EXCEPTION
    return UserPublic(username=token_data.sub)


CurrentUser = Annotated[UserPublic, Depends(get_current_user)]
