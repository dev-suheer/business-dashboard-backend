"""The travel Excel report (plan-travel.md section 8).

The client signed off on the food workbook, and travel follows it: Dashboard,
Orders, Exceptions, Wallet when a wallet history was uploaded, B2C Invoices
ERP, copies of the Portal and Vendor uploads, and Finance Report when that
file was uploaded.

The Orders sheet follows the client's own ``Analysis`` tab: the portal
columns, the rate checks, then the claim columns, one row per matched booking.
Their totals sit beneath, with the SG / vendor / difference block of the
client's ``Sheet1``. Unmatched bookings are listed under that, out of the
totals, each with the reason it did not match. Only matched bookings feed a
total, the same as the JSON summary.

When a finance report is uploaded, each booking row looks its SG Order ID up
in that report's Order ID, in five cashback columns after Verdict.

Written with ``constant_memory``: rows go out strictly top to bottom.
"""

from collections.abc import Callable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import xlsxwriter

from app.recon.categories.travel import parser
from app.recon.categories.travel.models import (
    BookingRecon,
    TravelInvoiceLine,
    TravelResult,
    UnmatchedReason,
    Vertical,
)
from app.recon.cleaning import money
from app.recon.exceptions import ReconException
from app.recon.finance import (
    CASHBACK_COLUMNS,
    FinanceLayout,
    finance_layout,
    finance_sheet,
    write_cashback,
)
from app.recon.sheets import (
    PORTAL_TITLE,
    VENDOR_TITLE,
    BreakdownEntry,
    CashbackLine,
    Column,
    DashBlock,
    cashback_sheet,
    exceptions_sheet,
    letter,
    portal_records,
    ranked,
    report_formats,
    share,
    source_sheets,
    wallet_sheet,
    write_column_headers,
    write_dashboard,
    write_order_row,
    write_sum_row,
)
from app.recon.xlsx import num

REASON_LABELS: dict[UnmatchedReason, str] = {
    UnmatchedReason.REFUNDED: "Refunded",
    UnmatchedReason.CANCELLED: "Cancelled",
    UnmatchedReason.MISSING_IN_VENDOR: "Missing from vendor claim",
    UnmatchedReason.MISSING_IN_PORTAL: "Missing from portal",
    UnmatchedReason.OTHER: "Other",
}

# The portal's own wording for each vertical.
_VERTICAL_LABELS = {Vertical.HOTEL: "Hotel", Vertical.FLIGHT: "Flights"}

