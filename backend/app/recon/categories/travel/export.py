"""The travel Excel report (plan-travel.md section 8).

Three sections, told apart by tab colour and sheet-name prefix, so a reviewer
always knows what a sheet holds:

- **Matched** (green): bookings both sides carry with nothing reversed. The main
  output, and the only source of the totals.
- **Explanation** (amber): why each unmatched booking did not match, and every
  exception raised.
- **References** (grey): every other source row, so nothing is lost and each can
  be traced back to its file and row.

Every portal row and every claim row lands in exactly one of Matched or
References; the Summary proves it.

Written with ``constant_memory``: every row is flushed as it is added, so rows
go out strictly top to bottom and a sheet is finished before the next begins.
Subtotals are therefore computed values, never formulas pointing at rows that
have not been written yet.
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import xlsxwriter

from app.recon.categories.travel.models import (
    BookingMatchState,
    BookingRecon,
    PaymentMethodTotals,
    TravelInvoiceLine,
    TravelResult,
    UnmatchedReason,
    WalletState,
)
from app.recon.exceptions import ReconException, Severity
from app.recon.xlsx import MONEY, RATE, formats, heading, kv, num, table

TAB_SUMMARY = "#1F3864"
TAB_MATCHED = "#548235"
TAB_EXPLANATION = "#ED7D31"
TAB_REFERENCE = "#7F7F7F"

REASON_LABELS: dict[UnmatchedReason, str] = {
    UnmatchedReason.REFUNDED: "Refunded",
    UnmatchedReason.CANCELLED: "Cancelled",
    UnmatchedReason.MISSING_IN_VENDOR: "Missing from vendor claim",
    UnmatchedReason.MISSING_IN_PORTAL: "Missing from portal",
    UnmatchedReason.OTHER: "Other",
}
_REASON_ORDER = {reason: index for index, reason in enumerate(REASON_LABELS)}
NO_BOOKING_ID = "No booking ID — excluded from calculations"
REPEAT_ROW = "Repeat portal row — excluded"

# key, header, width, kind (text / date / money / number)
Column = tuple[str, str, int, str]


def build_report(result: TravelResult, path: Path) -> Path:
    book = xlsxwriter.Workbook(
        str(path),
        {
            "constant_memory": True,
            "in_memory": False,
            "default_date_format": "yyyy-mm-dd",
        },
    )
    try:
        fmt = _formats(book)
        _summary_sheet(book, fmt, result)
        _analysis_sheet(book, fmt, result)
        _differences_sheet(book, fmt, result)
        _matched_lines_sheet(book, fmt, result)
        _explanation_sheet(book, fmt, result)
        _exceptions_sheet(book, fmt, result)
        _portal_references_sheet(book, fmt, result)
        _vendor_references_sheet(book, fmt, result)
        _methods_sheet(book, fmt, result)
        if result.wallet is not None:
            _wallet_sheet(book, fmt, result)
        _claims_sheet(book, fmt, result)
    finally:
        book.close()
    return path


def _formats(book: xlsxwriter.Workbook) -> dict[str, Any]:
    """The shared palette, plus the side-by-side headers of the analysis."""
    fmt = formats(book)
    band = {"bold": True, "border": 1, "align": "center", "valign": "vcenter"}
    fmt["band"] = book.add_format(
        {**band, "bg_color": TAB_SUMMARY, "font_color": "white"}
    )
    fmt["band_measure"] = book.add_format({**band, "bg_color": "#D9E1F2"})
    head = {**band, "text_wrap": True}
    fmt["head"] = book.add_format(
        {**head, "bg_color": TAB_SUMMARY, "font_color": "white"}
    )
    fmt["head_portal"] = book.add_format({**head, "bg_color": "#BDD7EE"})
    fmt["head_vendor"] = book.add_format({**head, "bg_color": "#A9D08E"})
    fmt["head_diff"] = book.add_format({**head, "bg_color": "#F8CBAD"})
    fmt["money_vendor"] = book.add_format({"num_format": MONEY, "bg_color": "#E2EFDA"})
    fmt["rate_vendor"] = book.add_format({"num_format": RATE, "bg_color": "#E2EFDA"})
    fmt["check_ok"] = book.add_format(
        {"bold": True, "font_color": "#375623", "bg_color": "#C6EFCE"}
    )
    fmt["check_bad"] = book.add_format(
        {"bold": True, "font_color": "#9C0006", "bg_color": "#FFC7CE"}
    )
    fmt["alert"] = book.add_format(
        {"bold": True, "font_size": 12, "font_color": "#9C0006"}
    )
    fmt["good"] = book.add_format(
        {"bold": True, "font_size": 12, "font_color": "#375623"}
    )
    for name, colour in (
        ("matched", TAB_MATCHED),
        ("explanation", TAB_EXPLANATION),
        ("reference", TAB_REFERENCE),
    ):
        fmt[f"section_{name}"] = book.add_format(
            {"bold": True, "bg_color": colour, "font_color": "white"}
        )
    return fmt


def _summary_sheet(book: Any, fmt: dict[str, Any], result: TravelResult) -> None:
    sheet = book.add_worksheet("Summary")
    sheet.set_tab_color(TAB_SUMMARY)
    sheet.set_column(0, 0, 50)
    sheet.set_column(1, 4, 18)
    s = result.summary
    row = heading(sheet, fmt, 0, "Travel reconciliation") + 1

    for label, text in (
        ("Vendor", result.vendor_name or "—"),
        ("Period start", str(result.period_start)),
        ("Period end", str(result.period_end)),
    ):
        row = kv(sheet, fmt, row, label, text)

    row = heading(sheet, fmt, row + 1, "How to read this workbook")
    for style, section, text in (
        (
            "section_matched",
            "Matched",
            "Green tabs: bookings on both sides with nothing reversed. The main "
            "output; every total below is taken from these.",
        ),
        (
            "section_explanation",
            "Explanation",
            "Amber tabs: why each unmatched booking did not match, and every "
            "exception raised.",
        ),
        (
            "section_reference",
            "References",
            "Grey tabs: every other source row, so nothing is lost. Each carries "
            "the file and row it came from.",
        ),
    ):
        sheet.write(row, 0, section, fmt[style])
        sheet.write(row, 1, text)
        row += 1

    row = _match_result(sheet, fmt, row + 1, result)

    row = heading(sheet, fmt, row + 1, "Volumes")
    both = sum(1 for b in result.bookings if b.match_state is BookingMatchState.MATCHED)
    volumes: tuple[tuple[str, int], ...] = (
        ("Portal rows read", s.portal_rows),
        ("Portal rows with no booking ID (excluded)", s.portal_rows_without_booking_id),
        ("Bookings on the portal", s.portal_bookings),
        ("Claim line items", s.vendor_lines),
        ("Claim rows with no booking ID (excluded)", s.vendor_rows_without_booking_id),
        ("Bookings on the claim", s.vendor_bookings),
        ("Booking IDs on both sides", both),
        ("On the portal only", s.bookings_portal_only),
        ("On the claim only", s.bookings_vendor_only),
        ("Repeated portal rows (excluded)", s.repeat_rows),
    )
    for label, count in volumes:
        sheet.write(row, 0, label, fmt["label"])
        sheet.write_number(row, 1, count)
        row += 1

    row = heading(sheet, fmt, row + 1, "Matched bookings: portal against claim")
    for col, title in enumerate(("Portal", "Claim", "Difference"), start=1):
        sheet.write(row, col, title, fmt["header"])
    row += 1
    for label, ours, theirs, diff, bad in (
        (
            "Base amount",
            s.portal_base,
            s.vendor_base,
            s.base_difference,
            abs(s.base_difference) > result.rates.base_tolerance,
        ),
        (
            "Commission",
            s.portal_commission,
            s.vendor_commission,
            s.commission_difference,
            False,
        ),
        (
            "Net payable",
            s.portal_net_payable,
            s.vendor_net_payable,
            s.net_payable_difference,
            False,
        ),
    ):
        sheet.write(row, 0, label, fmt["label"])
        sheet.write(row, 1, num(ours), fmt["money"])
        sheet.write(row, 2, num(theirs), fmt["money_vendor"])
        sheet.write(row, 3, num(diff), fmt[_diff_style(diff, bad)])
        row += 1
    for label, amount in (
        (
            "Commission difference at the contract rate",
            s.commission_difference_at_contract,
        ),
        (
            "Net payable difference at the contract rate",
            s.net_payable_difference_at_contract,
        ),
    ):
        sheet.write(row, 0, label, fmt["label"])
        sheet.write(row, 3, num(amount), fmt["money_bad" if amount != 0 else "money"])
        row += 1
    sheet.write(
        row,
        0,
        "Our commission is short of the claim's on international bookings by the "
        "VAT uplift; at the contract rate only the base difference remains.",
        fmt["info"],
    )
    row += 1

    row = heading(sheet, fmt, row + 1, "Settlement, matched bookings")
    settlement: tuple[tuple[str, Decimal, str], ...] = (
        (
            "Commission at the contract rate on our base",
            s.commission_as_vendor,
            "money",
        ),
        ("Net payable at the contract rate", s.net_payable_as_vendor, "money_bold"),
        ("VAT uplift never charged to customers", s.vat_uplift_shortfall, "money_warn"),
    )
    for label, amount, style in settlement:
        sheet.write(row, 0, label, fmt["label"])
        sheet.write(row, 1, num(amount), fmt[style if amount != 0 else "money"])
        row += 1

    row = heading(sheet, fmt, row + 1, "The claim as billed")
    for col, title in enumerate(("Whole claim", "Matched", "Unmatched"), start=1):
        sheet.write(row, col, title, fmt["header"])
    row += 1
    for label, whole, matched in (
        ("Base amount", s.claim_base, s.vendor_base),
        ("Commission", s.claim_commission, s.vendor_commission),
        ("Net payable", s.claim_net_payable, s.vendor_net_payable),
    ):
        sheet.write(row, 0, label, fmt["label"])
        sheet.write(row, 1, num(whole), fmt["money_vendor"])
        sheet.write(row, 2, num(matched), fmt["money"])
        sheet.write(row, 3, num(whole - matched), fmt["money"])
        row += 1
    claim_level: tuple[tuple[str, Decimal, str], ...] = (
        ("Commission billed above the contract rate", s.vendor_overcharge, "money_bad"),
        ("Adjustments credited by the vendor", s.vendor_adjustments, "money"),
        ("Cancellation and change fees retained", s.retained_fees, "money"),
        (
            "Commission kept on refunded bookings",
            s.commission_retained_on_refunds,
            "money_bad",
        ),
    )
    for label, amount, style in claim_level:
        sheet.write(row, 0, label, fmt["label"])
        sheet.write(row, 1, num(amount), fmt[style if amount != 0 else "money"])
        row += 1

    row = _tie_out(sheet, fmt, row + 1, result)

    row = heading(sheet, fmt, row + 1, "Commission by vertical, matched bookings")
    sheet.write(row, 1, "Including VAT", fmt["header"])
    sheet.write(row, 2, "Excluding VAT", fmt["header"])
    row += 1
    for label, gross, net in (
        ("Hotel", s.hotel_commission, s.hotel_commission_ex_vat),
        ("Flights", s.flight_commission, s.flight_commission_ex_vat),
    ):
        sheet.write(row, 0, label, fmt["label"])
        sheet.write(row, 1, num(gross), fmt["money"])
        sheet.write(row, 2, num(net), fmt["money"])
        row += 1

    if result.wallet is not None:
        wallet = result.wallet
        row = heading(sheet, fmt, row + 1, "Wallet")
        for label, count in (
            ("Wallet-paid rows checked", wallet.rows_checked),
            ("Found in the history", wallet.matched),
            ("Amount disagreed", wallet.amount_mismatch),
            ("Outside the history's window", wallet.out_of_window),
            ("Missing from the history", wallet.missing),
        ):
            sheet.write(row, 0, label, fmt["label"])
            sheet.write_number(row, 1, count)
            row += 1
        row = kv(
            sheet,
            fmt,
            row,
            "History covers",
            f"{wallet.window_start} to {wallet.window_end}",
        )

    row = heading(sheet, fmt, row + 1, "Exceptions by code")
    for code, count in sorted(result.summary.exception_counts.items()):
        sheet.write(row, 0, code)
        sheet.write_number(row, 1, count)
        row += 1

    # The legend: which rates produced these numbers. Without it a report
    # cannot be re-derived months later.
    row = heading(sheet, fmt, row + 1, "Rates applied to this run")
    rates = result.rates
    legend: tuple[tuple[str, float | str, str | None], ...] = (
        ("Hotel rate (local)", num(rates.hotel_rate), "rate"),
        ("Flight rate (local)", num(rates.flight_rate), "rate"),
        ("VAT added on international commission", num(rates.vat_rate), "rate"),
        (
            "Hotel rate (international)",
            num(rates.hotel_rate * (1 + rates.vat_rate)),
            "rate",
        ),
        (
            "Flight rate (international)",
            num(rates.flight_rate * (1 + rates.vat_rate)),
            "rate",
        ),
        ("Base tolerance", num(rates.base_tolerance), "money"),
        ("Commission tolerance", num(rates.commission_tolerance), "money"),
    )
    for label, shown, legend_style in legend:
        row = kv(sheet, fmt, row, label, shown, style=legend_style)
    sheet.write(
        row + 1,
        0,
        "Local or international is decided by the VAT on the claim, never by its "
        "DOM/INT label. The payable is our own base at the contract rate.",
        fmt["info"],
    )


def _match_result(
    sheet: Any, fmt: dict[str, Any], row: int, result: TravelResult
) -> int:
    """The headline count of what did and did not match, by reason."""
    s = result.summary
    row = heading(sheet, fmt, row, "Match result, by booking ID")
    sheet.write(row, 0, "Bookings matched", fmt["label"])
    sheet.write_number(row, 1, s.bookings_matched)
    row += 1
    sheet.write(
        row,
        0,
        f"{s.bookings_unmatched} booking IDs could not be matched",
        fmt["alert" if s.bookings_unmatched else "good"],
    )
    row += 1

    unmatched = _unmatched(result)
    if unmatched:
        for col, title in enumerate(
            ("Reason", "Bookings", "Portal base", "Claim base")
        ):
            sheet.write(row, col, title, fmt["header"])
        row += 1
        for reason, label in REASON_LABELS.items():
            group = [b for b in unmatched if b.unmatched_reason is reason]
            if not group:
                continue
            sheet.write(row, 0, label)
            sheet.write_number(row, 1, len(group))
            sheet.write(row, 2, num(_total(b.portal_base for b in group)), fmt["money"])
            sheet.write(row, 3, num(_total(b.vendor_base for b in group)), fmt["money"])
            row += 1
        sheet.write(row, 0, "Total", fmt["label"])
        sheet.write_number(row, 1, len(unmatched), fmt["label"])
        sheet.write(
            row, 2, num(_total(b.portal_base for b in unmatched)), fmt["money_bold"]
        )
        sheet.write(
            row, 3, num(_total(b.vendor_base for b in unmatched)), fmt["money_bold"]
        )
        row += 1
        sheet.write(
            row,
            0,
            "Each one is explained on 'Unmatched - Explanation'.",
            fmt["info"],
        )
        row += 1
    sheet.write(
        row,
        0,
        f"{s.portal_rows_without_booking_id} portal row(s) and "
        f"{s.vendor_rows_without_booking_id} claim row(s) carry no booking ID: "
        "excluded from every calculation and listed in References.",
        fmt["info"],
    )
    return row + 1


def _tie_out(sheet: Any, fmt: dict[str, Any], row: int, result: TravelResult) -> int:
    """Proof that every source row is on exactly one of Matched or References."""
    s = result.summary
    row = heading(sheet, fmt, row, "Every source row accounted for")
    for col, title in enumerate(("Read", "Matched", "References", "Check"), start=1):
        sheet.write(row, col, title, fmt["header"])
    row += 1
    references = result.references
    matched_lines = len(_lines(result, matched=True))
    for label, read, in_matched, in_references in (
        ("Portal rows", s.portal_rows, s.bookings_matched, len(references.portal_rows)),
        (
            "Claim line items",
            s.vendor_lines,
            matched_lines,
            len(_lines(result, matched=False)),
        ),
        (
            "Claim rows with no booking ID",
            s.vendor_rows_without_booking_id,
            0,
            len(references.vendor_rows_without_booking_id),
        ),
        ("Claim adjustment rows", len(result.adjustments), 0, len(result.adjustments)),
    ):
        agrees = read == in_matched + in_references
        sheet.write(row, 0, label, fmt["label"])
        sheet.write_number(row, 1, read)
        sheet.write_number(row, 2, in_matched)
        sheet.write_number(row, 3, in_references)
        sheet.write(
            row,
            4,
            "OK" if agrees else "Rows missing",
            fmt["check_ok" if agrees else "check_bad"],
        )
        row += 1
    return row


# The side-by-side: each measure shows portal, vendor and their difference next
# to each other under one band. (group, ((header, width, header style), ...))
_ANALYSIS_GROUPS: tuple[tuple[str, tuple[tuple[str, int, str], ...]], ...] = (
    (
        "BOOKING",
        (
            ("Booking", 20, "head"),
            ("Vertical", 9, "head"),
            ("Locality", 13, "head"),
            ("Booked", 11, "head"),
            ("Portal status", 12, "head"),
            ("Payment method", 18, "head"),
            ("Claim lines", 8, "head"),
        ),
    ),
    (
        "BASE AMOUNT",
        (
            ("Portal", 13, "head_portal"),
            ("Vendor", 13, "head_vendor"),
            ("Difference", 12, "head_diff"),
        ),
    ),
    (
        "COMMISSION",
        (
            ("Portal", 13, "head_portal"),
            ("Vendor", 13, "head_vendor"),
            ("Difference", 12, "head_diff"),
        ),
    ),
    (
        "NET PAYABLE",
        (
            ("Portal", 13, "head_portal"),
            ("Vendor", 13, "head_vendor"),
            ("Difference", 12, "head_diff"),
        ),
    ),
    (
        "COMMISSION RATE",
        (
            ("Portal", 9, "head_portal"),
            ("Vendor", 9, "head_vendor"),
            ("Contract", 9, "head"),
        ),
    ),
    (
        "AT THE CONTRACT RATE",
        (
            ("Commission", 13, "head"),
            ("Net payable", 13, "head"),
            ("Commission difference", 13, "head_diff"),
            ("Net payable difference", 13, "head_diff"),
        ),
    ),
    ("VAT UPLIFT", (("Not charged", 12, "head"),)),
    (
        "RESULT",
        (
            ("Check", 22, "head"),
            ("Flags", 18, "head"),
            ("Invoices", 22, "head"),
        ),
    ),
)


def _analysis_sheet(book: Any, fmt: dict[str, Any], result: TravelResult) -> None:
    """Matched bookings, portal and vendor side by side for each measure."""
    sheet = book.add_worksheet("Matched - Booking Analysis")
    sheet.set_tab_color(TAB_MATCHED)
    sheet.set_row(1, 30)

    col = 0
    for group, columns in _ANALYSIS_GROUPS:
        last = col + len(columns) - 1
        style = fmt["band" if group in {"BOOKING", "RESULT"} else "band_measure"]
        if last > col:
            sheet.merge_range(0, col, 0, last, group, style)
        else:
            sheet.write(0, col, group, style)
        for _title, width, _head in columns:
            sheet.set_column(col, col, width)
            col += 1
    width = col
    col = 0
    for _group, columns in _ANALYSIS_GROUPS:
        for title, _width, head in columns:
            sheet.write(1, col, title, fmt[head])
            col += 1

    matched = _matched(result)
    totals: dict[int, Decimal] = {}
    row = 2
    for booking in matched:
        for col, (value, style) in enumerate(_analysis_cells(booking, result)):
            if isinstance(value, Decimal):
                if style and style.startswith("money"):
                    totals[col] = totals.get(col, Decimal(0)) + value
                value = num(value)
            elif value is None:
                value = ""
            sheet.write(row, col, value, fmt[style] if style else None)
        row += 1

    sheet.write(row, 0, f"Total, {len(matched)} matched", fmt["label"])
    for col, total in totals.items():
        sheet.write(row, col, num(total), fmt["money_bold"])
    sheet.write(
        row + 2,
        0,
        "Green cells are the vendor's figures. Red: a difference beyond tolerance. "
        "Amber: a commission gap already explained by the base difference or by "
        "the VAT uplift on international bookings. "
        "Unmatched bookings are on 'Unmatched - Explanation'.",
        fmt["info"],
    )
    sheet.freeze_panes(2, 1)
    sheet.autofilter(1, 0, max(row - 1, 1), width - 1)


def _analysis_cells(
    booking: BookingRecon, result: TravelResult
) -> list[tuple[Any, str | None]]:
    """One booking's row, in ``_ANALYSIS_GROUPS`` order, with each cell's style."""
    rates = result.rates
    base_bad = abs(booking.base_diff) > rates.base_tolerance
    # The vendor billing off its own contract rate. A commission gap that only
    # follows from the base difference or the VAT uplift is not one.
    commission_bad = (
        abs(booking.vendor_commission - booking.vendor_expected_commission)
        > rates.commission_tolerance
    )
    net_bad = abs(booking.net_payable_diff_as_vendor) > rates.base_tolerance
    if base_bad and commission_bad:
        check = "Base & commission differ"
    elif base_bad:
        check = "Base differs"
    elif commission_bad:
        check = "Commission differs"
    else:
        check = "OK"
    return [
        (booking.booking_id, None),
        (booking.vertical.value, None),
        (booking.locality.value, None),
        (booking.booking_date, "date"),
        (booking.portal_status.value if booking.portal_status else None, None),
        (booking.payment_method, None),
        (booking.vendor_lines, None),
        (booking.portal_base, "money"),
        (booking.vendor_base, "money_vendor"),
        (booking.base_diff, _diff_style(booking.base_diff, base_bad)),
        (booking.portal_commission, "money"),
        (booking.vendor_commission, "money_vendor"),
        (booking.commission_diff, _diff_style(booking.commission_diff, commission_bad)),
        (booking.portal_net_payable, "money"),
        (booking.vendor_net_payable, "money_vendor"),
        (booking.net_payable_diff, _diff_style(booking.net_payable_diff, net_bad)),
        (booking.sg_rate, "rate"),
        (booking.vendor_rate, "rate_vendor"),
        (booking.contract_rate, "rate"),
        (booking.commission_as_vendor, "money"),
        (booking.net_payable_as_vendor, "money"),
        (
            booking.commission_diff_as_vendor,
            _diff_style(booking.commission_diff_as_vendor, commission_bad),
        ),
        (booking.net_payable_diff_as_vendor, "money_bad" if net_bad else "money"),
        (
            booking.vat_uplift_shortfall,
            "money_warn" if booking.vat_uplift_shortfall > 0 else "money",
        ),
        (check, "check_ok" if check == "OK" else "check_bad"),
        (", ".join(booking.flags), None),
        (", ".join(booking.vendor_invoice_nos), None),
    ]


def _diff_style(diff: Decimal, bad: bool) -> str:
    """Red beyond tolerance; amber when it differs only by what is explained."""
    if bad:
        return "money_bad"
    return "money_warn" if diff != 0 else "money"


def _differences_sheet(book: Any, fmt: dict[str, Any], result: TravelResult) -> None:
    """The client's ``Sheet1``: our figures, theirs, and the gap between them."""
    sheet = book.add_worksheet("Matched - Base Differences")
    sheet.set_tab_color(TAB_MATCHED)
    widths = (22, 9, 14, 16, 16, 14, 16, 16, 14, 16, 16)
    for col, width in enumerate(widths):
        sheet.set_column(col, col, width)

    sheet.write(0, 2, "SHOPPINGGATE", fmt["title"])
    sheet.write(0, 5, "ALMOSAFER", fmt["title"])
    sheet.write(0, 8, "DIFFERENCE", fmt["title"])
    headers = (
        "Booking",
        "Vertical",
        "Base amount",
        "Commission",
        "Net payable",
        "Base amount",
        "Commission",
        "Net payable",
        "Base amount",
        "Commission",
        "Net payable",
    )
    for col, title in enumerate(headers):
        sheet.write(1, col, title, fmt["header"])
    sheet.freeze_panes(2, 0)

    tolerance = result.rates.base_tolerance
    differing = [b for b in _matched(result) if abs(b.base_diff) > tolerance]
    row = 2
    totals = [Decimal(0)] * 9
    for booking in differing:
        values = (
            booking.portal_base,
            booking.commission_as_vendor,
            booking.net_payable_as_vendor,
            booking.vendor_base,
            booking.vendor_commission,
            booking.vendor_net_payable,
            booking.base_diff,
            booking.commission_diff_as_vendor,
            booking.net_payable_diff_as_vendor,
        )
        sheet.write(row, 0, booking.booking_id)
        sheet.write(row, 1, booking.vertical.value)
        for offset, value in enumerate(values):
            style = "money_bad" if offset >= 6 and value != 0 else "money"
            sheet.write(row, 2 + offset, num(value), fmt[style])
            totals[offset] += value
        row += 1

    sheet.write(row, 0, "Total", fmt["label"])
    for offset, total in enumerate(totals):
        sheet.write(row, 2 + offset, num(total), fmt["money_bold"])
    sheet.write(
        row + 2,
        0,
        "Both sides are shown at the contract rate, so what remains is the "
        "difference in the base amount alone.",
        fmt["info"],
    )


