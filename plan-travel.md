# Travel Reconciliation — Implementation Plan

**Scope:** Travel category (hotels and flights, vendor *Almosafer*). Same FastAPI backend, same
Excel-only, no-database constraints as [plan.md](./plan.md).
**Status:** every figure below was verified against the seven sample workbooks, not assumed.

---

## 1. Business model

We resell the vendor's inventory. The customer pays us the full booking value; we retain a
commission and pay the vendor the remainder. Two verticals run through one pipeline:

| Vertical | Booking id prefix | Our commission |
|---|---|---|
| Hotel | `H…` | 5% of the order total |
| Flights | `A…` | 3% of the order total |

The vendor bills its commission **plus 15% VAT when the booking carries no VAT of its own** —
an international booking. That single rule is the source of almost every difference this
reconciliation finds.

| | VAT on the invoice (local) | no VAT (international) |
|---|---|---|
| flight | 3.00% | 3.45% = 3% × 1.15 |
| hotel | 5.00% | 5.75% = 5% × 1.15 |

**The portal charges the local rate on every booking, international included.** The vendor's
commission therefore exceeds ours on every international booking, and the gap — the *VAT uplift
shortfall* — is money we never collected from the customer. It is reported per booking and summed.

Reconciliation proves that our portal data and the vendor's weekly claim agree on what was booked,
what was refunded, and what we owe.

---

## 2. Inputs

**One file per role per run.** Each file is searched for the sheets its role needs, so a workbook
holding several tabs is read whole — hotel and flights live as two tabs in one portal workbook,
exactly the way the vendor's own claim file 13 carries `Hotel` and `Flight` tabs.

### 2.1 Portal export (master data) — one row per order

38 columns. Identical schema for both verticals; `Vertical` separates them.

| Column | Use |
|---|---|
| `Order Date` | period boundary; display |
| `SG Order ID` | per-export row sequence — **not stable between exports**; used only for the wallet join |
| `Partner Order ID` | **the booking id and the join key.** `-` on unbooked rows |
| `Vertical` | `Hotel` / `Flights` — rows with any other value are not travel and are skipped |
| `Order Status` | `Processed`, `Cancelled`, `Expired`, `Pending` |
| `Payment Status` | `Paid`, `Completed`, `Pending`, `Failed`, `Refunded`, `-` |
| `Payment Method` | `Card`, `Tamara`, `Apple Pay`, `Wallet`, and the `X + Wallet` combinations |
| `Order Total` | **base amount** for commission and payable |
| `VAT` | > 0 ⇒ local booking; 0 ⇒ international |
| `Commission Charged` | what we actually took. Hotels leave `Commission Amount` at 0, so this is the one column both verticals populate |
| `Wallet Amount`, `Applepay/Card Amount` | the payment split; they sum to `Order Total` |
| `Net Payable Amount to Partner` | `Order Total − Commission Charged` |
| `SG Comission %` | `"3.00 %"` on flights, `"-"` on hotels — diagnostic only, the rate is resolved from the vertical |
| `Refund Amount`, `Compensation Amount` | **always 0 in the export.** The portal never shows a refund |

Only `Processed` and `Cancelled` rows carry a `Partner Order ID` — 45 of the sample's 206 rows.
The rest never reached the vendor.

The booking id **embeds its own booking date**: `A260726150195500` → 2026-07-26 (`YYMMDD` at
positions 1–7). Verified against all 59 vendor invoice rows: 59/59 exact. This is the only reliable
date for a credit note, because the vendor's own `Booking Date` column holds the *credit's* date on
every one of the seven credit rows.

### 2.2 Vendor claim (line level) — one row per invoice line item

~37 columns, and they drift between weeks: one file adds five hotel columns, another drops
`Supply Date`, another drops `Entitlement`. Sheet names drift too (`Sheet1`,
`Aug 01 to 08 Mosafer Report`, `Vedor Report`). Everything is therefore located by header text.

