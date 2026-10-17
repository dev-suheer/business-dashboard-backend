"""eShop readers: portal export, vendor ledger, promo catalogue, settlements.

Each reader streams its sheet and yields canonical models. Nothing here holds a
whole sheet in memory, and nothing here maps a column by position.
"""

import re
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

from openpyxl.workbook import Workbook

from app.recon.cleaning import clean_text, money, to_date, to_decimal
from app.recon.excel import (
    ColumnSpec,
    SheetFormatError,
    SheetSchema,
    iter_records,
)
from app.recon.models import (
    LedgerDocType,
    LedgerEntry,
    OrderStatus,
    PortalLine,
    PromoSku,
    Settlement,
)

# plan.md 3.6. Matched against the *description*, never the reference column,
# which the vendor's system truncates to ten characters.
ORDER_RE = re.compile(r"SG-SH-\d+")


PORTAL_SCHEMA = SheetSchema(
    name="portal export",
    fingerprint=("Order Number", "GMV", "SG Commission", "Order Status"),
    # The legacy recon tab bolts the promo catalogue onto a subset of the portal
    # columns; this header appears only there.
    exclude=("MATCH SKU's",),
    columns=(
        ColumnSpec("order_no", ("Order Number",), required=True),
        ColumnSpec("order_date", ("Date",), required=True),
        ColumnSpec("merchant_id", ("Merchant Id",)),
        ColumnSpec("merchant_name", ("Merchant Name",)),
        ColumnSpec("sku", ("SG SKU",)),
        ColumnSpec("merchant_sku", ("Merchant SKU",)),
        ColumnSpec("brand", ("Brand",)),
        ColumnSpec("item_title", ("Item Title",)),
        ColumnSpec("gmv", ("GMV",), required=True),
        ColumnSpec("units", ("Units",)),
        ColumnSpec("coupon_name", ("Coupon Name",)),
        ColumnSpec("coupon_value", ("Coupon Value",)),
        ColumnSpec("actual_commission", ("SG Commission",), required=True),
        ColumnSpec("status", ("Order Status",), required=True),
        # Prior-cycle settlement. Present in the sample export, and the reason a
        # separate settlement upload is a fallback rather than a precondition.
        ColumnSpec("amount_paid", ("Paid",)),
    ),
)

LEDGER_SCHEMA = SheetSchema(
    name="vendor statement",
    fingerprint=("دائن", "مدين", "البيان", "نوع المستند"),
    columns=(
        ColumnSpec("balance", ("الرصيد الحالي",)),
        ColumnSpec("credit", ("دائن",), required=True),
        ColumnSpec("debit", ("مدين",), required=True),
        ColumnSpec("raw_ref", ("رقم المرجع",)),
        ColumnSpec("raw_desc", ("البيان",), required=True),
        # Only the vendor template carries this. The converter's statement has
        # no order-number column, so the description stays the fallback.
        ColumnSpec("order_no", ("رقم الطلب", "Order Number")),
        ColumnSpec("doc_no", ("رقم المستند",)),
        ColumnSpec("doc_type", ("نوع المستند",), required=True),
        ColumnSpec("doc_date", ("التاريخ",), required=True),
    ),
)

PROMO_SCHEMA = SheetSchema(
    name="discount base sheet",
    fingerprint=("رمز المنتج", "سعر العرض"),
    # Disqualifies the legacy recon tab, which repeats these promo columns
    # alongside the portal ones.
    exclude=("Order Number", "GMV"),
    columns=(
        ColumnSpec("sku", ("رمز المنتج",), required=True),
        ColumnSpec("offer_price", ("سعر العرض",)),
        ColumnSpec("merchant_sku", ("رمز البائع",)),
    ),
)

SETTLEMENT_SCHEMA = SheetSchema(
    name="settlement history",
    fingerprint=("order_no",),
    columns=(
        ColumnSpec("order_no", ("order_no", "Order Number", "order no"), required=True),
        ColumnSpec(
            "amount_paid", ("amount_paid", "amount paid", "Paid"), required=True
        ),
        ColumnSpec("settled_on", ("settled_on", "settled on", "Date")),
        ColumnSpec("cycle_ref", ("cycle_ref", "cycle ref")),
    ),
)


def extract_order_no(raw: Any) -> str | None:
    """Pull ``SG-SH-#####`` out of a ledger description.

    Copes with the bidirectional wrapping the converter adds
    (``‫-SG-SH-01397# طلب‬``) and with names prefixed to the number
    (``Turki Aldhry- SG-SH-01990``), because the text is stripped of format
    characters before the regex sees it.
    """
    text = clean_text(raw)
    if text is None:
        return None
    match = ORDER_RE.search(text.upper())
    return match.group(0) if match else None


def classify_doc_type(raw: Any) -> LedgerDocType:
    """Type a ledger row from its Arabic document-type text.

    Matched on distinctive substrings rather than whole strings: the return
    invoice type contains the sales invoice type, so the return test must come
    first, and orthographic variants of ``أجل`` must not change the outcome.
    English keywords are checked in the same order, for a template row whose
    document type was pasted or typed in English rather than picked.
    """
    text = clean_text(raw)
    if text is None:
        return LedgerDocType.UNKNOWN
    if "مردود" in text:
        return LedgerDocType.SALES_RETURN
    if "فاتورة" in text:
        return LedgerDocType.SALES_INVOICE
    if "قيد" in text:
        return LedgerDocType.JOURNAL_ENTRY
    english = text.casefold()
    if "return" in english or "credit note" in english:
        return LedgerDocType.SALES_RETURN
    if "invoice" in english:
        return LedgerDocType.SALES_INVOICE
    if any(word in english for word in ("journal", "commission", "payment")):
        return LedgerDocType.JOURNAL_ENTRY
    return LedgerDocType.UNKNOWN