_PORTAL_COLUMNS: tuple[Column, ...] = (
    ("order_date", "Order Date", 14, "portal", "date"),
    ("order_time", "Order Time", 12, "portal", "text"),
    ("sg_order_id", "SG Order ID", 14, "portal", "text"),
    ("booking_id", "Partner Order ID", 20, "portal", "text"),
    ("vertical", "Vertical", 10, "portal", "text"),
    ("provider", "Provider Name", 16, "portal", "text"),
    ("transaction_id", "Transaction ID", 28, "portal", "text"),
    ("order_status", "Order Status", 14, "portal", "text"),
    ("payment_status", "Payment Status", 14, "portal", "text"),
    ("payment_method", "Payment Method", 20, "portal", "text"),
    ("sub_total", "Sub Total", 12, "portal", "money"),
    ("promocode", "Promocode", 12, "portal", "text"),
    ("discount", "SG Discount/Promo Amount", 16, "portal", "money"),
    ("vat", "VAT", 12, "portal", "money"),
    ("order_total", "Order Total", 12, "portal", "money"),
    ("total_amount", "Total Amount", 14, "portal", "money"),
    ("stated_rate", "SG Comission %", 14, "portal", "rate"),
    ("commission_amount", "Commission Amount", 16, "portal", "money"),
    ("partner_payout", "Partner Payout", 14, "portal", "money"),
    ("commission_charged", "Commission Charged", 16, "portal", "money"),
    ("wallet_amount", "Wallet Amount", 14, "portal", "money"),
    ("card_amount", "Applepay/Card Amount", 18, "portal", "money"),
    ("refund_amount", "Refund Amount", 14, "portal", "money"),
    ("compensation_amount", "Compensation Amount", 16, "portal", "money"),
    ("net_payable", "Net Payable Amount to Partner", 18, "portal", "money"),
)
# The client's six computed columns, then the base difference, what is left
# of the net payable once both sides use the contract rate, and the VAT uplift.
_CHECK_COLUMNS: tuple[Column, ...] = (
    ("match", "MATCH ID'S", 12, "check", "text"),
    ("sg_rate", "Com Rate Check", 12, "check", "rate"),
    ("vendor_rate", "Vendor Rate Check", 12, "check", "rate"),
    ("rate_diff", "Difference", 12, "check", "rate"),
    ("contract_rate", "Contract Rate", 12, "check", "rate"),
    ("as_vendor", "Actual As Vendor", 14, "check", "money"),
    ("net_as_vendor", "Net Payable", 14, "check", "money"),
    ("base_diff", "BASE DIFFERENCE", 14, "check", "diff"),
    ("net_diff", "NET PAYABLE DIFFERENCE", 16, "check", "diff"),
    ("uplift", "VAT UPLIFT NOT CHARGED", 16, "check", "money"),
)
_VENDOR_COLUMNS: tuple[Column, ...] = (
    ("dom_int", "DOM/INT", 10, "vendor", "text"),
    ("invoice", "Invoice Number", 16, "vendor", "text"),
    ("lines", "Claim Lines", 10, "vendor", "text"),
    ("billing", "Billing Type", 14, "vendor", "text"),
    ("issued", "Issue Date", 12, "vendor", "date"),
    ("service", "Service Type", 10, "vendor", "text"),
    ("service_provider", "Service Provider", 18, "vendor", "text"),
    ("description", "Description of Service", 22, "vendor", "text"),
    ("supplier", "Supplier Name", 16, "vendor", "text"),
    ("pnr", "PNR No", 12, "vendor", "text"),
    ("booked", "Booking Date", 12, "vendor", "date"),
    ("check_in", "Check In Date", 12, "vendor", "text"),
    ("check_out", "Check Out Date", 12, "vendor", "text"),
    ("to_number", "T/O Number", 14, "vendor", "text"),
    ("client", "Client Name", 24, "vendor", "text"),
    ("route", "Route", 18, "vendor", "text"),
    ("city", "Destination City", 16, "vendor", "text"),
    ("class_type", "Class/Type", 20, "vendor", "text"),
    ("base_ex_vat", "Total Price W.O VAT", 16, "vendor", "money"),
    ("vat_percent", "VAT %", 8, "vendor", "text"),
    ("line_vat", "VAT", 12, "vendor", "money"),
    ("cancel_fee", "Airline Can Fee", 14, "vendor", "money"),
    ("total_invoice", "Total Invoice Amount", 16, "vendor", "money"),
    ("v_commission", "commision amount", 16, "vendor", "money"),
    ("v_net", "net payable amount", 16, "vendor", "money"),
    ("currency", "Currency", 10, "vendor", "text"),
    ("booking_ref", "Booking ID", 26, "vendor", "text"),
)
_COLUMNS: tuple[Column, ...] = (
    *_PORTAL_COLUMNS,
    *_CHECK_COLUMNS,
    *_VENDOR_COLUMNS,
    ("verdict", "Verdict", 24, "meta", "text"),
)
_COL = {spec[0]: index for index, spec in enumerate(_COLUMNS)}


