# Food Reconciliation — Implementation Plan

**Scope:** Food category, vendor *The Chefz*. Same FastAPI backend, same Excel-only, no-database
constraints as [plan.md](./plan.md).
**Status:** rules below were checked against `Food 01 TO 06 2026.xlsx` (portal tab, vendor tab,
and the hand-built `ANALYSIS & RECON` sheet). `Final Summary` is out of scope.
**Implemented** in `backend/app/recon/categories/food/` and `POST /api/v1/reconcile/food`.

---

## 1. Business model

We sell The Chefz's orders. The customer pays us. We keep a commission and pay the restaurant
the rest, plus the share of the delivery fee that is ours to bear.

The commission rate is **on the vendor row**, not a contract constant. In the sample it is
4%, 4.5%, 4.75%, or 5% per order. The portal stores the fee it charged, rounded to 2 decimals.
The vendor stores the same fee to 3 decimals. Reconciliation compares those two fees. It does
not recompute a rate of our own.

A delivery fee is split three ways, and the three shares add back to the vendor's
`original_deliveryFee` on every matched order in the sample:

| Share | Who pays | Source |
|---|---|---|
| Customer | the customer | portal `SG Delivery Expense` |
| Chefz | the restaurant | vendor `promotion_chef_charge_amount` |
| Shopping Gate | us | vendor `promotion_shoppinggate_charge_amount` |

What we owe on a clean order is the vendor's own "Due from shoppinggate":

```
due = vendor total + SG delivery charge − vendor fees
```

`Final Summary` (prior months, Wasel, compensation clawbacks, payments already made) is a
running account. It is not produced from these two uploads and is not part of this pipeline.

---

## 2. Inputs

Two files per run, and an optional wallet history (section 2.4). Each file is searched for the sheet its role needs. Columns
are located by header text, so a title row above the headers is ignored. The sample vendor tab
has that title row (`Common Field`, `Rate %`) and the real headers on row 2.

### 2.1 Portal export — one row per order

Same 38-column export travel uses. `Vertical` = `Food` is the discriminator. Rows with any
other vertical are not food and are skipped. The three columns that are all named `Customer`
are ignored. They are not a join key and they are not shown.

| Column | Use |
|---|---|
| `Order Date` | display. Not a join key and not a check |
| `Order Time` | display |
| `SG Order ID` | display only. Not an identity |
| `Partner Order ID` | **join key** |
| `Vertical` | must be `Food` |
| `Provider Name` | display. Sample is `The Chefz` on every row |
| `Food Order Type` | display (`Solo Order`, `Group Order`). No rule difference |
| `Restaurant ID/Seller ID`, `Restaurant Name` | display |
| `Order Status` | only `Delivered` is reconciled |
| `Payment Status`, `Payment Method` | display |
| `Sub Total` | food amount on our side. Diagnostic against `order_chef_total` |
| `Promocode`, `SG Discount/Promo Amount` | display. Discount explains `Total Amount − Order Total` |
| `SG Delivery Expense` | customer delivery share |
| `VAT`, `Order Total` | display |
| `Total Amount` | **the amount under test** against vendor `total` |
| `SG Comission %` | display. A rounded echo of the fee, not the check |
| `Commission Amount` | equals `Commission Charged` on every sample row |
| `Commission Charged` | **the fee under test** against vendor `shoppinggate_fees` |
| `Partner Payout`, `Net Payable Amount to Partner` | display. Equal on every sample row. Not the settlement figure |
| `Compensation Amount` | reported, **not deducted** |
| `Refund Amount` | sample is blank. A non-zero value on a delivered order is an exception |
| `Wallet Amount`, `Applepay/Card Amount` | display |

Identities that hold on all 302 portal rows, used as parse checks rather than as the match:

```
Order Total  = Sub Total − SG Discount/Promo Amount + SG Delivery Expense
Total Amount = Order Total + SG Discount/Promo Amount
```

### 2.2 Vendor report — one row per order

A Chefz order export, not an account ledger. Headers may sit below a title row.

| Column | Use |
|---|---|
| `requestedDeliveryDate` | display. Not a join key and not a check |
| `brand_name` | display |
| `order_id` | **join key.** Matches `Partner Order ID` |
| `order_no` | display. A different number; never a key |
| `customer_name` | display |
| `total` | **the amount under test** against portal `Total Amount` |
| `order_chef_total` | restaurant food total. Diagnostic against `Sub Total` |
| `shoppinggate_percentage` | the rate the vendor applied. Display |
| `shoppinggate_fees` | **the fee we settle on** |
| `original_deliveryFee` | gross delivery, before the split |
| `promotion_shoppinggate_charge_amount` | SG's delivery share |
| `deliveryFee` | display |
| `order_status` | only `Delivered` is reconciled |
| `order_items` | display |
| `promotion_discount` | display |
| `promotion_chef_charge_amount` | Chefz's delivery share |

