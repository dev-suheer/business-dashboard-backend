FROM python:3.12

ENV PYTHONUNBUFFERED=1

# Install uv
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#installing-uv
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

# Compile bytecode
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#compiling-bytecode
ENV UV_COMPILE_BYTECODE=1

# uv Cache
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#caching
ENV UV_LINK_MODE=copy

WORKDIR /app/backend/

# Place executables in the environment at the front of the path
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#using-the-environment
ENV PATH="/app/backend/.venv/bin:$PATH"

# Install dependencies before copying the source, so a code change does not
# invalidate the dependency layer.
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=backend/uv.lock,target=uv.lock \
    --mount=type=bind,source=backend/pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

COPY ./backend/scripts /app/backend/scripts

COPY ./backend/pyproject.toml ./backend/uv.lock /app/backend/

COPY ./backend/app /app/backend/app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Nothing is persisted between requests, so workers need share no state.
# compose.yaml overrides this to add proxy-header trust behind nginx.
CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "6050", "--workers", "4"]