def build_report(
    result: TravelResult,
    path: Path,
    sources: dict[str, Path] | None = None,
) -> Path:
    """Write the report. ``sources`` maps ``portal`` and ``vendor`` to the uploads.

    B2C Invoices ERP and the Portal and Vendor copies are appended only when
    the paths are supplied, which the download route always does. B2C invoices
    are the matched bookings, and their unit price reads the Orders sheet. A
    ``finance`` path adds the cashback columns and a copy of that report.
    """
    finance = finance_layout(sources.get("finance")) if sources else None
    book = xlsxwriter.Workbook(
        str(path),
        {
            "constant_memory": True,
            "in_memory": False,
            "default_date_format": "yyyy-mm-dd",
        },
    )
    try:
        fmt = report_formats(book)
        lines = _lines_by_booking(result)
        _dashboard_sheet(book, fmt, result, lines)
        _orders_sheet(book, fmt, result, lines, finance)
        _exceptions_sheet(book, fmt, result)
        if result.wallet is not None:
            wallet_sheet(book, fmt, result.wallet)
        if sources:
            _b2c_invoices_sheet(book, fmt, result, sources)
            source_sheets(
                book,
                fmt,
                result.detected,
                sources,
                (
                    ("portal", PORTAL_TITLE, parser.PORTAL_SCHEMA, "portal_header"),
                    ("vendor", VENDOR_TITLE, parser.VENDOR_SCHEMA, "vendor_header"),
                ),
            )
            if finance is not None:
                finance_sheet(book, fmt, sources, finance)
    finally:
        book.close()
    return path


def _matched(result: TravelResult) -> list[BookingRecon]:
    return [b for b in result.bookings if b.unmatched_reason is None]


def _lines_by_booking(result: TravelResult) -> dict[str, list[TravelInvoiceLine]]:
    lines: dict[str, list[TravelInvoiceLine]] = {}
    for line in result.invoice_lines:
        lines.setdefault(line.booking_id, []).append(line)
    return lines


# --- Dashboard --------------------------------------------------------------

_PRIMARY_HEADERS = (
    "Order",
    "Booking Amount",
    "Vendor Amount",
    "Base Difference",
    "SG Commission",
    "Vendor Commission",
    "Round of differ",
    "Net Payable",
    "Wallet Amount",
    "Applepay/Card Amount",
    "Total collection",
    "Portal total",
    "T. Order - Collection Dif",
    "sg promo code disc",
    "T. Remaining",
)
_COLLECTION_HEADERS = (
    "Total Order Amount",
    "Wallets",
    "Other Method",
    "Collection",
    "Order-Collection",
    "Promo Discount",
    "Net Difference",
)
_COMMISSION_HEADERS = (
    "Hotel",
    "Flights",
    "TOTAL",
    "Excluding VAT",
    "Average Per Order",
)
_SHARE_HEADERS = ("By Hotel", "By Flights")
_BREAKDOWN_HEADERS = (
    "Breakdown By Most Used",
    "Order Count",
    "Discount Count",
    "Revenue By Provider",
    "Applepay",
    "Tamara",
    "Card",
    "Wallet",
    "Commission",
    "Net Payable",
)


