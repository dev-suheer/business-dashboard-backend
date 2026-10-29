# Backend

FastAPI service. Python 3.12, uv, no database.

See [../development.md](../development.md) to get it running, and
[../plan.md](../plan.md) for the business rules this implements.

## No database

There is none, by design. Every reconciliation is computed from the workbooks
uploaded in that request and returned in the response. Consequences worth being
explicit about:

- There are no migrations, no models to version, no `alembic`.
- There is no job store. `POST /reconcile` returns the whole result; there is
  nothing to fetch afterwards and nothing to expire. Workers need share no state.
- Prior settlement history cannot be remembered. Clawbacks are resolved from the
  portal export's `Paid` column where it is populated, and otherwise need the
  optional `settlements` upload. Failing both, the engine raises
  `CLAWBACK_UNRESOLVED` rather than silently dropping the amount.

## Layout

```
app/
  main.py                    FastAPI app, CORS
  models.py                  auth and shared response models
  create_credentials.py      CLI: write the single login account
  core/
    config.py                settings: cookies, rates, tolerances, limits
    security.py              JWT, password hashing, cookie set/clear
    credentials.py           load and verify the one account
  api/
    main.py                  router assembly
    deps.py                  cookie authentication
    routes/
      login.py               login / logout / me
      reconcile.py           POST /reconcile           (eshop)
      travel.py              POST /reconcile/travel
      food.py                POST /reconcile/food
      _uploads.py            spooling, size limits, file responses
      utils.py               health check
  recon/
    cleaning.py              text, number and date coercion
    excel.py                 streaming reader, header and sheet detection
    inputs.py                Upload, ReconInputError — the shared vocabulary
    xlsx.py                  workbook formats shared by both reports
    sheets.py                the sheets every category's report shares
    wallet.py                the wallet-history check, for every category
    finance.py               the finance report's cashback lookups, for every category
    models.py                eshop's canonical models — Decimal throughout
    exceptions.py            exception codes and severities, for every category
    matcher.py               aggregate, join, tolerance, classify
    engine.py                the eshop pipeline
    export.py                the eshop .xlsx report
    registry.py              category registry
    categories/
      base.py                the adapter contract
      eshop/                 parser and rules for the eShop category
      travel/                models, parser, rules, wallet, pipeline, export
      food/                  models, parser, rules, pipeline, export
tests/
  fixtures/                  the two eshop sample workbooks
  fixtures/travel/           the portal export, four weekly claims, wallet history
```

## Adding a category

There are two shapes, and picking the wrong one is expensive.

**Same grain as eShop** — an order-line portal export joined to an account
ledger. Supply four sheet schemas, four readers, an accumulator and two rule
functions, wire them into a `CategoryAdapter`, and register it in
`app/recon/registry.py`. Nothing else changes.

**Different grain** — travel is the worked example. It reconciles a *booking*
against a claim's invoice lines, needs no promo catalogue, takes an optional
third file, and reports two blocks eshop has no concept of. Forcing it through
`CategoryAdapter` would have widened fourteen shared structures for one
category, including teaching `OrderStatus` travel's statuses, which eshop's own
parser would then have accepted silently. So travel owns its pipeline, its
result model and its report under `categories/travel/`, and reuses what is
genuinely generic: `excel.py`, `cleaning.py`, `exceptions.py`, `inputs.py`,
`xlsx.py` and the upload plumbing in `api/routes/_uploads.py`. See
[../plan-travel.md](../plan-travel.md) section 4.

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
- **Streaming.** Uploads go to temp files in chunks; sheets are read row by row;
  the portal export becomes order-level totals as it is read; per-line records are
  kept only for lines that breach tolerance.

## Testing

```bash
bash scripts/test.sh
```

`tests/test_golden.py` and `tests/test_travel_golden.py` are the important ones:
they reconcile the sample workbooks and assert every figure in plan.md section 9
and plan-travel.md section 9. If the first fails, the engine
changed behaviour — check that before changing the test.
