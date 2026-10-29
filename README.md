# Excel Reconciliation Service

A backend-only FastAPI service that reconciles our portal sales data against a
vendor's account statement and reports what we owe them.

Uploads go in, a reconciliation comes out. **Nothing is persisted** — no
database, no job store, no files kept on disk. Every run is computed from the
workbooks in that one request.

The business rules live in [plan.md](./plan.md), which is the specification this
engine implements. Read it before changing anything in `backend/app/recon/`.

## What it does

Given three workbooks — the portal export, the vendor's account statement and
the promotional SKU catalogue — one request returns:

- per-order reconciliation: portal GMV against the vendor's invoice, within tolerance
- contractual commission against what was actually charged, classified as clean,
  overcharged, wrong-rate or promotional absorption
- order-status treatment for returns and RTOs, including clawbacks of amounts
  paid in an earlier cycle
- a settlement bridge from computed net payable to the vendor's closing balance
- an exception list, split by severity, with informational prior- and
  later-period items kept out of the way of real breaks
- the same result as a formatted `.xlsx` report with the same tabs in every
  category: Dashboard, Orders, Exceptions, Wallet (when a wallet history is
  uploaded), B2C Invoices ERP, copies of the Portal and Vendor uploads, and
  Finance Report (when a finance report is uploaded)

## API

All under `/api/v1`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/login/access-token` | Log in; sets the session cookie |
| `POST` | `/logout` | Clears the session cookie |
| `GET` | `/login/me` | Confirms the session is still valid |
| `POST` | `/reconcile` | eShop reconciliation. `?format=xlsx` returns the report instead of JSON |
| `POST` | `/reconcile/travel` | Travel reconciliation. `?format=xlsx` returns the report |
| `POST` | `/reconcile/food` | Food reconciliation. `?format=xlsx` returns the report |
| `GET` | `/utils/health-check/` | Liveness |

Interactive docs at `/docs`.

### Authentication

One account, and only one. Its username and password **hash** live in
`backend/credentials.json`, which is not committed.

```bash
cd backend
uv run python app/create_credentials.py
```

Logging in sets a signed JWT in an `HttpOnly`, `SameSite=Lax` cookie that
expires after **one hour**. The token is never returned in a response body, and
`SameSite=Lax` is what stands in for a CSRF token: the browser will not send the
cookie on a cross-site `POST`.

Because the cookie is a credential, `CORS_ORIGINS` must name your frontend's
origin explicitly — browsers reject a `*` origin whenever credentials are
included.

### Reconciling eShop

`POST /api/v1/reconcile` is `multipart/form-data`:

| Field | Required | Meaning |
|---|---|---|
| `portal` | yes | Portal export, one row per order line |
| `vendor` | yes | Vendor account statement |
| `promo` | yes | Discount base sheet (promotional SKUs) |
| `settlements` | no | Prior settlement history, for clawbacks |
| `wallet` | no | Wallet history, to prove the wallet share of each order |
| `finance` | no | Finance (cashback) report; each order's number digits are looked up in its Order ID |
| `category` | no | Defaults to `eshop` |
| `period_start`, `period_end` | no | Default to the portal order-date range |
| `standard_rate`, `promo_rate`, `promo_cutoff` | no | Per-run overrides of the configured rates |
| `format` (query) | no | `json` (default) or `xlsx` |

Each file is searched for the sheet its field needs, so a workbook containing
several tabs can be uploaded to more than one field — the sample data does
exactly this, with the portal export and the discount sheet in one file. The
response includes a `detected` manifest naming the sheet chosen for each role, so
a misdetection is visible rather than silent.

The vendor statement is handled differently: *every* ledger-shaped sheet in it is
read and the union deduplicated, because the PDF converter that produces these
files emits overlapping copies of the same transactions across several sheets.

### Reconciling travel

Travel reconciles a *booking* — hotels (`H…`) and flights (`A…`) — against the
vendor's weekly claim, so it has its own endpoint and its own result shape.
`POST /api/v1/reconcile/travel` is `multipart/form-data`:

| Field | Required | Meaning |
|---|---|---|
| `portal` | yes | Portal export; hotel and flight tabs are both read |
| `vendor` | yes | The vendor's weekly claim workbook |
| `wallet` | no | Wallet history, to prove the wallet share of each booking |
| `finance` | no | Finance (cashback) report; each booking's SG Order ID is looked up in its Order ID |
| `period_start`, `period_end` | no | Default to the portal order-date range |
| `hotel_rate`, `flight_rate`, `vat_rate` | no | Per-run overrides of the configured rates |
| `format` (query) | no | `json` (default) or `xlsx` |

Every travel-shaped sheet in each workbook is read, so hotel and flights can be
two tabs of one file and rows belonging to another category are ignored rather
than rejected.

The commission rule is the point of the exercise: we charge 3% on flights and 5%
on hotels, while the vendor bills that rate **plus 15% VAT whenever the booking
carries no VAT of its own** — an international booking. Local or international is
decided by the VAT on the claim, never by its `DOM/INT` label, which the sample
data proves unreliable. The run settles on our own base at the vendor's contract
rate and reports the uplift we never charged the customer, booking by booking.

See [plan-travel.md](./plan-travel.md) for the full rule set and the report.

### Reconciling food

`POST /api/v1/reconcile/food` pairs a portal export with the Chefz order report
on `Partner Order ID` = `order_id`. Dates are not compared. Only `Delivered`
rows settle. The JSON is the whole result: one object per order with the portal
side, the six checks, the vendor side, a verdict, and the settled due. A sample
response is in [frontend/food-reconcile.sample.json](./frontend/food-reconcile.sample.json).
An optional `wallet` upload checks each wallet-paid order against the wallet
history, as travel does. An optional `finance` upload looks each order's SG Order
ID up in the finance (cashback) report.

See [plan-food.md](./plan-food.md).

## Getting started

### With Docker — frontend and API together

```bash
cp .env.example .env                                   # then set SECRET_KEY
(cd backend && uv run python app/create_credentials.py)
cp -r /path/to/react-app/dist/. frontend/dist/         # your React build
docker compose up -d --build --wait
```

Open http://localhost:6050. One FastAPI process serves the API under `/api/*`
and the React build at everything else — the same pattern as Express with
`express.static(buildPath)` — so the session cookie needs no CORS setup.
Swagger UI is at http://localhost:6050/docs. See
[frontend/README.md](./frontend/README.md) for how to build the app for this, and
[development.md](./development.md#credentials-under-docker) for creating or
changing the login account when running under Docker.

### API only, for development

```bash
cp .env.example .env          # then set SECRET_KEY
cd backend
uv sync
uv run python app/create_credentials.py
uv run fastapi dev app/main.py --port 6050
```

See [development.md](./development.md) for the full loop, and
[backend/README.md](./backend/README.md) for the layout of the engine.

## Large files

The service is built to process large workbooks without holding them in memory:
uploads are streamed to temporary files, sheets are read row by row with
openpyxl's read-only mode, the portal export is reduced to order-level totals as
it streams, and the report is written straight to disk. Peak memory tracks the
number of *orders* and *exceptions*, not the number of rows.

Every monetary value is a `Decimal`. `float` appears in exactly one place — the
boundary where a number is handed to the Excel writer, which cannot accept a
`Decimal`.