def _dashboard_sheet(
    book: Any,
    fmt: dict[str, Any],
    result: TravelResult,
    lines: dict[str, list[TravelInvoiceLine]],
) -> None:
    """The client dashboard, over the matched bookings the totals are taken from."""
    s = result.summary
    matched = _matched(result)
    portals = [b.portal for b in matched if b.portal is not None]
    wallet = money(sum((b.wallet_amount for b in matched), Decimal(0)))
    card = money(sum((b.card_amount for b in matched), Decimal(0)))
    portal_total = money(sum((row.total_amount for row in portals), Decimal(0)))
    promo = money(sum((row.discount for row in portals), Decimal(0)))
    collection = wallet + card
    commission = s.hotel_commission + s.flight_commission
    average = commission / len(matched) if matched else Decimal(0)

    blocks = (
        DashBlock(
            label="SG TRAVEL ORDER Amount",
            value_label="Vendor TRAVEL ORDER Amount",
            headers=_PRIMARY_HEADERS,
            values=(
                (len(matched), "int"),
                (s.portal_base, "money"),
                (s.vendor_base, "money"),
                (s.base_difference, "money"),
                (s.portal_commission, "money"),
                (s.vendor_commission, "money"),
                (s.vendor_commission - s.portal_commission, "money"),
                (s.net_payable_as_vendor, "money"),
                (wallet, "money"),
                (card, "money"),
                (collection, "money"),
                (portal_total, "money"),
                (collection - portal_total, "money"),
                (promo, "money"),
                (promo + collection - portal_total, "money"),
            ),
        ),
        DashBlock(
            label="Cash",
            headers=_COLLECTION_HEADERS,
            values=(
                (portal_total, "money"),
                (wallet, "money"),
                (card, "money"),
                (collection, "money"),
                (portal_total - collection, "money"),
                (promo, "money"),
                (portal_total - collection - promo, "money"),
            ),
        ),
        DashBlock(
            label="Commission at the contract rate",
            headers=_COMMISSION_HEADERS,
            values=(
                (s.hotel_commission, "money"),
                (s.flight_commission, "money"),
                (commission, "money"),
                (s.hotel_commission_ex_vat + s.flight_commission_ex_vat, "money"),
                (average, "money"),
            ),
        ),
        DashBlock(
            label="Average Commission share",
            headers=_SHARE_HEADERS,
            values=(
                (share(s.hotel_commission, commission), "pct"),
                (share(s.flight_commission, commission), "pct"),
            ),
            tall=False,
        ),
    )
    # The TOTAL row is every booking under one name: each counts once, and
    # its money is not split.
    total = next(
        iter(_breakdown(matched, lines, lambda _provider: "TOTAL")),
        BreakdownEntry(name="TOTAL"),
    )
    entries = ranked(_breakdown(matched, lines, lambda provider: provider))
    write_dashboard(book, fmt, blocks, _BREAKDOWN_HEADERS, total, entries)


def _breakdown(
    matched: Sequence[BookingRecon],
    lines: dict[str, list[TravelInvoiceLine]],
    name_of: Callable[[str], str],
) -> list[BreakdownEntry]:
    """One row per claim Service Provider.

    A booking whose lines name several providers is counted under each. Its
    booking-level money is split between them by their share of the claim's
    Total Invoice Amount; commission is the providers' own line commission.
    Each row is quantized once.
    """
    entries: dict[str, BreakdownEntry] = {}
    for booking in matched:
        row = booking.portal
        booking_lines = lines.get(booking.booking_id, [])
        if row is None or not booking_lines:
            continue
        basis: dict[str, Decimal] = {}
        commission: dict[str, Decimal] = {}
        for line in booking_lines:
            name = name_of(line.service_provider or "Unknown")
            basis[name] = basis.get(name, Decimal(0)) + line.total_invoice
            commission[name] = commission.get(name, Decimal(0)) + line.commission
        whole = sum(basis.values(), Decimal(0))
        for name, part in basis.items():
            weight = part / whole if whole else Decimal(1) / len(basis)
            entry = entries.setdefault(name, BreakdownEntry(name=name))
            entry.orders += 1
            if row.discount != 0 or row.promocode:
                entry.discounts += 1
            entry.revenue += row.discount * weight
            entry.add_payment(
                row.payment_method, row.card_amount * weight, row.wallet_amount * weight
            )
            entry.commission += commission[name]
            entry.extra += booking.net_payable_as_vendor * weight
    for entry in entries.values():
        entry.quantize()
    return list(entries.values())


# --- Orders -----------------------------------------------------------------


