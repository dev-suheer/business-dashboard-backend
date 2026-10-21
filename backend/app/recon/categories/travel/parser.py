"""Travel readers: portal export, vendor claim, claim ledger, wallet history.

Each reader streams its sheet and yields canonical models. Nothing here holds a
whole sheet in memory, and nothing here maps a column by position — the claim
files move their columns every week.
"""

import re
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from openpyxl.workbook import Workbook

from app.recon.categories.travel.models import (
    BillingType,
    BookingStatus,
    ClaimPeriodTotals,
    PaymentStatus,
    TravelAdjustment,
    TravelInvoiceLine,
    TravelPortalRow,
    TravelSkippedRow,
    Vertical,
)
from app.recon.cleaning import clean_text, to_date, to_decimal
from app.recon.excel import ColumnSpec, SheetSchema, iter_records
from app.recon.wallet import WALLET_SCHEMA as WALLET_SCHEMA
from app.recon.wallet import WalletEvent
from app.recon.wallet import read_wallet as read_category_wallet

# A booking id inside free text, for the claim's adjustment rows.
ADJUSTMENT_BOOKING_RE = re.compile(r"[AH]\d{12,18}")
# The date a booking id carries in its own digits: A260726150195500 -> 2026-07-26.
BOOKING_DATE_RE = re.compile(r"^[AH](\d{6})")
# The trailing "-1" the portal adds when one booking arrives as two rows.
PORTAL_SUFFIX_RE = re.compile(r"^(?P<id>[A-Za-z]\d+)-(?P<suffix>\d+)$")
REVERSAL_REF_RE = re.compile(r"_R\d+$")

VendorRow = TravelInvoiceLine | TravelAdjustment | TravelSkippedRow


PORTAL_SCHEMA = SheetSchema(
    name="travel portal export",
    fingerprint=(
        "SG Order ID",
        "Partner Order ID",
        "Vertical",
        "Order Total",
        "Wallet Amount",
        "Applepay/Card Amount",
        "Commission Charged",
        "Net Payable Amount to Partner",
    ),
    # The portal header is shared with the Food export, and both the client's
    # own analysis tab and ours bolt the vendor columns onto it. None of those
    # are an input, and each carries one of these headers.
    exclude=(
        "Match ID's",
        "Booking ID",
        "Total Invoice Amount",
        "shoppinggate_percentage",
        "MATCH SKU's",
    ),
    columns=(
        ColumnSpec("sg_order_id", ("SG Order ID",), required=True),
        ColumnSpec("booking_id", ("Partner Order ID",), required=True),
        ColumnSpec("vertical", ("Vertical",), required=True),
        ColumnSpec("order_date", ("Order Date",), required=True),
        ColumnSpec("order_time", ("Order Time",)),
        # The export repeats "Customer" three times; the first is the id.
        ColumnSpec("customer_ref", ("Customer",)),
        ColumnSpec("provider", ("Provider Name",)),
        ColumnSpec("transaction_id", ("Transaction ID",)),
        ColumnSpec("status", ("Order Status",), required=True),
        ColumnSpec("payment_status", ("Payment Status",)),
        ColumnSpec("payment_method", ("Payment Method",)),
        ColumnSpec("sub_total", ("Sub Total",)),
        ColumnSpec("promocode", ("Promocode",)),
        ColumnSpec("discount", ("SG Discount/Promo Amount",)),
        ColumnSpec("vat", ("VAT",), required=True),
        ColumnSpec("order_total", ("Order Total",), required=True),
        ColumnSpec("total_amount", ("Total Amount",)),
        ColumnSpec("stated_rate", ("SG Comission %", "SG Commission %")),
        ColumnSpec("commission_amount", ("Commission Amount",)),
        ColumnSpec("partner_payout", ("Partner Payout",)),
        ColumnSpec("commission_charged", ("Commission Charged",), required=True),
        ColumnSpec("wallet_amount", ("Wallet Amount",), required=True),
        ColumnSpec("card_amount", ("Applepay/Card Amount",), required=True),
        ColumnSpec("refund_amount", ("Refund Amount",)),
        ColumnSpec("compensation_amount", ("Compensation Amount",)),
        ColumnSpec("net_payable", ("Net Payable Amount to Partner",), required=True),
    ),
)