_LINE_COLUMNS: tuple[Column, ...] = (
    ("booking", "Booking", 22, "text"),
    ("reference", "Reference", 26, "text"),
    ("invoice", "Invoice", 12, "text"),
    ("line", "Line", 8, "text"),
    ("billing", "Billing type", 18, "text"),
    ("service", "Service", 8, "text"),
    ("issued", "Issued", 12, "date"),
    ("provider", "Provider", 22, "text"),
    ("supplier", "Supplier", 18, "text"),
    ("description", "Description", 24, "text"),
    ("pnr", "PNR", 14, "text"),
    ("client", "Client", 26, "text"),
    ("route", "Route", 22, "text"),
    ("city", "City", 16, "text"),
    ("class", "Class", 22, "text"),
    ("check_in", "Check in", 12, "text"),
    ("check_out", "Check out", 12, "text"),
    ("dom_int", "DOM/INT label", 13, "text"),
    ("base_ex_vat", "Base ex VAT", 14, "money"),
    ("vat_percent", "VAT %", 8, "number"),
    ("vat", "VAT", 12, "money"),
    ("cancel_fee", "Cancel fee", 12, "money"),
    ("total", "Total invoice", 14, "money"),
    ("commission", "Commission", 13, "money"),
    ("net_payable", "Net payable", 14, "money"),
    ("currency", "Currency", 9, "text"),
    ("source", "Source", 28, "text"),
)