def _orders_sheet(
    book: Any,
    fmt: dict[str, Any],
    result: TravelResult,
    lines: dict[str, list[TravelInvoiceLine]],
    finance: FinanceLayout | None,
) -> None:
    columns = _COLUMNS + CASHBACK_COLUMNS if finance is not None else _COLUMNS
    sheet = book.add_worksheet("Orders")
    write_column_headers(sheet, fmt, columns)
    tolerances = {
        "base_diff": result.rates.base_tolerance,
        "net_diff": result.rates.base_tolerance,
    }

    matched = _matched(result)
    unmatched = [b for b in result.bookings if b.unmatched_reason is not None]
    for offset, booking in enumerate(matched):
        values = _booking_values(booking, lines.get(booking.booking_id, []), result)
        write_order_row(sheet, fmt, 1 + offset, _COLUMNS, values, tolerances=tolerances)
        _write_cashback(sheet, fmt, 1 + offset, finance)

    first = 2
    last = 1 + len(matched)
    totals = last  # 0-based row of the totals; its Excel row is totals + 1
    row = 2
    if matched:
        sheet.write(totals, 0, f"Total, {len(matched)} matched", fmt["label"])
        write_sum_row(sheet, fmt, totals, first, last, _COLUMNS)
        row = _write_client_summary(sheet, fmt, totals + 2, totals, result) + 2
        sheet.autofilter(0, 0, last - 1, len(columns) - 1)
    if unmatched:
        sheet.write(
            row,
            0,
            "Bookings not in the totals, with the reason they did not match",
            fmt["title"],
        )
        for offset, booking in enumerate(unmatched, start=1):
            values = _booking_values(booking, lines.get(booking.booking_id, []), result)
            write_order_row(
                sheet, fmt, row + offset, _COLUMNS, values, tolerances=tolerances
            )
            _write_cashback(sheet, fmt, row + offset, finance)


def _write_cashback(
    sheet: Any, fmt: dict[str, Any], row: int, finance: FinanceLayout | None
) -> None:
    """The finance lookups after Verdict. A booking with no SG Order ID is NA."""
    if finance is None:
        return
    key = f"{letter(_COL['sg_order_id'])}{row + 1}"
    write_cashback(sheet, fmt, row, len(_COLUMNS), key, finance)


def _write_client_summary(
    sheet: Any, fmt: dict[str, Any], row: int, totals: int, result: TravelResult
) -> int:
    """The client's Sheet1: our figures, theirs, and the gap, then by vertical.

    Returns the last row written.
    """
    excel = totals + 1

    def ref(key: str) -> str:
        return f"{letter(_COL[key])}{excel}"

    vendor = (result.vendor_name or "Vendor").upper()
    sheet.merge_range(row, 0, row, 2, "Matched bookings", fmt["label"])
    for col, title in enumerate(("SHOPPINGGATE", vendor, "DIFFERENCE"), start=3):
        sheet.write(row, col, title, fmt["header"])
    for label, ours, theirs in (
        ("Base Amount", ref("order_total"), ref("total_invoice")),
        ("Commission at the contract rate", ref("as_vendor"), ref("v_commission")),
        ("Net Payable at the contract rate", ref("net_as_vendor"), ref("v_net")),
    ):
        row += 1
        sheet.merge_range(row, 0, row, 2, label, fmt["label"])
        sheet.write_formula(row, 3, f"={ours}", fmt["money"])
        sheet.write_formula(row, 4, f"={theirs}", fmt["money"])
        sheet.write_formula(
            row, 5, f"={letter(3)}{row + 1}-{letter(4)}{row + 1}", fmt["yellow"]
        )
    row += 1
    sheet.merge_range(row, 0, row, 2, "VAT uplift never charged", fmt["label"])
    sheet.write_formula(row, 3, f"={ref('uplift')}", fmt["money"])

    s = result.summary
    row += 2
    sheet.merge_range(row, 0, row, 2, "Commission by vertical", fmt["label"])
    sheet.write(row, 3, "Including VAT", fmt["header"])
    sheet.write(row, 4, "Excluding VAT", fmt["header"])
    for label, gross, net in (
        ("Hotel", s.hotel_commission, s.hotel_commission_ex_vat),
        ("Flights", s.flight_commission, s.flight_commission_ex_vat),
    ):
        row += 1
        sheet.merge_range(row, 0, row, 2, label, fmt["label"])
        sheet.write(row, 3, num(gross), fmt["money"])
        sheet.write(row, 4, num(net), fmt["money"])

    rates = result.rates
    row += 2
    sheet.write(
        row,
        0,
        f"Rates applied: hotel {rates.hotel_rate:.2%}, flights "
        f"{rates.flight_rate:.2%}, plus {rates.vat_rate:.2%} VAT on the commission "
        "of an international booking. Local or international is decided by the "
        "VAT on the claim, never by its DOM/INT label.",
        fmt["info"],
    )
    return row


