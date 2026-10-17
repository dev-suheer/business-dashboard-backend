# eShop Reconciliation — Implementation Plan

**Scope:** eShop category only (1 of 11). FastAPI backend, Excel uploads, dashboard + Excel export.
**Constraints:** Excel files only. No database. All processing at runtime, in memory.

---

## 1. Business model

We act as a broker between customers and vendors. The customer pays us the full order value
(GMV). We retain a commission and pay the vendor the remainder. Shipping is borne by the vendor
but is **settled outside this reconciliation** and must not appear in any payable calculation.

Reconciliation proves that our portal data and the vendor's accounting records agree on what was
sold, what was returned, and what we owe.

---

## 2. Inputs

Three Excel files per run. All uploaded from the frontend; nothing is read from disk or a database.

### 2.1 Portal export (master data) — line level

One row per order line. Several lines can share one order number.

| Column | Use |
|---|---|
| `date` | **Order date.** Drives the period boundary and the promo cutoff. |
| `time` | display only |
| `Merchant Id` | vendor identity |
| `Merchant Name` | display |
| `Order Number` | join key (e.g. `SG-SH-05488`) |
| `SG SKU` | promo-list lookup key |
| `Merchant SKU` | display / secondary identity |
| `Brand`, `Item Title` | display |
| `Store Type`, `Category`, `Sub Category` | display |
| `GMV` | **base amount** for commission and payable |
| `Net GMV` | diagnostic only — 0 on RTO rows |
| `ASP`, `Units` | display; `Units` used for diagnostics only |
| `Coupon Name`, `Coupon Value` | corroborates promotional absorption |
| `SG Commission` | **commission actually charged** — the figure under test |
| `Merchant Commission` | diagnostic (equals `GMV − SG Commission`) |
| `Shipping Ownership`, `Shipping Channel`, `Shipping Cost` | display only — **never deducted** |
| `Payment Type`, `Wallet Amount`, `Non-Wallet Amount` | display only |
| `Order Status` | drives inclusion and adjustments |

**Parsing traps**

- `Non-Wallet Amount` repeats the **order-level** total on every line of that order (3949 appears
  on all six lines of `SG-SH-05488`). Never sum it.
- `Shipping Cost`, `Shipping Ownership`, `Shipping Channel` and `Coupon Name` use the string `"-"`
  or `"--"` instead of null. Coerce to null before numeric casting.
- `date` may arrive as a real datetime or as text. Normalize to `date`.

### 2.2 Vendor statement (account ledger) — Excel

This is **not** a sales report. It is a customer account statement from the vendor's accounting
system, addressed to us. Invoices are raised **per order at full GMV**, with no line detail and
no commission.

Locate columns **by Arabic header text, never by position** — in the sample file, page 1 puts the
balance in column A and page 2 shifts every column by one.

| Header | Meaning | Field |
|---|---|---|
| `الرصيد الحالي` | running balance | `balance` |
| `دائن` | credit | `credit` |
| `مدين` | debit | `debit` |
| `رقم المرجع` | reference — **truncated to 10 chars, do not use as a key** | `raw_ref` |
| `البيان` | description — contains the full order number | `raw_desc` |
| `رقم المستند` | document number | `doc_no` |
| `نوع المستند` | document type | `doc_type` |
| `التاريخ` | document date (`YYYY/MM/DD`) | `doc_date` |

Document types:

| Arabic | Meaning | Effect |
|---|---|---|
| `فاتورة بيع أجل` | credit sales invoice | debit — one per order, at full GMV |
| `فاتورة مردود بيع أجل` | sales return invoice | credit — reverses a full order |
| `قيد يومية` | journal entry | credit — our commission posting, or our payment |

**Parsing traps**

- The converter emits the same transactions across multiple sheets. The sample file has
  `Table002 (Page 1)`, `Table004 (Page 2)` (a subset), `Sheet1` and `Sheet1 (2)`. Parse every
  ledger-shaped sheet, union the rows, then **deduplicate on
  `(doc_no, doc_type, doc_date, debit, credit)`**. Skipping this double-counts ~28 transactions.
- Text fields are wrapped in invisible bidirectional control characters (U+202A, U+202C, U+200E,
  U+200F). Strip every character of Unicode category `Cf` before any regex or comparison.