# Leads every references sheet: why the row is here rather than in Matched.
_WHY_COLUMNS: tuple[Column, ...] = (
    ("why", "Why here", 34, "text"),
    ("detail", "Detail", 60, "text"),
)


def _matched_lines_sheet(book: Any, fmt: dict[str, Any], result: TravelResult) -> None:
    """The claim lines of the matched bookings, tagged with their booking."""
    sheet = book.add_worksheet("Matched - Invoice Lines")
    sheet.set_tab_color(TAB_MATCHED)
    _headers(sheet, fmt, _LINE_COLUMNS)
    values = [_line_values(line) for line in _lines(result, matched=True)]
    for index, cells in enumerate(values, start=1):
        _write_values(sheet, fmt, index, _LINE_COLUMNS, cells)
    _write_totals(sheet, fmt, len(values) + 1, _LINE_COLUMNS, values)


_EXPLANATION_COLUMNS: tuple[Column, ...] = (
    ("booking", "Booking", 20, "text"),
    ("vertical", "Vertical", 9, "text"),
    ("reason", "Reason", 24, "text"),
    ("explanation", "Explanation", 80, "text"),
    ("found_in", "Found in", 10, "text"),
    ("booked", "Booked", 11, "date"),
    ("portal_status", "Portal status", 12, "text"),
    ("payment_status", "Payment status", 13, "text"),
    ("portal_base", "Portal base", 13, "money"),
    ("portal_commission", "Portal commission", 14, "money"),
    ("vendor_base", "Claim base", 13, "money"),
    ("vendor_commission", "Claim commission", 14, "money"),
    ("cancel_fee", "Cancellation fee", 13, "money"),
    ("change_fee", "Change fee", 11, "money"),
    ("adjustment", "Adjustment", 11, "money"),
    ("severity", "Highest severity", 10, "text"),
    ("exceptions", "Exceptions raised", 60, "text"),
)


