# Excel Reconciliation Service

A FastAPI service, and the React app it serves, that reconciles our portal sales
data against each vendor's files and reports what we owe them.

Each **category** (eShop, Travel, Food, and any added later) is a setup kept in
Postgres: the files it takes and how each is read, its rates, how rows become
orders, each order's fields, its status rules and exceptions, and the layout of
its Excel report. Every save is a new **version**, and the next run uses it.
Every **run** is kept in History with the files it was given, its report, its
key figures and the version it ran under.

Version 1 of eShop, Travel and Food is the client-approved rules, written out
exactly. The business rules behind them are in [plan.md](./plan.md),
[plan-travel.md](./plan-travel.md) and [plan-food.md](./plan-food.md).

## What it does

A run takes one workbook per upload its category takes and produces:

- per-order reconciliation of the portal against the vendor, within tolerance
- contractual commission against what was actually charged
- order-status treatment for returns, cancellations and RTOs
- an exception list, split by severity, with informational prior- and
  later-period items kept out of the way of real breaks
- a formatted `.xlsx` report with the same tabs in every category: Dashboard,
  Orders, Exceptions, Wallet (when a wallet history is uploaded), B2C Invoices
  ERP, copies of the Portal and Vendor uploads, and Finance Report (when a
  finance report is uploaded)

In the app, **New reconciliation** starts a run, **History** finds and downloads
past ones, and **Configuration** lists the categories and opens each one's
setup on a canvas: every upload, the orders, the figures and the report, with
Excel-like formulas, the lines between a column and what it reads, and the
status rules in a pop-up.

## API