- Amounts are strings with thousands separators: `"1,885.00"`.
- The statement's invoice date **lags the portal order date** by one to several days, so the
  statement always straddles the period boundary. Period is defined by portal order date only.

### 2.3 Discount base sheet (promo SKU catalogue)

| Column | Use |
|---|---|
| `رمز المنتج` | **SG SKU** — the only load-bearing column |
| `سعر العرض` | offer price — reference only, **not** used as the commission base |
| `رمز البائع` | merchant SKU — reference |
| everything else | ignore |

Sample file holds 100 unique SKUs, no duplicates.

### 2.4 Prior settlement history (optional upload)

Required for clawbacks. Because nothing is persisted, the caller must supply the history of what
was already paid per order in earlier cycles, or clawbacks cannot be computed.

Minimal schema: `order_no`, `amount_paid`, `settled_on`, `cycle_ref`.

If the file is absent, the engine still detects the condition (an RTO or Return whose invoice date
precedes the period) and raises `CLAWBACK_UNRESOLVED` for manual handling, rather than silently
omitting it.

---

## 3. Business rules

### 3.1 Rates and promo window

Configuration, **never hardcoded in the calculation**:

```python
STANDARD_RATE = 0.15
PROMO_RATE    = 0.10
PROMO_CUTOFF  = date(2026, 8, 9)   # inclusive, evaluated against ORDER DATE
```

```
rate = PROMO_RATE  if sku in promo_skus and order_date <= PROMO_CUTOFF
       else STANDARD_RATE
```

The cutoff is **9 August inclusive**, confirmed against the data: it reproduces the client's own
15% base of 11,996.91 exactly. An 8 August cutoff does not. Note this contradicts the
`"To be charged till 8th Aug"` label on the discount sheet — the evidence wins.

The existing workbook's formula `IF(F2 = AI2, 10%, 15%)` tests SKU membership only and applies no
date rule at all, which is why its RECON tab still awards 10% to orders placed on 10 August. Dated
rate resolution is the single most important correctness fix in this build.

### 3.2 Per-line calculation

```
base     = gmv                                   # always portal GMV
expected = round_half_up(base * rate, 2)
payable  = base - expected                       # contractual commission, always
variance = actual_commission - expected          # actual = portal "SG Commission"
```

Never use the catalogue price as the base. The legacy workbook did so on three rows and overstates
its payable by 0.45 as a result.

### 3.3 Order status treatment

The complete status list is: **Delivered, Ready to Pickup, Return, RTO**.

| Status | Payable | Notes |
|---|---|---|
| `Delivered` | `base − expected` | normal case |
| `Ready to Pickup` | `base − expected` | flag `PENDING_DELIVERY` — revenue not yet delivered |
| `Return` | `base − expected`, then deduct full `base` | net = `−expected`; **commission stays with us** |
| `RTO` (not previously settled) | excluded entirely | expect a matching credit note in the ledger |
| `RTO` (previously settled) | negative line at `amount_paid` | the −238.85 clawback case |

### 3.4 Commission variance

Tolerance **±0.01 per line** — the portal rounds `SG Commission` to 2 decimals, so effective rates
read 0.14996 rather than 0.15. These are not breaks.

| Condition | Classification | Action |
|---|---|---|
| `\|variance\| ≤ 0.01` | clean | none |
| `variance > 0.01` | **overcharge** | we charged more than contractual — owed back to the vendor |
| `variance < −0.01`, and `actual/base` matches another configured rate | **wrong rate applied** | system error, investigate |
| `variance < −0.01`, otherwise | **promotional absorption** | a coupon reduced what we collected |

**Promotional absorption is not a vendor claim.** We bear that expense. The vendor is still paid
`base − expected` using the *contractual* commission, not the reduced amount we actually collected.
The shortfall is recorded as a margin-leakage KPI. Corroborate with a non-empty `Coupon Name` /
`Coupon Value` where present, but do not require it — classification is driven by the variance.

Worked example: contractual commission 12, actually charged 8. Vendor is paid `base − 12`. We
absorb 4 and report it as promotional leakage.

### 3.5 Matching

- Aggregate portal lines to order level: `sum(gmv)`, `count(lines)`, `set(status)`, `min(date)`.
- Aggregate ledger debits per order.
- Join on order number. Tolerance **±0.05 per order** (vendor rounds on their own unit-price base;
  four of 37 orders in the sample differ by exactly 0.01).