def _explanation_sheet(book: Any, fmt: dict[str, Any], result: TravelResult) -> None:
    """One row per unmatched booking: the reason, and the figures behind it."""
    sheet = book.add_worksheet("Unmatched - Explanation")
    sheet.set_tab_color(TAB_EXPLANATION)
    _headers(sheet, fmt, _EXPLANATION_COLUMNS)
    sheet.freeze_panes(1, 1)

    raised = _exceptions_by_booking(result)
    unmatched = sorted(
        _unmatched(result),
        key=lambda b: (
            _REASON_ORDER[b.unmatched_reason] if b.unmatched_reason else 0,
            b.booking_date or date.min,
            b.booking_id,
        ),
    )
    rows: list[dict[str, Any]] = []
    for index, booking in enumerate(unmatched, start=1):
        found = raised.get(booking.booking_id, [])
        # Exceptions are sorted most severe first.
        severity = found[0].severity if found else None
        cells = {
            "booking": booking.booking_id,
            "vertical": booking.vertical.value,
            "reason": _reason_label(booking),
            "explanation": booking.explanation,
            "found_in": _found_in(booking),
            "booked": booking.booking_date,
            "portal_status": booking.portal_status.value
            if booking.portal_status
            else None,
            "payment_status": booking.portal_payment_status.value
            if booking.portal_payment_status
            else None,
            "portal_base": booking.portal_base,
            "portal_commission": booking.portal_commission,
            "vendor_base": booking.vendor_base,
            "vendor_commission": booking.vendor_commission,
            "cancel_fee": booking.vendor_cancel_fee,
            "change_fee": booking.vendor_change_fee,
            "adjustment": booking.vendor_adjustment,
            "severity": str(severity) if severity else None,
            "exceptions": ", ".join(dict.fromkeys(str(e.code) for e in found)),
        }
        rows.append(cells)
        _write_values(
            sheet,
            fmt,
            index,
            _EXPLANATION_COLUMNS,
            cells,
            styles={"reason": "label", "severity": "high"}
            if severity is Severity.HIGH
            else {"reason": "label"},
        )
    _write_totals(sheet, fmt, len(rows) + 1, _EXPLANATION_COLUMNS, rows)