VENDOR_SCHEMA = SheetSchema(
    name="vendor claim report",
    # The intersection of all four sample claim files: one adds five hotel
    # columns, one drops Supply Date, one drops Entitlement. The booking id is
    # left out so that a claim heading it "Booking Number" is still found.
    fingerprint=(
        "Invoice Number",
        "line Item",
        "Billing Type",
        "Service Type",
        "Total Price W.O VAT",
        "VAT %",
        "Airline Can Fee",
        "Total Invoice Amount",
        "commision amount",
        "net payable amount",
    ),
    # The client's analysis tab satisfies the fingerprint too — it bolts these
    # columns onto the portal ones.
    exclude=("SG Order ID", "Order Total", "Match ID's"),
    columns=(
        ColumnSpec(
            "booking_ref",
            ("Booking ID", "Booking Number", "Booking No"),
            required=True,
        ),
        ColumnSpec("customer_id", ("Customer ID",)),
        ColumnSpec("dom_int_label", ("DOM/INT",)),
        ColumnSpec("customer_name", ("Customer Name",)),
        ColumnSpec("invoice_no", ("Invoice Number",), required=True),
        ColumnSpec("line_item", ("line Item",)),
        ColumnSpec("billing_type", ("Billing Type",)),
        ColumnSpec("issue_date", ("Issue Date",)),
        ColumnSpec("service_type", ("Service Type",)),
        ColumnSpec("service_provider", ("Service Provider",)),
        ColumnSpec("description", ("Description of Service",)),
        ColumnSpec("supplier", ("Supplier Name",)),
        ColumnSpec("pnr", ("PNR No",)),
        ColumnSpec("vendor_booking_date", ("Booking Date",)),
        ColumnSpec("check_in", ("Check In Date",)),
        ColumnSpec("check_out", ("Check Out Date",)),
        ColumnSpec("to_number", ("T/O Number",)),
        ColumnSpec("client_name", ("Client Name",)),
        ColumnSpec("route", ("Route",)),
        ColumnSpec("destination_city", ("Destination City",)),
        ColumnSpec("class_type", ("Class/Type",)),
        ColumnSpec("base_ex_vat", ("Total Price W.O VAT",), required=True),
        ColumnSpec("vat_percent", ("VAT %",), required=True),
        ColumnSpec("vat", ("VAT",)),
        ColumnSpec("airline_cancel_fee", ("Airline Can Fee",)),
        ColumnSpec("total_invoice", ("Total Invoice Amount",), required=True),
        ColumnSpec("commission", ("commision amount", "commission amount")),
        ColumnSpec("net_payable", ("net payable amount",)),
        ColumnSpec("currency", ("Currency",)),
    ),
)

CLAIM_SCHEMA = SheetSchema(
    name="claim period ledger",
    fingerprint=("Claim period", "total Payable amount", "DIFF in payment"),
    columns=(
        ColumnSpec("claim_period", ("Claim period",), required=True),
        ColumnSpec("total_claim", ("Total Claim", "total Claim")),
        ColumnSpec(
            "commission_amount",
            ("Commision Amount", "Commision invoices Amount", "Commission Amount"),
        ),
        ColumnSpec("total_payable", ("total Payable amount",), required=True),
        ColumnSpec("paid_amount", ("Paid Amount",)),
        ColumnSpec("difference", ("DIFF in payment",)),
        ColumnSpec(
            "unprovided_commission",
            ("unprovided Commision invoices", "unprovided Commission invoices"),
        ),
    ),
)


def portal_booking_key(raw: Any) -> tuple[str, int | None] | None:
    """The booking id a portal row points at, and its ``-N`` leg if it has one.

    Unbooked rows carry ``-``, which ``clean_text`` already reads as no value.
    """
    text = clean_text(raw)
    if text is None:
        return None
    text = text.upper()
    match = PORTAL_SUFFIX_RE.match(text)
    if match:
        return match.group("id"), int(match.group("suffix"))
    return text, None


def vendor_booking_key(raw: Any) -> tuple[str, str, bool] | None:
    """Split a claim's ``Booking ID`` into (group key, full reference, reversal).

    ``A260726150195500_7889547`` groups under ``A260726150195500``; a refund's
    ``…_257964_R0`` groups under the same booking, which is exactly what lets a
    credit note net against the invoice it reverses.
    """
    text = clean_text(raw)
    if text is None:
        return None
    text = text.upper()
    return text.split("_", 1)[0], text, bool(REVERSAL_REF_RE.search(text))


def booking_date(booking_id: str) -> date | None:
    """The date a booking id carries in its own digits.

    Verified against every invoice row of the sample claims. It is the only
    reliable date for a credit note, whose ``Booking Date`` column holds the
    date of the credit rather than of the booking.
    """
    match = BOOKING_DATE_RE.match(booking_id)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), "%y%m%d").date()
    except ValueError:
        return None


def vertical_from_id(booking_id: str) -> Vertical | None:
    if booking_id.startswith("H"):
        return Vertical.HOTEL
    if booking_id.startswith("A"):
        return Vertical.FLIGHT
    return None