- Classify statement orders with no portal counterpart by invoice date:
  - before period start → `PRIOR_PERIOD` (informational)
  - after period end → `LATER_PERIOD` (informational)
  - inside the period → `MISSING_FROM_PORTAL` (**real exception**)

Without that three-way split, the exception list on the sample data opens with 21 rows of noise.

### 3.6 Order-number extraction

```python
ORDER_RE = re.compile(r"SG-SH-\d+")
order_no = m.group(0) if (m := ORDER_RE.search(clean(raw_desc))) else None
```

Handles `‪-SG-SH-01397# طلب‬`, `Turki Aldhry- SG-SH-01990`, and unprefixed variants.
**Never use `رقم المرجع`** — `SG-SH-05785` appears there as `SG-SH-0578`.

### 3.7 Credit-note resolution

Return invoices carry **no order number in the description** — only the truncated reference.
Resolve in two steps:

1. Prefix-match the truncated reference against known order numbers.
2. Disambiguate on amount.

`SG-SH-0611` matches both `SG-SH-06115` (108.00, Delivered) and `SG-SH-06118` (168.00, RTO). Only
the amount separates them. If prefix + amount still yields more than one candidate, raise
`AMBIGUOUS_CREDIT_NOTE` — never guess.

---

## 4. Architecture

```
frontend ──upload (3–4 xlsx)──> POST /api/reconcile
                                      │
                                      ├─ parser registry [category]
                                      ├─ normalizers → canonical models
                                      ├─ rules engine [category]
                                      ├─ matcher
                                      └─ ReconResult → in-memory store {job_id: result}
                                                          │
                              GET /api/jobs/{id}/summary ─┤
                              GET /api/jobs/{id}/orders   ─┤
                              GET /api/jobs/{id}/exceptions┤
                              GET /api/jobs/{id}/export ──┘ (xlsx, built on demand)
```

### 4.1 No-database consequences

Results live in a process-local dict with a TTL. Three consequences to design around explicitly:

1. **Single worker only.** Run `uvicorn app:api --workers 1`. With multiple workers the export
   request can land on a process that never saw the job. If you later need multiple workers, the
   fix is a shared cache, not a bigger dict.
2. **Restart loses everything.** The frontend must treat `404 JOB_NOT_FOUND` as "re-upload", not
   as an error state.
3. **No settlement history.** Clawbacks depend on §2.4 being uploaded. Document this in the UI.

```python
from cachetools import TTLContainer  # or a plain dict + background sweep
JOBS: TTLCache[str, ReconResult] = TTLCache(maxsize=64, ttl=3600)
```

Cap upload size (~10 MB/file) and job count — an unbounded in-memory store is the only real
availability risk in this design.

### 4.2 Stack

```
fastapi, uvicorn, pydantic>=2, pandas, openpyxl, xlsxwriter, python-multipart, cachetools
```

Dev: `pytest`, `ruff`, `mypy`.

### 4.3 Project layout

```
app/
  main.py                  # FastAPI app, routers, CORS
  config.py                # rates, cutoff, tolerances, TTL, size caps
  models.py                # Pydantic canonical models
  store.py                 # TTL job store
  api/
    reconcile.py           # POST /api/reconcile
    jobs.py                # summary / orders / exceptions / variance / settlement
    export.py              # GET /api/jobs/{id}/export
  core/
    registry.py            # @register("eshop") for parsers and rules
    cleaning.py            # Cf-stripping, numeric coercion, date parsing
    matcher.py             # generic: aggregate, join, tolerance, classify
    exceptions.py          # exception codes + severities
  categories/
    eshop/
      parser.py            # portal / ledger / promo / settlement readers
      rules.py             # rate resolution, status treatment, variance
  export/
    workbook.py            # xlsxwriter report builder
tests/
  fixtures/                # the three sample files
  test_parser.py
  test_rules.py
  test_matcher.py
  test_golden.py
```

### 4.4 Canonical models

