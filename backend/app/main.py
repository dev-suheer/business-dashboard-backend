from pathlib import Path

import sentry_sdk
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from app.api.main import api_router
from app.core.config import backend_path, settings
from app.spa import SecurityHeadersMiddleware, SPAStaticFiles


def custom_generate_unique_id(route: APIRoute) -> str:
    return f"{route.tags[0]}-{route.name}"


if settings.SENTRY_DSN and settings.FASTAPI_ENV != "development":
    sentry_sdk.init(dsn=str(settings.SENTRY_DSN), enable_tracing=True)


def create_app(frontend_dist: Path | None = None) -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        openapi_url=f"{settings.API_V1_STR}/openapi.json",
        generate_unique_id_function=custom_generate_unique_id,
    )

    # Middleware runs outermost-last: CORS sees the request first, then gzip,
    # then the security headers are stamped on whatever comes back.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        # Only needed when a dev server (e.g. Vite) runs on another origin; the
        # served build shares this origin. Must be an explicit list: browsers
        # reject a "*" origin whenever credentials are included, and the
        # session cookie is a credential.
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.API_V1_STR)

    # The React build, mounted last so the API routes and /docs match first.
    app.mount(
        "/",
        SPAStaticFiles(
            directory=frontend_dist or backend_path(settings.FRONTEND_DIST),
            api_prefix="/api",
        ),
        name="frontend",
    )
    return app


app = create_app()