def _exceptions_sheet(book: Any, fmt: dict[str, Any], result: TravelResult) -> None:
    sheet = book.add_worksheet("Exceptions")
    sheet.set_tab_color(TAB_EXPLANATION)
    table(
        sheet,
        fmt,
        ("Severity", "Code", "Booking", "Amount", "Detail"),
        (10, 36, 24, 14, 90),
    )
    for index, exc in enumerate(result.exceptions, start=1):
        style = fmt["high"] if exc.severity is Severity.HIGH else None
        sheet.write(index, 0, str(exc.severity), style)
        sheet.write(index, 1, str(exc.code), style)
        sheet.write(index, 2, exc.order_no or "")
        sheet.write(index, 3, num(exc.amount), fmt["money"])
        sheet.write(index, 4, exc.detail)


_PORTAL_COLUMNS: tuple[Column, ...] = (
    *_WHY_COLUMNS,
    ("booking", "Booking", 20, "text"),
    ("sg_order_id", "SG order id", 11, "text"),
    ("vertical", "Vertical", 9, "text"),
    ("order_date", "Order date", 11, "date"),
    ("status", "Order status", 12, "text"),
    ("payment_status", "Payment status", 13, "text"),
    ("payment_method", "Payment method", 18, "text"),
    ("order_total", "Order total", 13, "money"),
    ("vat", "VAT", 10, "money"),
    ("commission", "Commission charged", 14, "money"),
    ("net_payable", "Net payable", 13, "money"),
    ("wallet", "Wallet", 11, "money"),
    ("card", "Card / other", 12, "money"),
    ("source", "Source", 30, "text"),
)


