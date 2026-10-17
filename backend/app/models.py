from pydantic import BaseModel


# Generic message
class Message(BaseModel):
    message: str


# Contents of the JWT carried by the session cookie
class TokenPayload(BaseModel):
    sub: str | None = None


# The single account, as returned to the caller. There is no id and no database
# row behind it — the account is a line in a JSON file.
class UserPublic(BaseModel):
    username: str