def _booking_values(
    booking: BookingRecon, lines: list[TravelInvoiceLine], result: TravelResult
) -> dict[str, Any]:
    """One booking's cells by column key. A side it lacks stays blank."""
    values: dict[str, Any] = {
        "booking_id": booking.booking_id,
        "verdict": _verdict(booking, result),
    }
    row = booking.portal
    if row is not None:
        values.update(
            {
                "order_date": row.order_date,
                "order_time": row.order_time,
                "sg_order_id": row.sg_order_id,
                "vertical": _VERTICAL_LABELS[row.vertical],
                "provider": row.provider,
                "transaction_id": row.transaction_id,
                "order_status": row.status.value,
                "payment_status": row.payment_status.value,
                "payment_method": row.payment_method,
                "sub_total": row.sub_total,
                "promocode": row.promocode,
                "discount": row.discount,
                "vat": row.vat,
                "order_total": row.order_total,
                "total_amount": row.total_amount,
                "stated_rate": row.stated_rate,
                "commission_amount": row.commission_amount,
                "partner_payout": row.partner_payout,
                "commission_charged": row.commission_charged,
                "wallet_amount": row.wallet_amount,
                "card_amount": row.card_amount,
                "refund_amount": row.refund_amount,
                "compensation_amount": row.compensation_amount,
                "net_payable": row.net_payable,
            }
        )
    if lines:
        first = lines[0]
        values.update(
            {
                "dom_int": _joined(line.dom_int_label for line in lines),
                "invoice": _joined(line.invoice_no for line in lines),
                "lines": len(lines),
                "billing": _joined(line.billing_type.value for line in lines),
                "issued": first.issue_date,
                "service": _joined(line.service_type for line in lines),
                "service_provider": _joined(line.service_provider for line in lines),
                "description": _joined(line.description for line in lines),
                "supplier": _joined(line.supplier for line in lines),
                "pnr": _joined(line.pnr for line in lines),
                "booked": first.vendor_booking_date,
                "check_in": _joined(line.check_in_text for line in lines),
                "check_out": _joined(line.check_out_text for line in lines),
                "to_number": _joined(line.to_number for line in lines),
                "client": _joined(line.client_name for line in lines),
                "route": _joined(line.route for line in lines),
                "city": _joined(line.destination_city for line in lines),
                "class_type": _joined(line.class_type for line in lines),
                "base_ex_vat": money(_sum(line.base_ex_vat for line in lines)),
                "vat_percent": _joined(_percent(line.vat_percent) for line in lines),
                "line_vat": money(_sum(line.vat for line in lines)),
                "cancel_fee": booking.vendor_cancel_fee,
                "total_invoice": booking.vendor_base,
                "v_commission": booking.vendor_commission,
                "v_net": booking.vendor_net_payable,
                "currency": _joined(line.currency for line in lines),
                "booking_ref": _joined(line.booking_ref for line in lines),
            }
        )
    both = row is not None and bool(lines)
    values["match"] = "TRUE" if both else "FALSE"
    if both:
        rate_diff = (
            booking.sg_rate - booking.vendor_rate
            if booking.sg_rate is not None and booking.vendor_rate is not None
            else None
        )
        values.update(
            {
                "sg_rate": booking.sg_rate,
                "vendor_rate": booking.vendor_rate,
                "rate_diff": rate_diff,
                "contract_rate": booking.contract_rate,
                "as_vendor": booking.commission_as_vendor,
                "net_as_vendor": booking.net_payable_as_vendor,
                "base_diff": booking.base_diff,
                "net_diff": booking.net_payable_diff_as_vendor,
                "uplift": booking.vat_uplift_shortfall,
            }
        )
    return values


