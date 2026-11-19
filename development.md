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

Then install:

```bash
cd backend
uv sync
```

`.env` is gitignored. The login comes with the database: see [The login](#the-login).

## Database

v2 keeps run history and category configuration in Postgres: a `recon`
database (and `recon_test` for the test suite) in the shared `postgres`
container on `:5432`. `.env` holds `DATABASE_URL` and `TEST_DATABASE_URL`.
Under Docker, `compose.yaml` runs its own Postgres instead (see [Docker](#docker)).

```bash
docker start postgres
cd backend
uv run alembic upgrade head          # the app's database
```

The test suite migrates `recon_test` itself, and skips the database tests when
`TEST_DATABASE_URL` is not set. In Docker, the backend runs `alembic upgrade
head` before it starts serving.

## Category configuration

Each category's configuration lives in `category_version` (one row per saved
version; `category.current_version_id` is the one runs use, and every run
records the version it ran under). A run activates it for its duration
(`app/config_model/runtime.py`): the parsers read their sheet schemas from it,
exceptions take their severity from it, the reports take their verdict labels
from it, and the rates and tolerances come from its parameters.

The report workbook is written by `app/render/` from the configuration's
`output`: its tabs in order, each table's columns and its sections (row sets,
the totals row, fixed blocks of labels, figures and formulas that name columns
by key), each dashboard's blocks of figures and its breakdown table. The
breakdown is designed column by column: each column counts its members, or adds
up a formula on them, where its own condition holds, and is written as a whole
number or money; the rows are ranked by one of them. A column header has its
band's colour, or one of its own (`color`, `#RRGGBB`), with white or black text
on it, whichever reads. Tabs can be added, removed, renamed and moved: the
invoices read the table that lists their set and the upload's copy by its
title, wherever they are, and a table's look-up columns are written only when
the workbook has the copy they look in.

Every category runs on the generic runner (`app/runner/`), driven entirely by
the configuration's `model`: it reads each upload's rows, works out their
further fields (which may look into the other uploads through lookups), builds
the orders, raises the row rules' exceptions, computes each order's fields,
takes the first status rule that holds (or runs the checks), raises its
findings, then works out the figures and the History entry. Every field, rule, check,
figure and report column is a formula in the Excel-like language of
`app/expr/` (`portal.total_amount - vendor.total`,
`IF(ABS(fee_diff) > param.tolerance, ...)`, `SUMX("settled", N(due))`, or with
Excel's criteria `SUMIF("settled", status, "Delivered", due)`). Food
(`app/config_model/seed_food.py`) pairs two sides; Travel (`seed_travel.py`)
gathers a booking's claim lines, its portal row and its repeats, and its
adjustments; eShop (`seed_eshop.py`) gathers an order's lines and the statement
rows that name it, resolving a credit note's truncated reference by its prefix
and amount.

Version 1 of each category is written out in those files:

```bash
cd backend
uv run python -m scripts.build_seed   # writes app/seed/v1/*.json
```

The migrations load those files into version 1, and `tests/test_config.py`
fails if they and the code ever disagree.

A setup is checked before it is saved (`app/config_model/check.py`), and the
editor shows the same check as you type. Besides parsing every formula, it
works out what each formula can see when it runs (`app/config_model/scopes.py`,
which mirrors the runner's scopes): a row its columns and fields, an order its
sides or rows and the fields before it, the run its parameters, context and the
figures before it, an aggregate the members of the set it names. A name that is
not there, or a value of a kind its operator or function cannot take, is a
problem, and the setup is not saved. Whatever it cannot be sure of is let
through for a real run to find, so it never blocks a setup that works;
`tests/test_check.py` holds the approved setups to no problems at all. A part
of the setup that is itself broken (a set made from itself, a group of a
collection the order does not have) is reported once, where it is, and the
formulas that read through it are not reported again.

**Criteria** in `SUMIF`, `SUMIFS`, `COUNTIF` and `COUNTIFS` are Excel's: a value
means equal (text whatever its case), and `=`, `<>`, `>`, `>=`, `<`, `<=` in
front compare. A number may be written `5`, `-0.5`, `.5`, `1,000.25`, `1E3` or
`50%`; a date only as YYYY-MM-DD. `*` and `?` are wildcards (`~` before one for
the character itself) and match text only, never a number or a date. `""` and a
blank value match blanks, `"<>"` anything not blank. A criterion that compares
as a number or a date but is neither (`">=01/08/2026"`) is a problem when it is
written out, and fails the run otherwise: it would quietly match nothing.

**Nothing in a setup is hidden from the editor.** The canvas shows the uploads,
each order's fields, the figures and the report; everything else has a dialog:
*Rules and exceptions* (upload-row rules, run values, stops, status rules,
checks, order exceptions, once-per-run rules, named lists, the exception list),
*How orders are made* (pairing or gathering, groups, lookups, fields worked out
after every upload is read, the order of the orders; a new blank category starts
there), each upload's *Settings* (how its sheets are recognised, which rows are
read, repeated and refused rows, counting, and the wallet check), *Rates and
tolerances*, *Workbook settings* (the History line and file name, texts, the
Exceptions sheet's status, labels) and a table's *Sheet layout* (its parts,
totals and fixed rows) or the ERP sheet's invoice lines. Each rule, stop, list
and exception can say in plain words what it does (`about`); version 1 has
those words for the approved rules (`app/config_model/seed_words.py`), and no
run reads them.

A category can be built in the editor from blank to exactly what eShop, Travel or
Food has. An upload holds rows to reconcile; or is the standard *wallet history*,
whose sheet and column names are fixed because the wallet check reads them by name
(`GET /standard`, built from `wallet_upload()` in `seed.py`), and the setup check
refuses one that has lost its name, its sheet's key or a column the check needs;
or is a *reference file*, a file to look things up in: read and copied into the
report, but no part of the reconciliation, so a run does not list its sheets. The
finance report is one. The editor has nothing ready-made of any one such file: it
is added as a file to look things up in and typed by hand (the approved setups'
finance report is built by `finance_upload()` in `seed.py`, for version 1 only).

A table's *look-up columns* (`lookup_columns`, `app/render/lookups.py`) come after
its own. Each finds its value in a copy of an upload (`lookup.sheet`): the row
whose `match` column holds this row's key (`key_column`, a column of the table,
or `key_value`, a formula), and that row's `value` column. Each cell is an Excel
lookup, so it shows where its value comes from; no formula reads it, so it never
moves a figure; and it is written only when its copy is in the report, on the
parts that tick *Fills the look-up columns*. They are added one at a time
(*+ Look-up column*), a new one looking where the one before it looks; the
approved setups' five finance ones are built by `finance_lookups()` in
`seed_output.py`, for version 1 only. A new file needs no code: add it as a file
to look things up in, copy it into the report (*Copy of an upload*), and add
look-up columns. The setup check refuses a look-up into no copy, of a column the copied
upload does not have, with no key or two, or a copy leaving out a column a
look-up reads. An upload can have several sheets, each with its own key; a new
tab can be given its key; and the report can have an *ERP invoices* tab. `uv run python -m scripts.compare_category travel` lists, place
by place, how a category's current setup differs from its version 1 seed (or, with
a second key, from another category); it only reads the database.

**Archiving and deleting a category.** On the Categories screen a category can be
*archived*: it leaves New reconciliation and the list, and no run can start on it
or copy it, but its versions and runs (with their reports) are all
kept and History still shows them; *Restore* brings it back. Only an archived
category can be *deleted for good*, confirmed by typing its key
(`DELETE /categories/{key}?confirm={key}`): that removes its versions and
every run of it, with their uploads and reports, from the
database and the file store, and frees its key. It cannot be undone.

**Renaming** in the editor renames everywhere (`app/config_model/rename.py`). The
check's scopes know where each name comes from, so a rename rewrites exactly the
names in formulas that read the renamed field, figure or column, wherever they
are in the setup, and leaves alone another thing of the same name (a row's own
column, say). An upload's column is renamed by its field, on every sheet of its
row set, and the look-up columns that read it follow; the wallet history's
columns cannot be. A report column is renamed with the totals, blocks and
look-ups that name its key, and a column with no formula keeps reading what it read. A rename that
would make a formula read something else is refused.

## Parity baseline

v2 must not change a single approved figure. `tests/baseline/` holds the result
and the report of every scenario in `tests/parity/scenarios.py`, captured from
the v1 code; `tests/test_parity.py` re-runs each scenario and compares the
result field by field and the workbook cell by cell (values, formulas, formats,
merges, widths). A category on the generic runner stores its result in another
shape, so for it both sides are compared as a projection: the summary figures,
the vendor, each order's verdict, due and exception codes, and every exception,
wallet check and detected sheet. Never re-capture the baseline to make a failing
parity test pass: the difference is the bug.

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

`compose.yaml` runs two services: `db`, Postgres 18, and `backend`. FastAPI
serves both the API and the React build from one origin, the way an Express app
pairs its routes with `express.static(buildPath)`:

```
http://localhost:6050/api/*, /docs, /redoc   the API
http://localhost:6050/*                      the React build in frontend/dist/
```

```bash
cp -r /path/to/react-app/dist/. frontend/dist/            # your build
docker compose up -d --build --wait
docker compose ps                                         # healthy (--wait already waited for it)
docker compose logs -f backend
docker compose down
```

- **Settings.** Both services read the one settings file `compose.yaml` names
  (`x-env-file`): `PROJECT_NAME`, `SECRET_KEY`, `COOKIE_SECURE`,
  `POSTGRES_PASSWORD`, and `DATABASE_URL` pointing at the `db` service with that
  same password (`postgresql+psycopg://recon:<password>@db:5432/recon`). Keep the
  password to letters, digits, `-` and `_`, since it sits inside a URL.
- **Database.** `db` creates the `recon` database and user on its first start,
  with `POSTGRES_PASSWORD`; changing that setting later does not change the
  password of a database that already exists. Its data lives on the `recon-db`
  volume, and it publishes no port: only the backend reaches it. The backend
  waits until it answers, then migrates. `docker compose exec db psql -U recon recon`
  opens a prompt on it.
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
- **Long requests.** The app sets no request timeout, and a run on files near the
  upload limit takes a long time; see [Large files](#large-files). If you later
  put a reverse proxy or load balancer in front, raise its read timeout and its
  body-size limit to match, or it will cut large runs off. Once a proxy is in
  front, also add `--forwarded-allow-ips` for its address to the backend command
  so client IPs are reported correctly.
- **Postman.** The collection's default `base_url`, `http://localhost:6050`, works
  against compose as-is. Set Postman's working directory to `backend/tests/fixtures`
  so its file fields find the sample workbooks. Running it adds runs to History.
- **Cookies over http.** Keep `COOKIE_SECURE=false` while serving plain http on
  anything other than `localhost`, and set it to `true` once there is TLS in front.

### The login

The logins are rows of the `account` table: a username and the hash of its
password (argon2), never the password. The migrations seed them from
`backend/app/seed/users.json`, which holds the one account for now, so a new
database has it after its first `alembic upgrade head` (which the container runs
before serving). An account already in the table is left as it is.

There is no command to add an account or change a password for now. A session
is a signed cookie naming the account: one for a username no account has any
longer stops working at once. To sign everyone out at once, change `SECRET_KEY`
in the settings file and run `docker compose up -d`.

The backend image builds from the repository root. `backend/Dockerfile.dockerignore`
allowlists only what the Dockerfile copies, so `.env`, `sample-uploads/` and
the frontend build never enter the build context. Workers
share nothing but the database and the file store, so the backend runs several
(`BACKEND_WORKERS`, 4 by default). Each one running a large file holds its rows
in memory; see [Large files](#large-files) before raising it.

## Large files

`scripts/time_large_upload.py` times one run on a generated Food portal export of
the size asked for (20 MB by default) and a vendor report with the same orders,
through the same service as `POST /runs`, on the test database. It removes the
run afterwards.

```bash
cd backend
uv run python scripts/time_large_upload.py             # 20 MB: about five minutes
uv run python scripts/time_large_upload.py --mb 195    # at the 200 MB limit: over an hour
```

Measured on an 8-core machine:

| Portal export | Orders | Run | Peak memory |
|---|---|---|---|
| 1 MB | 4,832 | 13 s | 0.2 GB |
| 5 MB | 23,858 | 64 s | 0.4 GB |
| 20 MB | 95,734 | 4 min 19 s | 1.5 GB |

One run at the limit (a 196 MB export of 934,388 orders, and a 140 MB vendor
report) reconciled in about 65 minutes and peaked at 9.7 GB, then failed writing
the report, which needed ZIP64 (below); it has not been repeated since the fix.

Time grows a little faster than the number of orders. About a quarter of a run
is reading the uploads, a twentieth is reconciling, and the rest is writing the
report, half of that copying the two uploads into its Portal and Vendor tabs.
Memory is mostly the rows read from the uploads, which a run holds until it
ends.

What that means in practice:

- **Workers.** A run at the limit holds about 10 GB. With `BACKEND_WORKERS=4`,
  four of them at once need about 40 GB.
- **The request.** `POST /runs` answers when the run ends, so the browser waits
  on it, for over an hour at the limit. The session is checked when the run
  starts, so a run outlives a session that expires meanwhile and is kept in
  History, but the app asks for a new login when it ends. A proxy in front needs
  a read timeout longer than the longest run.
- **The report** of a run at the limit has sheets of more than 4 GB before
  compression; the workbook is written with ZIP64 so it can hold them, which
  Excel reads.

## Backups

Two things make up the system's state, and they are backed up together: the
`recon` database (every category's versions and the run history) and the file
store (every run's uploads and report). Back up the
database first and then the files, so every run in the backup has its files.

```bash
mkdir -p backups
# The database, in Postgres's own format: the compose db service (under Docker) ...
docker compose exec -T db pg_dump -U recon -Fc recon > backups/recon-$(date +%F).dump
# ... or the shared postgres container, with its admin account, without Docker.
docker exec postgres sh -c 'PGPASSWORD="$POSTGRESQL_PASSWORD" pg_dump -U "$POSTGRESQL_USERNAME" -Fc recon' \
  > backups/recon-$(date +%F).dump
# The file store: the compose volume (under Docker) ...
docker compose run --rm --no-deps -v "$PWD/backups:/backup" backend \
  tar czf /backup/recon-data-$(date +%F).tgz -C /data .
# ... or RUN_STORAGE_DIR when running without Docker (../data from backend/).
tar czf backups/recon-data-$(date +%F).tgz -C data .
```

To restore, stop the backend (`docker compose stop backend` under Docker), then:

```bash
# Under Docker:
docker compose exec -T db pg_restore -U recon --clean --if-exists --no-owner -d recon \
  < backups/recon-2026-11-04.dump
# Without Docker:
docker exec -i postgres sh -c 'PGPASSWORD="$POSTGRESQL_PASSWORD" pg_restore -U "$POSTGRESQL_USERNAME" --clean --if-exists --no-owner --role=recon -d recon' \
  < backups/recon-2026-11-04.dump
docker compose run --rm --no-deps -v "$PWD/backups:/backup" backend \
  tar xzf /backup/recon-data-2026-11-04.tgz -C /data
```

`pg_restore --list backups/recon-….dump` shows what a dump holds without
restoring it. Keep the backups off this machine, and with the same care as the
data: they hold every upload.

## Retention

Every run is kept until the client decides otherwise. `RUN_RETENTION_DAYS` in
`.env` sets how long a run is kept; unset (the default), every run is kept. Even
when it is set nothing is removed on its own: only this command removes runs,
and only with `--apply`.

```bash
cd backend
uv run python app/prune_runs.py               # lists the runs older than the limit, and their size
uv run python app/prune_runs.py --apply       # removes them
uv run python app/prune_runs.py --days 365    # another limit, for this once
# under Docker
docker compose exec backend python app/prune_runs.py
```

A run is removed whole: its History row, its uploads, its report and its result.
Run numbers are never reused. Category setups and their versions are never
removed.

## Configuration

Everything is in `backend/app/core/config.py`, read from the root `.env`. The
settings worth knowing:

| Setting | Default | Notes |
|---|---|---|
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `360` | Drives both the JWT `exp` and the cookie `Max-Age`, so they cannot drift apart |
| `COOKIE_SECURE` | `true` | Set `false` for local http |
| `COOKIE_SAMESITE` | `lax` | Use `none` only for a cross-domain frontend, and then `COOKIE_SECURE` must be `true` |
| `CORS_ORIGINS` | `http://localhost:5173` | Must be explicit; `*` is invalid with credentials |
| `STANDARD_RATE` / `PROMO_RATE` / `PROMO_CUTOFF` | `0.15` / `0.10` / `2026-08-09` | Overridable per request |
| `LINE_TOLERANCE` / `ORDER_TOLERANCE` | `0.01` / `0.05` | Per plan.md sections 3.4 and 3.5 |
| `MAX_UPLOAD_BYTES` | 200 MB | Enforced while streaming, per file |
| `MAX_ROWS_PER_SHEET` | 1,000,000 | Fails fast on absurd input |
| `DATABASE_URL` / `TEST_DATABASE_URL` | — | The app's database and the test suite's |
| `RUN_STORAGE_DIR` | `../data` | The file store; `/data` (a volume) under Docker |
| `RUN_RETENTION_DAYS` | unset | Unset keeps every run; see [Retention](#retention) |

The rates and tolerances in this table are only where version 1 of each category
took its values from. A run uses its category's setup, where each one can be
changed and saved as a new version, and each can be overridden for one run: a
back-dated recalculation is a single run with `promo_cutoff` set, not a redeploy.