Three figures the sample computes in extra columns, and which this pipeline computes the same
way. They are not required in the upload:

```
Due from shoppinggate             = total + promotion_shoppinggate_charge_amount − shoppinggate_fees
Net Invoice from shoppinggate     = shoppinggate_fees
Net Delivery Invoice to shoppinggate = original_deliveryFee − promotion_chef_charge_amount
```

### 2.3 Sheet detection

Food and travel share the portal header. Detection keys on header text, so the food schema
excludes any sheet that also carries vendor headers (`order_id`, `shoppinggate_fees`). The
vendor schema excludes any sheet that also carries `Partner Order ID`. That keeps the
hand-built `ANALYSIS & RECON` tab, which has both sides bolted together, from being read as
either input. `tests/test_travel_schemas.py` already pins the shared-header trap; food adds
the mirror case.

`SG Order ID` is not a key, here or anywhere else.

### 2.4 Wallet history (optional)

The same wallet history travel reads (plan-travel.md section 3.8). Food movements are the `Food`
wallet type, filed under the portal's `SG Order ID` — the one place that sequence is used. A
wallet-paid row with no `SG Order ID`, as on every row of the sample portal tab, cannot be looked
up: it is counted, and `WALLET_ORDER_ID_MISSING` is raised once for the run. The check never moves
the settled payable.

---

## 3. Business rules

### 3.1 Which rows are in the payable

An order is **settled** only when all of these hold:

1. It exists on both files, joined on `Partner Order ID` = `order_id`.
2. Both statuses are `Delivered`.
3. `|portal Total Amount − vendor total| ≤ 0.01`.
4. `|portal Commission Charged − vendor shoppinggate_fees| ≤ 0.01`.
5. The delivery split identity in section 3.3 holds within 0.01.
6. The order id is unique on each file.
7. Portal `Refund Amount` is blank or zero.

Dates are not part of the match. `ANALYSIS & RECON` pairs a row when the ids are equal
(`Partner Order ID = order_id`) and never compares `Order Date` with
`requestedDeliveryDate`. Both dates stay on the sheet as display.

Anything else is an exception. It is still listed, with both sides where we have them, and it
is **excluded from the settled payable**. Paying the vendor's number on a break would hide the
break inside the total. The sample's hand-built sheet did that for the 4 SAR order and parked
the gap in a difference line. This pipeline does not.

There is no configured rate and no per-run rate override. The fee on the vendor row is the
fee we compare and the fee we settle.

### 3.2 Status

Only `Delivered` is reconciled. A row with any other status is counted and listed as
`STATUS_NOT_DELIVERED` and is not joined into the payable. The sample portal and vendor tabs
are Delivered-only; seven cancelled orders live on a separate sheet and never enter either
upload. If a later export includes them, they stay out.

A delivered portal row whose vendor status is not Delivered, or the reverse, is
`STATUS_MISMATCH`, not settled.

### 3.3 The six checks

These are the columns AM–AR of `ANALYSIS & RECON`. Same formula on every paired row.

| Check | Formula |
|---|---|
| Match id | `Partner Order ID = order_id` |
| Order amount difference | `Total Amount − total` |
| Rate check | `Commission Charged − shoppinggate_fees` |
| Additional delivery SG bears | `original_deliveryFee − SG Delivery Expense − promotion_chef_charge_amount` |
| Additional delivery Chefz bears | `promotion_chef_charge_amount` |
| Customer delivery | `SG Delivery Expense` |

The rate column compares **fees in SAR**, not the displayed percentages. Portal `SG Comission %`
is a 2-decimal echo (`4.51 %` where the vendor rate is `4.5`). About 50 sample rows differ by
0.005 because the vendor keeps three decimals (`1.835`) and the portal rounds to `1.84`.
Tolerance ±0.01 leaves those clean. The one real fee break is 0.20.

Delivery identity, true on every paired sample row:

```
SG share + Chefz share + customer share = original_deliveryFee
SG share                                 = promotion_shoppinggate_charge_amount
```

SG share is computed as `original_deliveryFee − customer − Chefz`, then compared with the
vendor's SG charge. A miss is `DELIVERY_SPLIT_MISMATCH`.

### 3.4 Payable

On a settled order:

```
due = vendor total + promotion_shoppinggate_charge_amount − shoppinggate_fees
```

That is the vendor fee, not the portal's rounded commission. The half-cent gaps stay inside
tolerance and do not move the payable.