def _portal_references_sheet(
    book: Any, fmt: dict[str, Any], result: TravelResult
) -> None:
    """Every portal row no matched booking stands on, and why."""
    sheet = book.add_worksheet("Ref - Portal Rows")
    sheet.set_tab_color(TAB_REFERENCE)
    _headers(sheet, fmt, _PORTAL_COLUMNS)

    by_id = {b.booking_id: b for b in result.bookings}
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(result.references.portal_rows, start=1):
        booking = by_id.get(row.booking_id) if row.booking_id else None
        if booking is None:
            why = NO_BOOKING_ID
            detail = f"order {row.status.value}, payment {row.payment_status.value}"
        # The pipeline lists an unmatched booking's own row before its repeats;
        # every row of a matched booking that is here is a repeat.
        elif booking.unmatched_reason is None or booking.booking_id in seen:
            why, detail = REPEAT_ROW, f"repeats booking {booking.booking_id}"
        else:
            why, detail = f"Unmatched: {_reason_label(booking)}", booking.explanation
        if row.booking_id:
            seen.add(row.booking_id)
        cells = {
            "why": why,
            "detail": detail,
            "booking": row.booking_id,
            "sg_order_id": row.sg_order_id,
            "vertical": row.vertical.value,
            "order_date": row.order_date,
            "status": row.status.value,
            "payment_status": row.payment_status.value,
            "payment_method": row.payment_method,
            "order_total": row.order_total,
            "vat": row.vat,
            "commission": row.commission_charged,
            "net_payable": row.net_payable,
            "wallet": row.wallet_amount,
            "card": row.card_amount,
            "source": _source(row.source, row.sheet, row.row),
        }
        rows.append(cells)
        _write_values(
            sheet, fmt, index, _PORTAL_COLUMNS, cells, styles={"why": "label"}
        )
    _write_totals(sheet, fmt, len(rows) + 1, _PORTAL_COLUMNS, rows)


_VENDOR_REFERENCE_COLUMNS: tuple[Column, ...] = (*_WHY_COLUMNS, *_LINE_COLUMNS)