| Column | Use |
|---|---|
| `Booking ID` | `<PartnerOrderID>_<ref>`, and `…_R0` on a refund. Group key = everything before the first `_` |
| `Invoice Number` + `line Item` | row identity, and the deduplication key |
| `Billing Type` | `Invoice` or `Credit for Returns` |
| `Service Type` | `A` = flight, `H` = hotel; always agrees with the booking-id prefix |
| `VAT %` / `VAT` | **the locality test.** 15 ⇒ local, 0 ⇒ international |
| `DOM/INT` | the vendor's own label — **wrong on one sample row**, carried for display, never trusted |
| `Total Invoice Amount` | the vendor's base |
| `commision amount ` | the vendor's commission (trailing space in the header) |
| `net payable amount ` | what they expect us to pay |
| `Airline Can Fee` | the cancellation fee retained out of a refund |

Two shapes that are not invoice lines share the data sheet and must be handled, not parsed:

- **trailing totals rows** — every cell blank but the sums;
- **a free-text adjustment row** — `"Adjustment for booking A260629226265500 comision"`,
  commission −66.82, which is the vendor correcting an earlier over-charge on that booking.

A second tab (`Sheet2` / `summary `) carries the vendor's own per-claim-period ledger: claim period,
total claim, commission, total payable, paid, difference. Optional — one sample file has none.

### 2.3 Wallet history (optional third upload)

`ID, Customer, Mobile Number, SG Order ID, Amount, Message, Date, Payment Type, Wallet Type`.

`Wallet Type` names the vertical (`Hotel`, `Flights`, and eleven values belonging to other
categories). Join to the portal on (`Wallet Type` ↔ `Vertical`, `SG Order ID`); negative `Amount` is
a payment, positive is a refund. Aggregation is required, not 1:1 — one sample order has seven
payment/refund pairs from a retry loop.

The file covers its own date window, which need not match the portal's. On the sample data the
wallet file spans 2026-08-01..15 while the portal spans 07-26..08-22, and **all nine flight rows
that fail to match fall outside that window** — which is why out-of-window is a separate verdict
from missing.

---

## 3. Business rules

### 3.1 Rates

Configuration, never hardcoded in the calculation:

```python
TRAVEL_HOTEL_RATE  = 0.05
TRAVEL_FLIGHT_RATE = 0.03
TRAVEL_VAT_RATE    = 0.15
```

```
contract_rate = base_rate                      if the booking carries VAT   (local)
                base_rate * (1 + VAT_RATE)     if it does not               (international)
```

Two rates and a VAT rate, not four: `0.05 × 1.15` and `0.03 × 1.15` are exact in `Decimal`, and
expressing them this way records *why* 5.75% and 3.45% exist.

### 3.2 Locality

**Locality is derived from VAT, never from the vendor's `DOM/INT` label.** Verified: portal
`VAT > 0` agrees with vendor `VAT % = 15` on all 45 matched bookings, while the `DOM/INT` label is
wrong on one row (labelled `INT`, VAT 15, and the vendor correctly billed the local 5.00%).

It is resolved **per vendor line**, so a booking whose lines disagree needs no special case. The
portal's VAT is a cross-check: a disagreement raises `VAT_BASIS_CONFLICT`. A booking with no vendor
lines falls back to the portal row.

### 3.3 Per-booking calculation

```
sg_rate      = portal_commission / portal_base            # their "Com Rate Check"
vendor_rate  = vendor_commission / vendor_base            # their "Vendor Rate Check"

sg_expected            = portal_base * local_rate         # what we did charge
vendor_expected        = Σ(line_base * contract_rate(line))
commission_as_vendor   = portal_base * contract_rate      # their "Actual As Vendor"
net_payable_as_vendor  = portal_base - commission_as_vendor
vat_uplift_shortfall   = commission_as_vendor - sg_expected
```

**`net_payable_as_vendor` settles the payable.** We pay on our own base at the vendor's contract
rate; where the two bases differ, the difference is reported rather than silently adopted.

Anything the vendor bills *above* its contract rate is still an over-charge to claim back. The
sample's one true outlier — `A260629226265500` at 4.4724% against a contractual 3.45% — lands as
`VENDOR_COMMISSION_OVERCHARGE` of 66.82, which the later claim file's −66.82 adjustment nets to zero
once linked.

### 3.4 Status treatment

| Portal status | Treatment |
|---|---|
| `Processed` | normal case; expect matching vendor lines |
| `Cancelled` | expect a vendor credit note; **no commission should be retained** |
| `Expired`, `Pending` | never booked; excluded from the payable. Commission on such a row is an exception |