All under `/api/v1`. Interactive docs are at `/docs`, and
[postman/](./postman/) has a collection of every call in run order.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/login/access-token` | Log in; sets the session cookie |
| `POST` | `/logout` | Clears the session cookie |
| `GET` | `/login/me` | Confirms the session is still valid |
| `GET` | `/utils/health-check/` | Liveness |
| `POST` | `/runs` | Start a run; it is kept in History |
| `GET` | `/runs` | History: filter by category, status, period, run date; search by run number or file name |
| `GET` | `/runs/{number}` | One run: files, key figures, exception counts, version |
| `GET` | `/runs/{number}/report` | Its Excel report |
| `GET` | `/runs/{number}/files/{role}` | A file it was given, byte for byte |
| `GET` | `/categories` | Every category, the files it takes and its current version |
| `POST` | `/categories` | A new category, blank or a copy of another |
| `GET` / `PUT` | `/categories/{key}/config` | The setup runs use; saving makes a new version |
| `POST` | `/categories/{key}/check` | The problems in a setup, and what each formula reads |
| `POST` | `/categories/{key}/rename` | Rename a field, a figure or a column, with every formula, total and block that uses it; nothing is saved |
| `GET` | `/categories/{key}/versions[/{n}]` | Saved versions |
| `POST` | `/categories/{key}/sheets/{role}` | Read a file for its sheets and headers, to start an upload's columns; keeps nothing |
| `GET` | `/functions` | The functions a formula can call |

### Authentication

The logins are kept in the database (the `account` table), each a username and
its password's **hash**, never the password. They are seeded by the migrations
from `backend/app/seed/users.json`, which holds the one account for now: a new
database gets it on its first `alembic upgrade head`, as it gets version 1 of
each category.

Logging in sets a signed JWT in an `HttpOnly`, `SameSite=Lax` cookie that
expires after **one hour**. The token is never returned in a response body, and
`SameSite=Lax` is what stands in for a CSRF token: the browser will not send the
cookie on a cross-site `POST`.

Because the cookie is a credential, `CORS_ORIGINS` must name your frontend's
origin explicitly — browsers reject a `*` origin whenever credentials are
included.

### Starting a run

`POST /api/v1/runs` is `multipart/form-data`: `category`, one workbook per file
the category takes (field name = its role), and optionally the category's rate
overrides. The run's period is always worked out from the files.

| Category | Required files | Optional files | Rate overrides |
|---|---|---|---|
| `eshop` | `portal`, `vendor`, `promo` | `settlements`, `wallet`, `finance` | `standard_rate`, `promo_rate`, `promo_cutoff` |
| `travel` | `portal`, `vendor` | `wallet`, `finance` | `hotel_rate`, `flight_rate`, `vat_rate` |
| `food` | `portal`, `vendor` | `wallet`, `finance` | — |

`GET /categories` gives the same list for any category, including new ones.

- A file that does not fit its category (the wrong file, a damaged one, a sheet
  or column missing) makes a **failed run**: it is returned with `201`, kept in
  History with its reason and its files, and has no report.
- A request that cannot start a run at all (an unknown category, a missing or
  unexpected file, a file that is not a workbook, one over the size limit) is a
  `4xx` and leaves no trace.

Each file is searched for the sheet its role needs, so a workbook holding several
tabs can be uploaded to more than one field — the eShop sample data does exactly
this, with the portal export and the discount sheet in one file. Each run's files
record the sheets read and how many rows, so a misdetection is visible rather
than silent.

### Changing a setup

1. `GET /categories/{key}/config` gives the setup and its `version`.
2. Edit it. `POST /categories/{key}/check` returns its problems and what each
   formula reads. A problem is anything that would fail on any files: a formula
   that does not parse, a name that is not there when the formula runs (a typo,
   or a field worked out after it), a set or lookup that does not exist, a value
   of the wrong kind (text multiplied, a date compared with a number), an
   exception with no severity.
3. `PUT /categories/{key}/config` with `config`, a `note` and `base_version`,
   the version the edit started from. It is saved as the next version, which the
   next run uses. A setup with problems is refused with `422`; if someone saved
   since `base_version`, it is refused with `409` rather than overwrite theirs.

Earlier versions are kept, and any of them can be opened and saved again.

Renaming goes through `POST /categories/{key}/rename` with `config`, `path` (what
is renamed: `model.fields[3]`, `model.metrics[2]`,
`inputs[0].sheets[1].fields[2]`, `model.row_fields[0].fields[1]` or
`output.tabs[1].columns[5]`) and `name`. It returns the renamed setup and every
part of it that changed, including formulas the editor does not show. A name
that is taken there, or a rename that would change what a formula reads, is
refused with `422` and where.

## Getting started

Under Docker, `compose.yaml` runs Postgres itself (the `db` service). Without
Docker, Postgres runs outside this project: a `recon` database (and `recon_test`
for the tests) in the shared `postgres` container on `:5432`. See
[development.md](./development.md#database).

### With Docker — frontend and API together

```bash
# First, the settings file compose.yaml names (x-env-file): PROJECT_NAME,
# SECRET_KEY, POSTGRES_PASSWORD, and DATABASE_URL pointing at the db service.
cp -r /path/to/react-app/dist/. frontend/dist/         # your React build
docker compose up -d --build --wait
```

Open http://localhost:6050. One FastAPI process serves the API under `/api/*`
and the React build at everything else — the same pattern as Express with
`express.static(buildPath)` — so the session cookie needs no CORS setup. The
backend migrates the database before it starts serving. See
[frontend/README.md](./frontend/README.md) for how to build the app for this. The
login comes with the database (see [Authentication](#authentication)).

### API only, for development

```bash
cp .env.example .env          # then set SECRET_KEY and the database URLs
cd backend
uv sync
uv run alembic upgrade head
uv run fastapi dev app/main.py --port 6050
```

See [development.md](./development.md) for the full loop, backups and
retention, and [backend/README.md](./backend/README.md) for the layout of the
engine.

## Large files

Uploads are streamed to disk in chunks and refused the moment one passes the
limit (200 MB per file). Sheets are read row by row with openpyxl's read-only
mode, and the report is written straight to disk. The rows a run reads are held
in memory while it reconciles, so memory grows with the size of the files.

Measured on a Food portal export at the limit: see
[development.md](./development.md#large-files) for the timing and what to set in
front of the app.

Every monetary value is a `Decimal`. `float` appears in exactly one place — the
boundary where a number is handed to the Excel writer, which cannot accept a
`Decimal`.