def _vendor_references_sheet(
    book: Any, fmt: dict[str, Any], result: TravelResult
) -> None:
    """Claim lines of unmatched bookings, rows with no booking id, adjustments."""
    sheet = book.add_worksheet("Ref - Vendor Rows")
    sheet.set_tab_color(TAB_REFERENCE)
    _headers(sheet, fmt, _VENDOR_REFERENCE_COLUMNS)

    by_id = {b.booking_id: b for b in result.bookings}
    rows: list[dict[str, Any]] = []
    for line in _lines(result, matched=False):
        booking = by_id[line.booking_id]
        rows.append(
            {
                "why": f"Unmatched: {_reason_label(booking)}",
                "detail": booking.explanation,
                **_line_values(line),
            }
        )
    for skipped in result.references.vendor_rows_without_booking_id:
        rows.append(
            {
                "why": NO_BOOKING_ID,
                "detail": skipped.reason,
                "invoice": skipped.invoice_no,
                "total": skipped.total_invoice,
                "commission": skipped.commission,
                "net_payable": skipped.net_payable,
                "source": _source(skipped.source, skipped.sheet, skipped.row),
            }
        )
    for adjustment in result.adjustments:
        rows.append(
            {
                "why": "Adjustment"
                if adjustment.booking_id
                else "Adjustment, booking not identified",
                "detail": adjustment.text,
                "booking": adjustment.booking_id,
                "commission": adjustment.commission,
                "net_payable": adjustment.net_payable,
                "source": _source(adjustment.source, adjustment.sheet, adjustment.row),
            }
        )
    for index, cells in enumerate(rows, start=1):
        _write_values(
            sheet,
            fmt,
            index,
            _VENDOR_REFERENCE_COLUMNS,
            cells,
            styles={"why": "label"},
        )
    _write_totals(sheet, fmt, len(rows) + 1, _VENDOR_REFERENCE_COLUMNS, rows)


def _methods_sheet(book: Any, fmt: dict[str, Any], result: TravelResult) -> None:
    """How much moved on which instrument (plan-travel.md 3.7)."""
    sheet = book.add_worksheet("Ref - Payment Methods")
    sheet.set_tab_color(TAB_REFERENCE)
    widths = (28, 10, 10, 16, 16, 16)
    for col, width in enumerate(widths):
        sheet.set_column(col, col, width)

    row = heading(sheet, fmt, 0, "By payment method, as the portal records it")
    row = _method_block(sheet, fmt, row, result.payment_methods.by_label)
    row = heading(sheet, fmt, row + 1, "By instrument, splitting a combined payment")
    row = _method_block(sheet, fmt, row, result.payment_methods.by_instrument)
    sheet.write(
        row + 1,
        0,
        "A combined payment contributes the amount that actually moved on each "
        "instrument, so the column still totals the order value. Rows with no "
        "booking ID are left out.",
        fmt["info"],
    )


def _method_block(
    sheet: Any, fmt: dict[str, Any], row: int, totals: list[PaymentMethodTotals]
) -> int:
    headers = ("Method", "Rows", "Booked", "Order total", "Wallet", "Card / other")
    for col, title in enumerate(headers):
        sheet.write(row, col, title, fmt["header"])
    row += 1
    sums = [Decimal(0), Decimal(0), Decimal(0)]
    rows = booked = 0
    for entry in totals:
        sheet.write(row, 0, entry.label)
        sheet.write_number(row, 1, entry.rows)
        sheet.write_number(row, 2, entry.bookings)
        sheet.write(row, 3, num(entry.order_total), fmt["money"])
        sheet.write(row, 4, num(entry.wallet_amount), fmt["money"])
        sheet.write(row, 5, num(entry.card_amount), fmt["money"])
        sums[0] += entry.order_total
        sums[1] += entry.wallet_amount
        sums[2] += entry.card_amount
        rows += entry.rows
        booked += entry.bookings
        row += 1
    sheet.write(row, 0, "Total", fmt["label"])
    sheet.write_number(row, 1, rows)
    sheet.write_number(row, 2, booked)
    for offset, total in enumerate(sums):
        sheet.write(row, 3 + offset, num(total), fmt["money_bold"])
    return row + 1


def _wallet_sheet(book: Any, fmt: dict[str, Any], result: TravelResult) -> None:
    wallet = result.wallet
    if wallet is None:  # pragma: no cover - guarded by the caller
        return
    sheet = book.add_worksheet("Ref - Wallet")
    sheet.set_tab_color(TAB_REFERENCE)
    headers = (
        "Vertical",
        "SG order id",
        "Booking",
        "Order date",
        "Portal wallet",
        "History paid",
        "History refunded",
        "Movements",
        "State",
        "Difference",
    )
    table(sheet, fmt, headers, (9, 12, 22, 12, 14, 14, 16, 11, 16, 13))
    for index, check in enumerate(wallet.checks, start=1):
        style = "money_bad" if check.state is WalletState.AMOUNT_MISMATCH else "money"
        sheet.write(index, 0, check.vertical.value)
        sheet.write(index, 1, check.sg_order_id)
        sheet.write(index, 2, check.booking_id or "")
        sheet.write(index, 3, _date(check.order_date), fmt["date"])
        sheet.write(index, 4, num(check.portal_wallet_amount), fmt["money"])
        sheet.write(index, 5, num(check.history_paid), fmt["money"])
        sheet.write(index, 6, num(check.history_refunded), fmt["money"])
        sheet.write_number(index, 7, check.events)
        sheet.write(index, 8, check.state.value)
        sheet.write(index, 9, num(check.diff), fmt[style])

    row = len(wallet.checks) + 2
    sheet.write(row, 0, "Wallet movements with no portal row", fmt["title"])
    row += 1
    for event in wallet.unmatched_history:
        sheet.write(row, 0, event.vertical.value)
        sheet.write(row, 1, event.sg_order_id)
        sheet.write(row, 3, _date(event.event_date), fmt["date"])
        sheet.write(row, 4, num(event.amount), fmt["money"])
        sheet.write(row, 8, event.message or "")
        row += 1