```python
class PortalLine(BaseModel):
    order_no: str; sku: str; merchant_sku: str | None
    order_date: date; gmv: Decimal; units: int
    actual_commission: Decimal
    coupon_name: str | None; coupon_value: Decimal | None
    status: OrderStatus
    brand: str | None; item_title: str | None

class LedgerEntry(BaseModel):
    doc_no: str; doc_type: LedgerDocType; doc_date: date
    debit: Decimal | None; credit: Decimal | None; balance: Decimal | None
    raw_ref: str; raw_desc: str
    order_no: str | None            # extracted, not raw_ref
    match_state: MatchState

class OrderRecon(BaseModel):
    order_no: str; order_date: date; line_count: int
    statuses: set[OrderStatus]
    portal_gmv: Decimal; vendor_amount: Decimal | None; amount_diff: Decimal | None
    expected_commission: Decimal; actual_commission: Decimal
    commission_variance: Decimal; variance_class: VarianceClass
    payable: Decimal; adjustments: Decimal; net_payable: Decimal
    flags: list[str]

class ReconResult(BaseModel):
    job_id: str; category: str; merchant_id: str | None
    period_start: date; period_end: date
    orders: list[OrderRecon]
    unmatched_ledger: list[LedgerEntry]
    exceptions: list[ReconException]
    summary: Summary
```

Use `Decimal` throughout, never `float`. Quantize to 2dp with `ROUND_HALF_UP` at every monetary
boundary.

---

## 5. Pipeline stages

1. **Parse** — detect header row, map by header text, coerce types, clean text.
2. **Extract order numbers** — regex against cleaned description (§3.6).
3. **Resolve credit notes** — prefix + amount disambiguation (§3.7).
4. **Apply rules** — rate resolution, status treatment, variance classification (§3).
5. **Match** — aggregate, join, tolerance, classify unmatched (§3.5).
6. **Build result** — orders, exceptions, summary; store under `job_id`.

---

## 6. Exception taxonomy

| Code | Severity | Meaning |
|---|---|---|
| `ORDER_MISSING_IN_VENDOR` | high | in portal, absent from statement |
| `MISSING_FROM_PORTAL` | high | in statement within period, absent from portal |
| `AMOUNT_MISMATCH` | high | order total differs by more than ±0.05 |
| `COMMISSION_OVERCHARGE` | high | charged above contractual — owed back to vendor |
| `COMMISSION_WRONG_RATE` | high | rate matches a different configured rate |
| `RTO_WITHOUT_CREDIT_NOTE` | high | RTO with no reversing credit in the ledger |
| `CREDIT_NOTE_WITHOUT_RTO` | high | credit note with no RTO/Return in portal |
| `AMBIGUOUS_CREDIT_NOTE` | high | prefix + amount left multiple candidates |
| `CLAWBACK_UNRESOLVED` | high | prior-cycle reversal with no settlement history |
| `UNPARSEABLE_LEDGER_ROW` | medium | row could not be typed or valued |
| `PROMO_ABSORBED` | info | coupon reduced our commission; we bear it |
| `PENDING_DELIVERY` | info | Ready to Pickup — paid but not yet delivered |
| `PRIOR_PERIOD` / `LATER_PERIOD` | info | statement order outside the portal window |

Each carries `code`, `severity`, `order_no`, `amount`, `detail`.

---

## 7. API

| Method | Path | Returns |
|---|---|---|
| `POST` | `/api/reconcile` | multipart: `portal`, `vendor`, `promo`, optional `settlements`, plus `category`, `period_start`, `period_end` → `{job_id, summary}` |
| `GET` | `/api/jobs/{id}/summary` | KPI block |
| `GET` | `/api/jobs/{id}/orders` | paginated matched detail, filterable by status / flag / variance class |
| `GET` | `/api/jobs/{id}/exceptions` | grouped by code and severity |
| `GET` | `/api/jobs/{id}/commission-variance` | over/undercharge detail + absorbed total |
| `GET` | `/api/jobs/{id}/settlement` | computed net vs vendor closing balance, with bridge |
| `GET` | `/api/jobs/{id}/export` | `.xlsx` stream |

Summary KPIs: gross sales · 15% base · 10% base · commission earned · payable before adjustments ·
adjustments · **net payable** · vendor closing balance · unexplained difference · exception counts
by severity · promotional absorption total.

The settlement bridge is the most valuable screen. On the sample cycle it shows computed payable
14,223.16, less clawback 238.85, less return adjustment 108.00 → net **13,876.31**, against a
vendor closing balance of 17,424.75, bridged by prior- and later-period items.

---

## 8. Excel export