def _parse_status(raw: Any) -> OrderStatus | None:
    text = clean_text(raw)
    if text is None:
        return None
    wanted = text.casefold().replace(" ", "")
    for status in OrderStatus:
        if status.value.casefold().replace(" ", "") == wanted:
            return status
    return None


def _money_or_none(value: Decimal | None) -> Decimal | None:
    return money(value) if value is not None else None


def _parse_units(raw: Any) -> int:
    units = to_decimal(raw)
    return int(units) if units is not None else 0


def read_portal(
    workbook: Workbook, sheet: str, *, max_rows: int
) -> Iterator[PortalLine]:
    """Stream the portal export.

    Rows without an order number are skipped rather than ending the read: the
    source sheet leaves blank rows before a trailing summary block that restates
    the adjustments in unrelated columns.
    """
    for record in iter_records(workbook[sheet], PORTAL_SCHEMA, max_rows=max_rows):
        order_no = clean_text(record["order_no"])
        if order_no is None:
            continue
        order_date = to_date(record["order_date"])
        gmv = to_decimal(record["gmv"])
        status = _parse_status(record["status"])
        if order_date is None or gmv is None or status is None:
            raise SheetFormatError(
                f"Portal row for order {order_no} is missing a usable "
                "date, GMV or status"
            )
        yield PortalLine(
            order_no=order_no.upper(),
            sku=clean_text(record.get("sku")),
            merchant_sku=clean_text(record.get("merchant_sku")),
            merchant_id=clean_text(record.get("merchant_id")),
            order_date=order_date,
            gmv=gmv,
            units=_parse_units(record.get("units")),
            actual_commission=to_decimal(record["actual_commission"]) or Decimal(0),
            coupon_name=clean_text(record.get("coupon_name")),
            coupon_value=to_decimal(record.get("coupon_value")),
            status=status,
            brand=clean_text(record.get("brand")),
            item_title=clean_text(record.get("item_title")),
            amount_paid=to_decimal(record.get("amount_paid")),
        )


def read_ledger(
    workbook: Workbook, sheets: list[str], *, max_rows: int
) -> Iterator[LedgerEntry]:
    """Stream every ledger-shaped sheet, deduplicating as it goes.

    The converter emits overlapping copies of the same transactions across
    several sheets. Deduplicating on ``(doc_no, doc_type, doc_date, debit,
    credit)`` is not optional — without it the sample file double-counts around
    28 transactions. The key set is bounded by the number of *distinct*
    transactions, so this stays constant-memory in the way that matters.
    The order number is part of the key too, so two template rows that differ
    only by order are never collapsed when the vendor leaves ``doc_no`` blank.
    """
    seen: set[tuple[str, str, str, str, str, str]] = set()
    for sheet in sheets:
        for record in iter_records(workbook[sheet], LEDGER_SCHEMA, max_rows=max_rows):
            doc_type = classify_doc_type(record["doc_type"])
            doc_date = to_date(record["doc_date"])
            # Quantized on the way in: one sheet writes 82.00 as text and another
            # as the number 82, and both must be the same amount everywhere.
            debit = _money_or_none(to_decimal(record["debit"]))
            credit = _money_or_none(to_decimal(record["credit"]))
            if doc_type is LedgerDocType.UNKNOWN and doc_date is None:
                # A page header, a subtotal, or a blank spacer row.
                continue
            entry = LedgerEntry(
                doc_no=clean_text(record.get("doc_no")),
                doc_type=doc_type,
                doc_date=doc_date,
                debit=debit,
                credit=credit,
                balance=_money_or_none(to_decimal(record.get("balance"))),
                raw_ref=clean_text(record.get("raw_ref")),
                raw_desc=clean_text(record["raw_desc"]),
                # The template's own column wins; the converter's statement has
                # none, so its order number still comes out of the description.
                order_no=extract_order_no(record.get("order_no"))
                or extract_order_no(record["raw_desc"]),
                sheet=sheet,
            )
            key = entry.dedup_key()
            if key in seen:
                continue
            seen.add(key)
            yield entry


def read_promo(workbook: Workbook, sheet: str, *, max_rows: int) -> Iterator[PromoSku]:
    """Stream the promo catalogue.

    Only ``رمز المنتج`` is load-bearing. ``سعر العرض`` is carried for reference
    and must never become the commission base.
    """
    for record in iter_records(workbook[sheet], PROMO_SCHEMA, max_rows=max_rows):
        sku = clean_text(record["sku"])
        if sku is None:
            continue
        yield PromoSku(
            sku=sku,
            offer_price=to_decimal(record.get("offer_price")),
            merchant_sku=clean_text(record.get("merchant_sku")),
        )


def read_settlements(
    workbook: Workbook, sheet: str, *, max_rows: int
) -> Iterator[Settlement]:
    """Stream prior settlement history (plan.md section 2.4)."""
    for record in iter_records(workbook[sheet], SETTLEMENT_SCHEMA, max_rows=max_rows):
        order_no = clean_text(record["order_no"])
        amount_paid = to_decimal(record["amount_paid"])
        if order_no is None or amount_paid is None:
            continue
        yield Settlement(
            order_no=order_no.upper(),
            amount_paid=amount_paid,
            settled_on=to_date(record.get("settled_on")),
            cycle_ref=clean_text(record.get("cycle_ref")),
        )