def _claims_sheet(book: Any, fmt: dict[str, Any], result: TravelResult) -> None:
    """The vendor's own claim ledger, beside what this run computed."""
    sheet = book.add_worksheet("Ref - Claim Periods")
    sheet.set_tab_color(TAB_REFERENCE)
    headers = (
        "Claim period",
        "Total claim",
        "Commission",
        "Total payable",
        "Paid",
        "Difference",
        "Unprovided commission",
        "Source",
    )
    table(sheet, fmt, headers, (40, 14, 14, 16, 14, 14, 20, 28))
    row = 1
    for period in result.claim_periods:
        sheet.write(row, 0, period.claim_period)
        sheet.write(row, 1, num(period.total_claim), fmt["money"])
        sheet.write(row, 2, num(period.commission_amount), fmt["money"])
        sheet.write(row, 3, num(period.total_payable), fmt["money"])
        sheet.write(row, 4, num(period.paid_amount), fmt["money"])
        sheet.write(row, 5, num(period.difference), fmt["money"])
        sheet.write(row, 6, num(period.unprovided_commission), fmt["money"])
        sheet.write(row, 7, period.source)
        row += 1

    row += 1
    sheet.write(row, 0, "This run", fmt["title"])
    row += 1
    s = result.summary
    for label, amount in (
        ("Claim base", s.claim_base),
        ("Claim commission", s.claim_commission),
        ("Claim net payable", s.claim_net_payable),
        ("Our net payable at the contract rate, matched", s.net_payable_as_vendor),
    ):
        sheet.write(row, 0, label, fmt["label"])
        sheet.write(row, 1, num(amount), fmt["money"])
        row += 1


def _matched(result: TravelResult) -> list[BookingRecon]:
    return [b for b in result.bookings if b.unmatched_reason is None]


def _unmatched(result: TravelResult) -> list[BookingRecon]:
    return [b for b in result.bookings if b.unmatched_reason is not None]


def _lines(result: TravelResult, *, matched: bool) -> list[TravelInvoiceLine]:
    """The claim lines on one side of the split, so each is reported once."""
    ids = {b.booking_id for b in _matched(result)}
    return [
        line for line in result.invoice_lines if (line.booking_id in ids) is matched
    ]


def _line_values(line: TravelInvoiceLine) -> dict[str, Any]:
    return {
        "booking": line.booking_id,
        "reference": line.booking_ref,
        "invoice": line.invoice_no,
        "line": line.line_item,
        "billing": line.billing_type.value,
        "service": line.service_type,
        "issued": line.issue_date,
        "provider": line.service_provider,
        "supplier": line.supplier,
        "description": line.description,
        "pnr": line.pnr,
        "client": line.client_name,
        "route": line.route,
        "city": line.destination_city,
        "class": line.class_type,
        "check_in": line.check_in_text,
        "check_out": line.check_out_text,
        "dom_int": line.dom_int_label,
        "base_ex_vat": line.base_ex_vat,
        "vat_percent": line.vat_percent,
        "vat": line.vat,
        "cancel_fee": line.airline_cancel_fee,
        "total": line.total_invoice,
        "commission": line.commission,
        "net_payable": line.net_payable,
        "currency": line.currency,
        "source": _source(line.source, line.sheet, line.row),
    }


def _headers(sheet: Any, fmt: dict[str, Any], columns: Sequence[Column]) -> None:
    table(sheet, fmt, [c[1] for c in columns], [c[2] for c in columns])


def _write_values(
    sheet: Any,
    fmt: dict[str, Any],
    row: int,
    columns: Sequence[Column],
    values: dict[str, Any],
    *,
    styles: dict[str, str] | None = None,
) -> None:
    """Write one row by column key; a key that is absent or None stays blank."""
    for col, (key, _title, _width, kind) in enumerate(columns):
        value = values.get(key)
        if value is None:
            continue
        style = (styles or {}).get(key)
        if kind == "money":
            sheet.write(row, col, num(value), fmt[style or "money"])
        elif kind == "date":
            sheet.write(row, col, value, fmt["date"])
        elif kind == "number":
            sheet.write(row, col, num(value))
        else:
            sheet.write(row, col, value, fmt[style] if style else None)


def _write_totals(
    sheet: Any,
    fmt: dict[str, Any],
    row: int,
    columns: Sequence[Column],
    rows: list[dict[str, Any]],
) -> None:
    """A computed total under every money column."""
    sheet.write(row, 0, f"Total, {len(rows)} rows", fmt["label"])
    for col, (key, _title, _width, kind) in enumerate(columns):
        if kind == "money":
            total = _total(cells[key] for cells in rows if cells.get(key) is not None)
            sheet.write(row, col, num(total), fmt["money_bold"])


def _exceptions_by_booking(result: TravelResult) -> dict[str, list[ReconException]]:
    found: dict[str, list[ReconException]] = {}
    for exc in result.exceptions:
        if exc.order_no:
            found.setdefault(exc.order_no, []).append(exc)
    return found


def _reason_label(booking: BookingRecon) -> str:
    reason = booking.unmatched_reason
    return REASON_LABELS[reason] if reason else "Matched"


def _found_in(booking: BookingRecon) -> str:
    on_claim = booking.vendor_lines > 0 or booking.vendor_adjustment != 0
    if booking.portal_rows and on_claim:
        return "Both"
    return "Portal" if booking.portal_rows else "Claim"


def _source(source: str, sheet: str, row: int) -> str:
    return f"{source} · {sheet} row {row}"


def _total(values: Any) -> Decimal:
    return sum(values, Decimal(0))


def _date(value: Any) -> Any:
    return value if value is not None else ""