### 3.5 Refunds and cancellations

**The verdict is taken from the vendor side, because the portal never shows a refund** — its
`Refund Amount` column is 0 on every row of both sample exports.

A refund is a `Credit for Returns` line, usually with a `…_R0` booking reference, and sometimes
accompanied by a positive `Online - Change Fee` line. After summing the group:

| Condition | Verdict |
|---|---|
| net base within tolerance of 0 | **full refund** |
| net base still positive | **partial refund**; `retained = Airline Can Fee + change fee` |

Commission must come back with it. The sample shows both behaviours, and the rule reports the
difference: the hotel credit reverses its commission exactly (+42.49997 then −42.49997), while the
flight credits carry commission 0, leaving the original invoice's commission standing →
`COMMISSION_NOT_REVERSED_ON_REFUND`.

A credit whose original booking predates the portal window nets negative and is classified by the
**derived** booking date as `PRIOR_PERIOD`, not as a break. That is the five vendor-only groups in
the sample.

### 3.6 Grouping

One booking = one row of the report.

- **Portal side:** group on `Partner Order ID` with any trailing `-N` stripped. The first row by
  `(order date, SG order id)` is primary; any further row is marked `REPEAT_BOOKING_ROW` and
  excluded from every total, never double-counted.
- **Vendor side:** group on the booking id base. A multi-leg or multi-passenger booking fans out
  into 2–10 invoice lines (`A260813120395500` → 2, `A26080683165500` → 10) and is compared once,
  against the group's sum. Verified: the portal row already holds that sum.

### 3.7 Payment method

Each row records how much moved on which instrument: `Wallet Amount` and `Applepay/Card Amount`,
which must sum to `Order Total`. One sample row breaks that (order total 276.25, wallet 13.00,
card 0) → `PAYMENT_SPLIT_MISMATCH`.

The summary reports two cuts:

- **by label** — the raw `Payment Method` text, blanks included: the client's pivot filter;
- **by instrument** — the label split on `+`, so `Apple Pay + Wallet` contributes to both, which is
  the only way to answer "how much moved on wallet at all".

### 3.8 Wallet reconciliation (only when the third file is uploaded)

For every portal row with a wallet amount, compare the history's aggregate payment for that
(vertical, SG order id) against it:

| Condition | Verdict |
|---|---|
| amounts agree within tolerance | matched |
| both present, amounts differ | `WALLET_AMOUNT_MISMATCH` |
| no history entry, order date outside the file's window | `WALLET_OUT_OF_WINDOW` (informational) |
| no history entry, order date inside it | `WALLET_ENTRY_MISSING` |

Because `SG Order ID` is a per-export sequence rather than an identity — the same booking is 251 in
one export and 364 in another — a wallet file from a different generation than the portal export
would produce a page of false misses. When the in-window match rate falls below 50%,
`WALLET_KEY_UNSTABLE` is raised once for the run and says to re-export both files together.

---

## 4. Architecture

Travel does not fit eshop's `CategoryAdapter`: the grain is a booking rather than an order line,
the vendor file is a line-level invoice claim rather than an account ledger, the question under test
is rate parity between two parties rather than our commission against a contract, and the output
carries two blocks eshop has no concept of. Forcing it through the shared `engine`/`matcher`/`models`
would widen fourteen shared structures for one category, including teaching `OrderStatus` travel's
statuses — which eshop's own parser would then silently accept.

So the seam is raised one level: **travel owns its pipeline, its result model and its report.**

```
app/recon/
  inputs.py        Upload, ReconInputError            shared
  excel.py         SheetSchema, detect_sheets, …      shared, unchanged
  cleaning.py      to_decimal, to_date, money, …      shared, unchanged
  exceptions.py    codes, severities, sorting         shared, +travel codes
  xlsx.py          workbook formats and helpers       shared
  engine.py        the eshop pipeline                 unchanged
  categories/travel/
    models.py      rows, BookingRecon, TravelResult
    parser.py      4 sheet schemas + 4 readers
    rules.py       rates, grouping, refunds, accumulators
    wallet.py      wallet history and its checks
    pipeline.py    run(TravelRequest) -> TravelResult
    export.py      the 9-sheet report
```