Sheets: **Summary** · **Matched Detail** · **Exceptions** · **Commission Variance** ·
**Settlement Bridge** · **Unmatched Ledger Rows**.

Built on demand from the stored result with `xlsxwriter` and streamed via `StreamingResponse`.
Freeze header rows, autofilter every table, currency format `#,##0.00`, conditional fill on
non-zero variance, and a legend on Summary naming the rates and cutoff used for that run.

---

## 9. Validation targets

Golden-file tests against the three sample files:

| Assertion | Expected |
|---|---|
| Portal lines parsed | 75 |
| Orders after aggregation | 37 |
| Ledger transactions after dedup | 64 |
| Orders matched within ±0.05 | 37 |
| Orders matching exactly | 33 |
| Orders differing by 0.01 | `SG-SH-05785`, `SG-SH-06874`, `SG-SH-07568`, `SG-SH-08458` |
| 15% base | 11,996.91 |
| 10% base | 4,473.10 |
| Payable before adjustments | 14,223.16 |
| Clawback (`SG-SH-03344`) | −238.85 |
| Return adjustment (`SG-SH-07435`) | −108.00 |
| Net payable | 13,876.31 |
| `PRIOR_PERIOD` + `LATER_PERIOD` orders | 21 |
| Ambiguity case resolved | `SG-SH-0611` → credit 168.00 maps to `SG-SH-06118` |

The legacy workbook reports 4,473.60 / 14,223.61 / 13,876.76. The 0.50 difference is the three
rows where it used catalogue price instead of GMV. **Our figures are the correct ones** — do not
tune the engine to reproduce the workbook.

---

## 10. Build order

1. Parsers + normalizers, with golden-file tests (37 orders, 33 exact).
2. Rules engine with dated rates, asserting the two base figures.
3. Matcher + exception taxonomy.
4. FastAPI endpoints + in-memory job store.
5. Excel export.
6. Dashboard integration.
7. Second category.

---

## 11. Extending to the other 10 categories

Register parsers and rules by category; keep models and matcher generic.

```python
@register("eshop")
class EShopParser(BaseParser): ...

@register("eshop")
class EShopRules(BaseRules): ...
```

The Food category differs substantially — its vendor file is a **line-level report** joined on
order id, carrying the vendor's own commission percentage, delivery fee and promotion splits. The
commission check there is vendor-side (compare their rate to ours), not contract-side. The matcher
(aggregate → join → tolerance → classify) is reusable as-is; everything else goes behind the
registry.

---

## 12. Findings to raise with the vendor

Two live discrepancies surfaced during analysis, independent of the build:

1. **Commission journal short by 134.78.** The 21 July journal posted 1,048.43. Cycle sales were
   7,888.06 and we paid 6,704.85 — exactly 85% — implying commission of 1,183.21.
2. **Overpayment of 238.85 on `SG-SH-03344`.** That payment was calculated on gross sales that
   included an order later returned. This is the clawback now carried into the current cycle, and
   it is why settlement history must be an explicit input rather than an afterthought.

---

## 13. Open questions

1. **Prior settlement history** — does a source for §2.4 exist today? Without it, the first run
   cannot compute clawbacks and will raise `CLAWBACK_UNRESOLVED` instead.
2. **Promo validity** — is 9 August global to the campaign, or can individual SKUs carry their own
   windows? Cheap to model per-SKU now, expensive to retrofit.
3. **Run scope** — one merchant per job, or the whole eShop category at once? The portal export
   carries `Merchant Id`, but the statement is per-vendor, which points to one vendor per job.
4. **Rate configuration surface** — do rates and the cutoff come from `config.py`, or should the
   frontend send them per run? A run-level override makes back-dated recalculation trivial.

---

## Appendix A — Assumptions locked by the client

| Question | Answer |
|---|---|
| Promo cutoff | Order date, ≤ 9 August inclusive (follow the evidence) |
| Commission base | Always portal GMV |
| Shipping | Vendor bears it, **settled outside reconciliation** |
| Returns | Commission stays with us; net = −commission |
| Matching tolerance | Portal rounds to 2dp; ±0.01 line, ±0.05 order |
| Status list | Delivered, Ready to Pickup, Return, RTO — complete |
| Undercharged commission | Promotion/coupon; **we** bear the expense, vendor paid on contractual rate |