`Compensation Amount` is summed and shown. It is not subtracted. The sample carries 776 SAR
across 17 delivered orders, and the hand-built net payable ignores it.

### 3.5 Amount breaks

Order `290238794` is the sample's only amount break, and its fee break is the same fact:

| | Portal | Vendor |
|---|---|---|
| Food (`Sub Total` / `order_chef_total`) | 39 | 43 |
| Order amount (`Total Amount` / `total`) | 41 | 45 |
| Fee | 1.95 | 2.15 |

The fee gap is 5% of the 4 SAR food gap, and both sides show rate 5. One exception,
`FOOD_AMOUNT_MISMATCH`, carries both deltas. A fee gap on an order whose amounts agree is a
separate `FEE_MISMATCH`.

The order stays on the sheet. Its vendor due (49.75) is reported as held, not added to the
settled payable.

### 3.6 One-sided orders

| Case | Exception | In the payable |
|---|---|---|
| Portal row, no vendor row | `ORDER_MISSING_IN_VENDOR` | no |
| Vendor row, no portal row | `MISSING_FROM_PORTAL` | no |

No period parameter. The two files are the cycle. An order that has no counterpart id is an
error. A difference between the portal order date and the vendor delivery date is not.

### 3.7 Rounding

`Decimal` throughout. Tolerance comparisons use the unrounded values. A reported total is
quantized once, 2dp, half up, at the boundary where it is shown. The portal fee is already
2dp; the vendor fee is 3dp; do not re-round the vendor fee before the comparison.

---

## 4. Architecture

Food does **not** go through `CategoryAdapter`. That adapter is an order-line portal joined
to an account ledger, with a contract rate and a promo catalogue. Food is order-to-order, the
rate arrives on the vendor row, and the result is a delivery split plus a fee check. Forcing
it through the adapter would widen the shared eshop types the way travel was not allowed to.

Own pipeline, same shared pieces travel uses:

```
app/recon/categories/food/
  models.py      rows, FoodOrder, FoodResult
  parser.py      portal and vendor schemas + readers
  rules.py       the six checks, due, tolerances
  pipeline.py    run(FoodRequest) -> FoodResult
  export.py      the workbook
```

`engine.py`, `matcher.py`, and `categories/eshop/` and `categories/travel/` stay as they are.
Eshop's and travel's golden tests passing untouched is the acceptance gate.

New exception codes live in `exceptions.py`, food-specific, not borrowed from eshop. A reader
of an exception should not have to know which category raised it. `ORDER_MISSING_IN_VENDOR`
and `MISSING_FROM_PORTAL` already mean the right thing and are reused. The rest are new.

---

## 5. Pipeline stages

1. **Portal** — find the food-shaped sheet, skip other verticals, skip non-Delivered (record
   them), reduce to one record per `Partner Order ID`.
2. **Vendor** — find the vendor-shaped sheet, skip non-Delivered (record them), reduce to one
   record per `order_id`.
3. **Duplicates** — an id that appears twice on one file raises `DUPLICATE_ORDER_ID` and none
   of those copies are settled.
4. **Pair** on the id. One-sided rows become exceptions.
5. **Checks** on each pair: amount, fee, delivery split, refund. Dates are carried through and not tested.
6. **Due** on the pairs that pass. Held pairs keep a vendor due for the report and contribute
   nothing to the settled total.
7. **Summary** from the settled rows, plus the held and one-sided totals beside it.

Uploads stream to temp files. Sheets are read row by row. Nothing is persisted.

---

## 6. Exception taxonomy

| Code | Severity | Raised when |
|---|---|---|
| `ORDER_MISSING_IN_VENDOR` | high | delivered portal order with no vendor row |
| `MISSING_FROM_PORTAL` | high | delivered vendor order with no portal row |
| `FOOD_AMOUNT_MISMATCH` | high | `Total Amount` and `total` differ by more than 0.01. The fee delta is attached when the fee is off by the same order |
| `FEE_MISMATCH` | high | fees differ by more than 0.01 and the amounts agree |
| `DELIVERY_SPLIT_MISMATCH` | high | the three shares do not add to `original_deliveryFee`, or the computed SG share does not equal `promotion_shoppinggate_charge_amount` |
| `STATUS_MISMATCH` | high | the two delivered-filters disagree (one side delivered, the other not) |
| `DUPLICATE_ORDER_ID` | high | the same id on more than one row of one file |
| `REFUND_ON_DELIVERED` | high | a delivered portal row with a non-zero refund |
| `STATUS_NOT_DELIVERED` | info | a row that is not Delivered. Counted, not reconciled |

---

## 7. API

```
POST /api/v1/reconcile/food?format=json|xlsx
```