`engine.py`, `matcher.py`, `models.py`, `export.py` and `categories/eshop/*` keep their behaviour
exactly; eshop's golden test passing untouched is the acceptance gate for the extraction.

---

## 5. Pipeline stages

1. **Vendor** — read every vendor-shaped sheet in the workbook; classify each row as invoice line,
   adjustment, or skip; deduplicate on `(invoice, line item, reference, total, commission)` so the
   same claim file uploaded twice cannot double the claim; then read the claim-period tab if present.
2. **Portal** — read every travel-shaped sheet; skip rows of another vertical; feed the
   payment-method accumulator; route booked rows into booking accumulators and keep unbooked rows
   only if they raise something.
3. **Period** — min/max portal order date, overridable per run.
4. **Link adjustments** to their booking by the id in their text.
5. **Build** one `BookingRecon` per group — the single rounding point.
6. **Exceptions** — booking, row, wallet and skip, sorted most severe first.
7. **Wallet**, when uploaded.
8. **Summary** from the unrounded accumulators, including the ÷1.15 ex-VAT commission split.

Every stage streams. The portal is the largest input and is reduced to per-booking state as it is
read; only rows that raise an exception are retained.

---

## 6. Exception taxonomy

| Code | Severity | Raised when |
|---|---|---|
| `BOOKING_MISSING_IN_VENDOR` | high | a portal booking the claim does not bill |
| `BOOKING_MISSING_IN_PORTAL` | high | a billed booking inside the window with no portal row |
| `BOOKING_BASE_MISMATCH` | high | the two bases differ beyond tolerance |
| `VENDOR_COMMISSION_OVERCHARGE` | high | billed above the contract rate |
| `VENDOR_COMMISSION_UNDERCHARGE` | medium | billed below it |
| `SG_COMMISSION_SHORT_OF_VENDOR_RATE` | high | the VAT uplift we never collected |
| `SG_COMMISSION_RATE_UNEXPECTED` | high | our own commission is not the local rate on our own total |
| `COMMISSION_NOT_REVERSED_ON_REFUND` | high | a full refund leaves commission standing |
| `REFUND_WITHOUT_PORTAL_CANCELLATION` | high | vendor credited it; the portal still says processed |
| `CANCELLATION_WITHOUT_VENDOR_CREDIT` | high | the portal cancelled it; no credit note |
| `PAYMENT_SPLIT_MISMATCH` | high | wallet + card ≠ order total |
| `VERTICAL_CONFLICT` | high | booking id, portal vertical and service type disagree |
| `WALLET_ENTRY_MISSING` | high | wallet-paid, in window, absent from the history |
| `WALLET_AMOUNT_MISMATCH` | high | the history paid a different amount |
| `COMMISSION_ON_UNCOMPLETED_ORDER` | medium | commission on an expired or pending row |
| `VAT_BASIS_CONFLICT` | medium | the two sides disagree on whether VAT applies |
| `ADJUSTMENT_UNLINKED` | medium | an adjustment whose booking could not be identified |
| `UNPARSEABLE_VENDOR_ROW` | medium | an invoiced row with no booking id |
| `WALLET_KEY_UNSTABLE` | medium | the wallet and portal exports are different generations |
| `PARTIAL_REFUND` | info | amount = the fee retained |
| `REPEAT_BOOKING_ROW` | info | a second portal row for one booking |
| `MULTI_LEG_BOOKING` | info | more than one invoice line under one booking |
| `ADJUSTMENT_APPLIED` | info | an adjustment matched to its booking |
| `DUPLICATE_VENDOR_LINE` | info | the same claim line uploaded twice |
| `WALLET_OUT_OF_WINDOW` | info | outside the history file's coverage |
| `WALLET_HISTORY_UNMATCHED` | info | a history entry with no portal row |
| `PRIOR_PERIOD` / `LATER_PERIOD` | info | a vendor-only group dated outside the window |

---

## 7. API

```
POST /api/v1/reconcile/travel?format=json|xlsx
```

