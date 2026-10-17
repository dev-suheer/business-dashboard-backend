"""Serve the React build from the API process — the FastAPI equivalent of

    app.use(express.static(buildPath))
    app.get("*", (req, res) => res.sendFile(path.join(buildPath, "index.html")))

Mounted last, so every API route and the docs pages match first; this only ever
sees paths nothing else claimed.
"""

from pathlib import Path

from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Content-hashed build output (Vite's assets/, CRA's static/): the filename
# changes whenever the content does, so these can be cached for a year.
HASHED_DIRS = ("assets", "static")
IMMUTABLE = "public, max-age=31536000, immutable"
# Everything else, index.html above all, is revalidated on every load so a
# newly copied build is picked up immediately.
REVALIDATE = "no-cache"

MISSING_BUILD = (
    "Frontend build not found. Copy your build output into frontend/dist/ "
    "so that index.html sits at its root.\n"
)


class SPAStaticFiles(StaticFiles):
    """Static files with a single-page-app fallback to ``index.html``."""

    def __init__(self, directory: Path, *, api_prefix: str = "/api") -> None:
        # check_dir=False: the build is copied in separately and may not exist
        # yet. The server must still start, and say so on request.
        super().__init__(directory=directory, check_dir=False)
        self.index = directory / "index.html"
        self.api_prefix = "/" + api_prefix.strip("/")
        # Skip Starlette's first-request directory check, which turns a missing
        # build into a 500; a missing build is handled below instead.
        self.config_checked = True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = await self._respond(scope)
        await response(scope, receive, send)

    async def _respond(self, scope: Scope) -> Response:
        request_path: str = scope["path"]

        # An unknown API path is an API error, never the app shell: a client
        # expecting JSON must not receive a 200 full of HTML.
        if request_path == self.api_prefix or request_path.startswith(
            self.api_prefix + "/"
        ):
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        # Never serve dotfiles (.gitkeep, .env ...) out of the build folder.
        if any(part.startswith(".") for part in request_path.split("/") if part):
            return PlainTextResponse("Not Found", status_code=404)

        if not self.index.is_file():
            return PlainTextResponse(MISSING_BUILD, status_code=503)

        path = self.get_path(scope)
        try:
            response = await self.get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404:
                return PlainTextResponse(str(exc.detail), status_code=exc.status_code)
            # A missing hashed asset is a genuine 404. Answering it with
            # index.html would hand the browser HTML where it expects JS or CSS.
            if path.split("/", 1)[0] in HASHED_DIRS:
                return PlainTextResponse("Not Found", status_code=404)
            # Anything else is a client-side route: let React Router handle it.
            response = FileResponse(self.index)
            response.headers["Cache-Control"] = REVALIDATE
            return response

        is_hashed = path.split("/", 1)[0] in HASHED_DIRS
        response.headers["Cache-Control"] = IMMUTABLE if is_hashed else REVALIDATE
        return response


SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
)


class SecurityHeadersMiddleware:
    """Add baseline security headers to every response, API and static alike.

    Pure ASGI rather than BaseHTTPMiddleware, so streamed responses such as the
    .xlsx download pass through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS:
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)