def vertical_from_text(raw: Any) -> Vertical | None:
    """Read a vertical from the portal's ``Vertical`` or the claim's ``Service Type``."""
    text = clean_text(raw)
    if text is None:
        return None
    wanted = text.casefold()
    if wanted in {"hotel", "hotels", "h"}:
        return Vertical.HOTEL
    if wanted in {"flight", "flights", "a"}:
        return Vertical.FLIGHT
    return None


def parse_billing_type(raw: Any) -> BillingType:
    text = clean_text(raw)
    if text is None:
        return BillingType.UNKNOWN
    wanted = text.casefold()
    for billing in (BillingType.INVOICE, BillingType.CREDIT_FOR_RETURNS):
        if billing.value.casefold() == wanted:
            return billing
    if "credit" in wanted:
        return BillingType.CREDIT_FOR_RETURNS
    return BillingType.UNKNOWN


def _enum_or_unknown[EnumT: (BookingStatus, PaymentStatus)](
    raw: Any, members: tuple[EnumT, ...], fallback: EnumT
) -> EnumT:
    text = clean_text(raw)
    if text is None:
        return fallback
    wanted = text.casefold().replace(" ", "")
    for member in members:
        if member.value.casefold().replace(" ", "") == wanted:
            return member
    return fallback


def _amount(raw: Any) -> Decimal:
    """A money cell, with ``-`` and blanks read as zero."""
    return to_decimal(raw) or Decimal(0)


def read_portal(
    workbook: Workbook, sheet: str, *, source: str, max_rows: int
) -> Iterator[TravelPortalRow]:
    """Stream one travel sheet of the portal export.

    Rows of another vertical are skipped rather than rejected: the same export
    format carries Food, and a workbook may hold several tabs.
    """
    for index, record in enumerate(
        iter_records(workbook[sheet], PORTAL_SCHEMA, max_rows=max_rows), start=1
    ):
        vertical = vertical_from_text(record["vertical"])
        order_date = to_date(record["order_date"])
        if vertical is None or order_date is None:
            continue
        key = portal_booking_key(record["booking_id"])
        order_total = _amount(record["order_total"])
        total_amount = to_decimal(record.get("total_amount"))
        yield TravelPortalRow(
            source=source,
            sheet=sheet,
            row=index,
            sg_order_id=clean_text(record["sg_order_id"]),
            booking_id=key[0] if key else None,
            booking_suffix=key[1] if key else None,
            vertical=vertical,
            order_date=order_date,
            order_time=clean_text(record.get("order_time")),
            customer_ref=clean_text(record.get("customer_ref")),
            provider=clean_text(record.get("provider")),
            transaction_id=clean_text(record.get("transaction_id")),
            status=_enum_or_unknown(
                record["status"],
                (
                    BookingStatus.PROCESSED,
                    BookingStatus.CANCELLED,
                    BookingStatus.EXPIRED,
                    BookingStatus.PENDING,
                ),
                BookingStatus.UNKNOWN,
            ),
            payment_status=_enum_or_unknown(
                record.get("payment_status"),
                (
                    PaymentStatus.PAID,
                    PaymentStatus.COMPLETED,
                    PaymentStatus.PENDING,
                    PaymentStatus.FAILED,
                    PaymentStatus.REFUNDED,
                ),
                PaymentStatus.UNKNOWN,
            ),
            payment_method=clean_text(record.get("payment_method")),
            sub_total=_amount(record.get("sub_total")),
            promocode=clean_text(record.get("promocode")),
            discount=_amount(record.get("discount")),
            vat=_amount(record["vat"]),
            order_total=order_total,
            total_amount=total_amount if total_amount is not None else order_total,
            commission_amount=_amount(record.get("commission_amount")),
            partner_payout=_amount(record.get("partner_payout")),
            commission_charged=_amount(record["commission_charged"]),
            net_payable=_amount(record["net_payable"]),
            wallet_amount=_amount(record["wallet_amount"]),
            card_amount=_amount(record["card_amount"]),
            refund_amount=_amount(record.get("refund_amount")),
            compensation_amount=_amount(record.get("compensation_amount")),
            stated_rate=to_decimal(record.get("stated_rate")),
        )