Multipart: `portal` (required), `vendor` (required), `wallet` (optional), `finance` (optional); form fields
`period_start`, `period_end`, `hotel_rate`, `flight_rate`, `vat_rate`. Session-cookie auth, the same
415 / 413 / 422 mapping as the eshop endpoint, nothing persisted, the whole result in the response.

`POST /api/v1/reconcile` keeps its shape, so nothing that exists today changes.

---

## 8. Excel export

The same tabs as the food report, which the client signed off on (plan-food.md section 8).

| # | Sheet | Contents |
|---|---|---|
| 1 | Dashboard | the client's dashboard: the headline block, cash collected against the portal total, the commission at the contract rate by vertical, with its ÷1.15 ex-VAT figure, and a "Breakdown By Most Used" table by the claim's Service Provider |
| 2 | Orders | their `Analysis` tab: the portal columns, the rate checks (`Com Rate Check`, `Vendor Rate Check`, `Actual As Vendor`, `Net Payable`, the base difference, the VAT uplift), then the claim columns, one row per matched booking. The totals carry their `Sheet1` block, `SHOPPINGGATE ‖ ALMOSAFER ‖ DIFFERENCE`, and the rates legend. Unmatched bookings are listed beneath with the reason, out of the totals |
| 3 | Exceptions | severity, code, order, the order's status, amount, detail |
| 4 | Wallet | only when a wallet history was uploaded: each wallet-paid row against the history, then the movements with no portal row |
| 5 | B2C Invoices ERP | one invoice line per matched booking, the Orders sheet's own rows, ready to import: unit price `=Orders!<Commission Charged>/1.15`, tax `Domestic Sales (15%)`. Refunded, cancelled and one-sided bookings are not billed |
| 6 | Portal | the portal upload, copied row for row |
| 7 | Vendor | the vendor upload, copied row for row |
| 8 | Finance Report | only when a finance report was uploaded: the report, copied row for row. Orders then gains five lookup columns after the verdict (Cash Back Reward, Vertical Name, Reward Code, Reward Type, Outcome), each the row's SG Order ID looked up in the report's Order ID, `NA` when there is none |

B2C Invoices ERP, Portal and Vendor need the uploads themselves, so a report built from the
result alone has the first three sheets (and Wallet). The JSON result keeps everything the
workbook leaves out.

---

## 9. Validation targets

Checked against the client's own `Analysis` and `Sheet1` tabs in claim file 13:

| Figure | Expected |
|---|---|
| Portal rows read (both verticals) | 206 |
| Bookings matched | 45 of 45 |
| Vendor-only groups | 5, all prior-period refunds |
| Vendor invoice lines / adjustments | 60 / 1 |
| Hotel base differences | −1.06, −2.11, −1.81 → **−4.98** |
| Commission difference on those | **−0.28635** |
| Net payable difference on those | **−4.69365** |
| Claim 16–22 Aug: ours vs theirs | **6,728.9633** vs **6,729.96235** |
| Vendor over-charge on `A260629226265500` | 66.82, netted by the −66.82 adjustment |
| Payment-split breaks | 1 (SG order 198) |
| Multi-leg bookings | 2 lines and 10 lines |

---

## 10. Findings to raise with the vendor

1. **The VAT uplift is not being collected from customers.** We charge 3% / 5% on international
   bookings; the vendor bills 3.45% / 5.75%. Every international booking loses the difference.
   Either the portal starts charging the uplifted rate, or it is a known cost of sale.
2. **`A260629226265500` was billed at 4.4724%** instead of 3.45% — 66.82 over. The vendor has since
   credited it; the reconciliation confirms the credit matches the over-charge.
3. **Hotel base amounts drift by 1–2 SAR** on several bookings (1161.70 vs 1162.76, 604.44 vs
   606.25, 758.31 vs 760.42) — a rate-of-exchange or supplier-price timing difference worth pinning
   down, since we pay on their base today.
4. **Flight refunds do not reverse commission.** The hotel credit reverses it exactly; the flight
   credits leave it standing.
5. **SG order 198 collected 13.00 against a 276.25 total.** A payment-capture defect on our side,
   not the vendor's.
6. **`DOM/INT` on the claim is unreliable** — at least one row is labelled `INT` while carrying 15%
   VAT. VAT is the only trustworthy locality signal.
