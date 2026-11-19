# Backend

FastAPI service. Python 3.12, uv, Postgres.

See [../development.md](../development.md) to get it running, and
[../plan.md](../plan.md) for the business rules this implements.

## What is kept where

- **Postgres** holds each category, every saved version of its setup (JSONB),
  and the run history. Alembic migrates it; version 1
  of every category arrives in a migration.
- **The file store** (`RUN_STORAGE_DIR`) holds each run's uploads as they
  arrived, its report and its full result.
  A workbook can be 200 MB, which does not belong in a table; the database
  records paths relative to the store, so the store can move.
- Prior settlement history is not looked up from earlier runs. Clawbacks are
  resolved from the portal export's `Paid` column where it is populated, and
  otherwise need the optional `settlements` upload. Failing both, the run raises
  `CLAWBACK_UNRESOLVED` rather than silently dropping the amount.

## Layout

```
app/
  main.py                    FastAPI app, CORS
  models.py                  auth and shared response models
  prune_runs.py              CLI: remove runs older than RUN_RETENTION_DAYS
  core/
    config.py                settings: cookies, rates, tolerances, limits
    security.py              JWT, password hashing, cookie set/clear
    credentials.py           the logins: find and verify an account (seeded from seed/users.json)
  api/
    main.py                  router assembly
    deps.py                  cookie authentication
    routes/
      login.py               login / logout / me
      runs.py                POST/GET /runs: run, keep, list, download
      categories.py          /categories: setups, versions, check, trace, file sheets
      _uploads.py            spooling and the size limit
      utils.py               health check
  config_model/              a category's configuration, and version 1 of each
    seed_eshop.py, seed_travel.py, seed_food.py   the approved rules, as formulas
    check.py, scopes.py      the check before a save: every name and kind a formula uses
    rename.py                renaming a field, figure or column with every use of it
  expr/                      the formula language: parse, compile, evaluate
  runner/                    the generic runner: read, build the orders, decide, sum
  render/                    the report workbook, from the output configuration
  services/                  runs, configurations, file sheets, trace, storage, retention
  recon/
    cleaning.py              text, number and date coercion
    excel.py                 streaming reader, header and sheet detection
    inputs.py                Upload, ReconInputError — the shared vocabulary
    xlsx.py                  workbook formats shared by every report
    sheets.py                the sheets every category's report shares
    wallet.py                the wallet-history check, for every category
    exceptions.py            exception codes and severities, for every category
    categories/eshop/template.py   the vendor statement template
tests/
  fixtures/                  the two eshop sample workbooks
  fixtures/travel/           the portal export, four weekly claims, wallet history
  fixtures/food/             the portal export and the vendor report
  baseline/                  every scenario's approved result and report (parity)
scripts/
  time_large_upload.py       time a run on a portal export at the upload limit
```

## Adding a category

A category is a configuration, not code: its uploads and how each is read, its
parameters, how its rows make orders (paired from two sides, or gathered from
any number), each order's fields, its status rules and checks, the exceptions
it raises, its figures and its report — every one a formula the generic runner
(`app/runner/`) carries out. Version 1 of eShop, Travel and Food
(`app/config_model/seed_*.py`) are the worked examples, each the approved v1
pipeline written out rule for rule. See [../development.md](../development.md).

Two traps a new category will meet, both of which cost time to find:

- **The portal header is not unique.** Food and travel share one export format
  and differ only by the `Vertical` cell. Sheet detection keys on header *text*
  only, so the discriminator has to be a `SheetSchema.exclude` — and the
  client's own worked reconciliations carry both sides' columns bolted
  together, so they satisfy every fingerprint. `tests/test_travel_schemas.py`
  pins all of it.
- **`SG Order ID` is not an identity.** It is a per-export row sequence: the
  same booking is 251 in one export and 364 in another. Join on the booking id;
  the wallet history is the one place the sequence has to be used, and the
  wallet check (`app/recon/wallet.py`) reports a collapse in the match rate
  rather than a page of missing rows.

## Conventions

- **`Decimal`, never `float`.** The only `float` in the codebase is at the
  boundary where a value is handed to `xlsxwriter`, which cannot take a
  `Decimal`. `cleaning.money()` is the single rounding point: 2dp, half up.
- **Rounding happens once, when a figure is reported.** Quantizing per line
  biases the cycle total upward — 31 of the sample's 75 lines land on an exact
  half cent, worth 0.12 — so running totals stay unrounded.
- **Columns are located by header text, never by position.** The vendor's
  statement shifts every column by one between pages.
- **Strip Unicode `Cf` before comparing any text.** The converter wraps fields in
  invisible bidirectional controls that break regexes and equality tests.
- **Streaming.** Uploads go to temp files in chunks and sheets are read row by
  row. The rows a run reads are then held in memory while it reconciles: only
  the columns the setup names, but every row.

## Testing

```bash
bash scripts/test.sh
```

`tests/test_golden.py` and `tests/test_travel_golden.py` are the important ones:
they reconcile the sample workbooks and assert every figure in plan.md section 9
and plan-travel.md section 9. If the first fails, the engine
changed behaviour — check that before changing the test.