def read_vendor(
    workbook: Workbook, sheets: list[str], *, source: str, max_rows: int
) -> Iterator[VendorRow]:
    """Stream the claim's data sheets, classifying every row it meets.

    Three shapes share the sheet: invoice lines, the free-text adjustment rows
    the vendor uses to correct an earlier claim, and trailing totals rows. The
    last are dropped silently; anything invoiced but unidentifiable comes back
    as a skip so the pipeline can report it rather than lose it.
    """
    for sheet in sheets:
        for index, record in enumerate(
            iter_records(workbook[sheet], VENDOR_SCHEMA, max_rows=max_rows), start=1
        ):
            key = vendor_booking_key(record["booking_ref"])
            if key is None:
                yield _classify_unbooked(record, source=source, sheet=sheet, row=index)
                continue
            booking_id, booking_ref, is_reversal = key
            yield TravelInvoiceLine(
                source=source,
                sheet=sheet,
                row=index,
                booking_id=booking_id,
                booking_ref=booking_ref,
                is_reversal_ref=is_reversal,
                invoice_no=clean_text(record["invoice_no"]),
                line_item=clean_text(record.get("line_item")),
                billing_type=parse_billing_type(record.get("billing_type")),
                service_type=clean_text(record.get("service_type")),
                issue_date=to_date(record.get("issue_date")),
                vendor_booking_date=to_date(record.get("vendor_booking_date")),
                supplier=clean_text(record.get("supplier")),
                service_provider=clean_text(record.get("service_provider")),
                description=clean_text(record.get("description")),
                pnr=clean_text(record.get("pnr")),
                to_number=clean_text(record.get("to_number")),
                client_name=clean_text(record.get("client_name")),
                route=clean_text(record.get("route")),
                destination_city=clean_text(record.get("destination_city")),
                class_type=clean_text(record.get("class_type")),
                check_in_text=clean_text(record.get("check_in")),
                check_out_text=clean_text(record.get("check_out")),
                dom_int_label=clean_text(record.get("dom_int_label")),
                base_ex_vat=_amount(record["base_ex_vat"]),
                vat_percent=_amount(record["vat_percent"]),
                vat=_amount(record.get("vat")),
                airline_cancel_fee=_amount(record.get("airline_cancel_fee")),
                total_invoice=_amount(record["total_invoice"]),
                commission=_amount(record.get("commission")),
                net_payable=_amount(record.get("net_payable")),
                currency=clean_text(record.get("currency")),
            )


def _classify_unbooked(
    record: dict[str, Any], *, source: str, sheet: str, row: int
) -> VendorRow:
    """A claim row with no booking id: an adjustment, a total, or a problem."""
    text = clean_text(record.get("customer_id"))
    invoice_no = clean_text(record["invoice_no"])
    commission = _amount(record.get("commission"))
    net_payable = _amount(record.get("net_payable"))

    if text is not None and not text.isdigit():
        match = ADJUSTMENT_BOOKING_RE.search(text.upper())
        return TravelAdjustment(
            source=source,
            sheet=sheet,
            row=row,
            text=text,
            booking_id=match.group(0) if match else None,
            commission=commission,
            net_payable=net_payable,
        )
    if invoice_no is not None:
        return TravelSkippedRow(
            source=source,
            sheet=sheet,
            row=row,
            reason="invoiced row carries no booking id",
            text=invoice_no,
            invoice_no=invoice_no,
            total_invoice=_amount(record.get("total_invoice")),
            commission=commission,
            net_payable=net_payable,
        )
    # A trailing totals row: no identity of any kind, only sums.
    return TravelSkippedRow(
        source=source, sheet=sheet, row=row, reason="totals or spacer row"
    )


def read_claim_periods(
    workbook: Workbook, sheet: str, *, source: str, max_rows: int
) -> Iterator[ClaimPeriodTotals]:
    """Stream the claim workbook's own per-period ledger."""
    for record in iter_records(workbook[sheet], CLAIM_SCHEMA, max_rows=max_rows):
        claim_period = clean_text(record["claim_period"])
        if claim_period is None:
            # The block ends with an unlabelled totals row.
            continue
        yield ClaimPeriodTotals(
            source=source,
            claim_period=claim_period,
            total_claim=to_decimal(record.get("total_claim")),
            commission_amount=to_decimal(record.get("commission_amount")),
            total_payable=to_decimal(record["total_payable"]),
            paid_amount=to_decimal(record.get("paid_amount")),
            difference=to_decimal(record.get("difference")),
            unprovided_commission=to_decimal(record.get("unprovided_commission")),
        )


def read_wallet(
    workbook: Workbook, sheet: str, *, source: str, max_rows: int
) -> Iterator[WalletEvent]:
    """Stream the wallet history, keeping only travel movements.

    Eleven of the file's fifteen wallet types belong to other categories; they
    are skipped here so nothing downstream has to know they exist.
    """
    return read_category_wallet(
        workbook,
        sheet,
        source=source,
        max_rows=max_rows,
        wallet_type=vertical_from_text,
    )