def _verdict(booking: BookingRecon, result: TravelResult) -> str:
    """Why an unmatched booking is out; for a matched one, what disagrees."""
    if booking.unmatched_reason is not None:
        return REASON_LABELS[booking.unmatched_reason]
    rates = result.rates
    base_bad = abs(booking.base_diff) > rates.base_tolerance
    # The vendor billing off its own contract rate. A commission gap that only
    # follows from the base difference or the VAT uplift is not one.
    commission_bad = (
        abs(booking.vendor_commission - booking.vendor_expected_commission)
        > rates.commission_tolerance
    )
    if base_bad and commission_bad:
        return "Base & commission differ"
    if base_bad:
        return "Base differs"
    if commission_bad:
        return "Commission differs"
    return "Matched"


def _joined(values: Any) -> str | None:
    """Distinct values in the order met, joined; None when there are none."""
    seen = [value for value in dict.fromkeys(values) if value]
    return ", ".join(str(value) for value in seen) if seen else None


def _percent(value: Decimal) -> str:
    """``15`` rather than ``15.00``: the claim writes VAT % as a whole number."""
    return str(int(value)) if value == value.to_integral_value() else str(value)


def _sum(values: Any) -> Decimal:
    return sum(values, Decimal(0))


# --- Exceptions -------------------------------------------------------------


def _exceptions_sheet(book: Any, fmt: dict[str, Any], result: TravelResult) -> None:
    statuses = {
        b.booking_id: b.portal_status.value
        for b in result.bookings
        if b.portal_status is not None
    }

    def status_of(exc: ReconException) -> str:
        return statuses.get(exc.order_no or "", "")

    exceptions_sheet(book, fmt, result.exceptions, status_of)


# --- B2C Invoices ERP -------------------------------------------------------

_INVOICE_PRODUCTS = {
    Vertical.HOTEL: "Hotel Booking",
    Vertical.FLIGHT: "Flight Booking",
}


def _b2c_invoices_sheet(
    book: Any,
    fmt: dict[str, Any],
    result: TravelResult,
    sources: dict[str, Path],
) -> None:
    """One invoice line per matched booking. Unit price reads the Orders sheet.

    The matched bookings are the Orders sheet's own rows, in the same order, so
    each line points at its booking's Commission Charged there. Refunded,
    cancelled and one-sided bookings are not matched and are not billed.
    """
    contacts = _invoice_contacts(result, sources)
    charged = letter(_COL["commission_charged"])
    lines: list[CashbackLine] = []
    for excel_row, booking in enumerate(_matched(result), start=2):
        row = booking.portal
        if row is None:  # pragma: no cover - a matched booking has its portal row
            continue
        lines.append(
            CashbackLine(
                partner=(row.provider or "").strip(),
                order_id=booking.booking_id,
                contact=contacts.get((row.sheet, row.row), ""),
                bill_date=row.order_date,
                product=_INVOICE_PRODUCTS[row.vertical],
                unit_cell=f"Orders!{charged}{excel_row}",
            )
        )
    cashback_sheet(book, fmt, lines)


def _invoice_contacts(
    result: TravelResult, sources: dict[str, Path]
) -> dict[tuple[str, int], str]:
    """The customer's name on each portal row, by sheet and data row.

    The export repeats ``Customer`` three times, and the second is the name.
    The parsed row keeps only the first, the customer's id, so the name is
    read from the upload.
    """
    path = sources.get("portal")
    names = {item.role: item.sheets for item in result.detected}.get("portal") or []
    if path is None or not names:
        return {}
    contacts: dict[tuple[str, int], str] = {}
    for record in portal_records(path, names, parser.PORTAL_SCHEMA):
        contact = record.contact()
        if contact:
            contacts[(record.sheet, record.ordinal)] = contact
    return contacts
