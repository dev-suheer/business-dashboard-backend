# Development

Backend only. Python 3.12, managed with [uv](https://docs.astral.sh/uv/).

## Setup

```bash
cp .env.example .env
```

Set `SECRET_KEY` to something random:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Then install and create the login account:

```bash
cd backend
uv sync
uv run python app/create_credentials.py
```

`credentials.json` and `.env` are both gitignored. Only the password *hash* is
written to disk.

## Running

```bash
cd backend
uv run fastapi dev app/main.py --port 6050
```

- API: http://localhost:6050
- Interactive docs: http://localhost:6050/docs

The docs page can log in for you: use the **Authorize** button, and the session
cookie is set as it would be from a browser.

## Checks

```bash
cd backend
bash scripts/format.sh    # ruff check --fix, ruff format
bash scripts/lint.sh      # mypy, ty, ruff
bash scripts/test.sh      # pytest under coverage, writes htmlcov/
```

All three must be clean before a change lands. `filterwarnings = ["error"]` is
set deliberately: a `DeprecationWarning` from our own code fails the suite.

To run one file, or one test:

```bash
uv run pytest tests/test_golden.py -q
uv run pytest tests/test_rules.py -k cutoff -q
```

## Tests

`tests/fixtures/` holds the two sample workbooks. `tests/test_golden.py` asserts
the figures in plan.md section 9 end to end — 37 orders, a 15% base of
11,996.91, a net payable of 13,876.31, and so on.

Those are the numbers to trust. They intentionally differ from the legacy
workbook's 4,473.60 / 14,223.61 / 13,876.76 by 0.50, because that workbook used
the promo catalogue price as the commission base on three rows. **Do not tune the
engine to reproduce the workbook.**

## Docker

`compose.yaml` runs a single service. FastAPI serves both the API and the React
build from one origin, the way an Express app pairs its routes with
`express.static(buildPath)`:

```
http://localhost:6050/api/*, /docs, /redoc   the API
http://localhost:6050/*                      the React build in frontend/dist/
```

```bash
(cd backend && uv run python app/create_credentials.py)   # once
cp -r /path/to/react-app/dist/. frontend/dist/            # your build
docker compose up -d --build --wait
docker compose ps                                         # healthy (--wait already waited for it)
docker compose logs -f backend
docker compose down
```

- **Frontend.** `frontend/dist/` is mounted into the container, not built into the
  image, so copying a new build in takes effect on the next page load — no
  rebuild or restart. Until a build is present, `/` answers 503 with a message
  saying so. Build the app with a relative API URL; see
  [frontend/README.md](./frontend/README.md).
- **Routing.** Any path that is not a real file gets `index.html`, so a refresh on
  a React Router URL works. Two exceptions, deliberately: a missing file under
  `/assets/` or `/static/` is a real 404 (never HTML where the browser expects
  JS), and an unknown `/api/...` path is a JSON 404 (never the app shell).
- **Caching.** Hashed files under `/assets/` and `/static/` are cached for a year;
  `index.html` is revalidated on every load, so a new build shows up at once.
- **Long requests.** The app sets no request timeout, and a 200,000-row export
  takes about six minutes. If you later put a reverse proxy or load balancer in
  front, raise its read timeout to around 15 minutes and its body-size limit to
  850 MB, or it will cut large reconciliations off. Once a proxy is in front, also
  add `--forwarded-allow-ips` for its address to the backend command so client IPs
  are reported correctly.
- **Postman.** The collection's default `base_url`, `http://localhost:6050`, works
  against compose as-is.
- **Cookies over http.** Keep `COOKIE_SECURE=false` while serving plain http on
  anything other than `localhost`, and set it to `true` once there is TLS in front.

### Credentials under Docker

Compose never creates the login account. It mounts `backend/credentials.json`
read-only into the container as a secret, so **the file must exist before
`docker compose up`**; if it is missing, compose stops with an error naming the
path. Point `CREDENTIALS_PATH` in `.env` elsewhere to use a different file.

**Create it** — with uv on the host:

```bash
cd backend && uv run python app/create_credentials.py
```

or with Docker only, e.g. on a server without uv:

```bash
touch backend/credentials.json        # compose needs the file to exist to start the container
docker compose run --rm --no-deps --user "$(id -u):$(id -g)" \
  -v "$PWD/backend:/out" -e CREDENTIALS_FILE=/out/credentials.json \
  backend python app/create_credentials.py
```

`--user` makes the file yours rather than root's; it is written with owner-only
permissions either way.

**Change the password or username** by running the same command again while
the stack is up. The change is live immediately in every worker — no restart.
The app re-reads the file whenever it changes on disk.

**Always change it with the command, not by editing the file.** Many editors save
by writing a new file and renaming it over the old one. A single-file mount
stays pinned to the original, so the container would keep seeing the old
account. If that happens, `docker compose restart backend` picks the new file up.

**Existing sessions** survive a password change and run out within the hour. A
change of *username* ends them immediately. To sign everyone out at once, change
`SECRET_KEY` in `.env` and run `docker compose up -d`.

The backend image builds from the repository root. `backend/Dockerfile.dockerignore`
allowlists only what the Dockerfile copies, so `.env`, credentials,
`sample-uploads/` and the frontend build never enter the build context. There is
no database and no job store, so the backend runs several workers.

## Configuration

Everything is in `backend/app/core/config.py`, read from the root `.env`. The
settings worth knowing:

| Setting | Default | Notes |
|---|---|---|
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Drives both the JWT `exp` and the cookie `Max-Age`, so they cannot drift apart |
| `COOKIE_SECURE` | `true` | Set `false` for local http |
| `COOKIE_SAMESITE` | `lax` | Use `none` only for a cross-domain frontend, and then `COOKIE_SECURE` must be `true` |
| `CORS_ORIGINS` | `http://localhost:5173` | Must be explicit; `*` is invalid with credentials |
| `STANDARD_RATE` / `PROMO_RATE` / `PROMO_CUTOFF` | `0.15` / `0.10` / `2026-08-09` | Overridable per request |
| `LINE_TOLERANCE` / `ORDER_TOLERANCE` | `0.01` / `0.05` | Per plan.md sections 3.4 and 3.5 |
| `MAX_UPLOAD_BYTES` | 200 MB | Enforced while streaming, per file |
| `MAX_ROWS_PER_SHEET` | 1,000,000 | Fails fast on absurd input |

Rates are never hardcoded in the calculation. A back-dated recalculation is a
single request with `promo_cutoff` set, not a redeploy.