Multipart: `portal` (required), `vendor` (required), `wallet` (optional), `finance` (optional). No rate fields and no period fields.
Session-cookie auth, the same 415 / 413 / 422 mapping as the other recon endpoints, nothing
persisted, the whole result in the response.

`POST /api/v1/reconcile` and `POST /api/v1/reconcile/travel` keep their shapes.

---

## 8. Excel export

Not the running `Final Summary`. The client signed this workbook off, and travel and eShop
carry the same tabs.

| # | Sheet | Contents |
|---|---|---|
| 1 | Dashboard | the client's dashboard: the headline block, cash collected against the portal total, the delivery split between customer, us and the restaurant, and a "Breakdown By Most Used" table by restaurant |
| 2 | Orders | Portal columns (without the three `Customer` columns), the six checks, the vendor columns, and a verdict, with the totals and the summary block from the bottom of the hand-built sheet. Only matched orders that are not cancelled are listed and totalled; one-sided and cancelled orders stay on Exceptions |
| 3 | Exceptions | severity, code, order, the order's status, amount, detail |
| 4 | Wallet | only when a wallet history was uploaded: each wallet-paid row against the history, then the movements with no portal row |
| 5 | B2C Invoices ERP | one invoice line per order on the Orders sheet, ready to import: unit price `=Orders!<SG Delivery Expense>/1.15`, tax `Domestic Sales (15%)` |
| 6 | Portal | the portal upload, copied row for row |
| 7 | Vendor | the vendor upload, copied row for row |
| 8 | Finance Report | only when a finance report was uploaded: the report, copied row for row. Orders then gains five lookup columns after the verdict (Cash Back Reward, Vertical Name, Reward Code, Reward Type, Outcome), each the row's SG Order ID looked up in the report's Order ID, `NA` when there is none |

B2C Invoices ERP, Portal and Vendor need the uploads themselves, so a report built from the
result alone has the first three sheets (and Wallet). The JSON result keeps everything the
workbook leaves out.

---

## 9. Validation targets

Taken from the portal tab and the vendor tab of `Food 01 TO 06 2026.xlsx`, not from the
hand-built analysis tab. That tab was built from a slightly wider portal extract: it includes
four 31 Aug orders that are absent from `PORTAL REPORT`, and it omits four 6 Sep portal orders
that are absent from `VENDOR REPORT`.

| Figure | Expected |
|---|---|
| Portal rows / vendor rows | 302 / 302, all `Delivered`, all dates 2026-09-01 .. 2026-09-06 |
| Paired on id | 298 |
| Portal only | 4 (`291072289`, `291072796`, `291073381`, `291073584`), all 6 Sep |
| Vendor only | 4 (`289926686`, `289927106`, `289927845`, `289928292`), all vendor date 1 Sep |
| Pairs whose calendar dates differ, still settled | 13. Each was ordered between 11:05 PM and 11:58 PM and delivered just after midnight. Amount and fees agree |
| Amount breaks | 1, order `290238794`, total 41 vs 45, fee 1.95 vs 2.15, vendor due 49.75 |
| Fee breaks where the amounts agree | 0 |
| Delivery-split failures | 0 |
| Settled orders | 297 |
| Settled due | **20,748.07** |
| Compensation on the portal, not deducted | 17 orders, 776 |
| Max fee gap on a settled order | 0.005 |
| Max amount gap on a settled order | under 0.01 |

The hand-built sheet's 21,088.33 is the vendor due of **every** vendor row, including the
four vendor-only orders and the 4 SAR break. It is not the settled payable. The 13
midnight pairs are inside the 20,748.07.

---

## 10. Decisions

Locked from the answers on the sample, plus one call that was left to us.

1. **No Final Summary.** Two uploads cannot reconstruct opening balances or payments already made.
2. **Match on order id, not on date.** `ANALYSIS & RECON` checks `Partner Order ID = order_id`
   and does not compare dates. An order that is only on one file is an error and stays out of
   the payable. A pair whose dates fall on different calendar days still settles.
3. **Delivered only.** Other statuses are listed and skipped.
4. **A break is held, not paid.** Order `290238794` stays visible and its 49.75 stays out of
   the settled due. Settling on the vendor figure would bury a 4 SAR overpay.
5. **We settle vendor fees**, and due is `vendor total + SG delivery charge − vendor fees`.
   Fee tolerance ±0.01, so the 0.005 rounding rows are clean.
6. **Compensation is reported and not subtracted.**
7. **The three `Customer` columns are ignored.** Header text is found wherever the row sits,
   so the vendor title row does not matter.

Thirteen paired orders in the sample were placed late at night and delivered just after
midnight, so the two date columns disagree. They match on id, amount, and fees, and they
settle. Their vendor due, 910.64, is inside the 20,748.07.
