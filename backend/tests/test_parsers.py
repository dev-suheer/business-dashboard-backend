import io
import zipfile
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.recon.categories.eshop.parser import (
    LEDGER_SCHEMA,
    PORTAL_SCHEMA,
    PROMO_SCHEMA,
    classify_doc_type,
    extract_order_no,
    read_ledger,
    read_portal,
    read_promo,
)
from app.recon.excel import (
    HEADER_SCAN_LIMIT,
    SheetFormatError,
    detect_sheet,
    detect_sheets,
    find_header,
    open_workbook,
)
from app.recon.models import LedgerDocType, LedgerEntry, OrderStatus
from tests.conftest import VENDOR_STATEMENT, WORKINGS

MAX_ROWS = 1_000_000


def _damaged(kind: str) -> bytes:
    if kind == "not a zip":
        return b"not a workbook at all"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as damaged:
        if kind == "no workbook inside":
            damaged.writestr("notes.txt", "hello")
        else:
            with zipfile.ZipFile(VENDOR_STATEMENT) as source:
                for item in source.infolist():
                    data = source.read(item.filename)
                    if kind == "broken part" and item.filename == "xl/workbook.xml":
                        data = b"<broken"
                    # Cut off partway: it opens, and fails only once it is read.
                    if (
                        kind == "broken sheet"
                        and item.filename == "xl/worksheets/sheet1.xml"
                    ):
                        data = data[: len(data) * 2 // 3]
                    damaged.writestr(item, data)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "kind", ["not a zip", "no workbook inside", "broken part", "broken sheet"]
)
def test_a_damaged_workbook_is_refused_by_field_name(kind: str, tmp_path: Path) -> None:
    """Uploads are spooled as <field>.xlsx, so the reason names the field."""
    path = tmp_path / "wallet.xlsx"
    path.write_bytes(_damaged(kind))
    with pytest.raises(SheetFormatError, match="'wallet' is not a readable"):
        with open_workbook(path) as workbook:
            for _row in workbook.worksheets[0].iter_rows(values_only=True):
                pass


def test_a_fault_while_reading_is_not_blamed_on_the_file() -> None:
    """Only a damaged file is reported as one; a bug in the reader still surfaces."""
    with pytest.raises(KeyError):
        with open_workbook(VENDOR_STATEMENT):
            raise KeyError("not the file's fault")


# --- order-number extraction (plan.md 3.6) ---------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("‫-SG-SH-01397# طلب‬", "SG-SH-01397"),
        ("Turki Aldhry- SG-SH-01990", "SG-SH-01990"),
        ("SG-SH-05488", "SG-SH-05488"),
        ("طلب SG-SH-12196 عميل", "SG-SH-12196"),
        ("sg-sh-05488", "SG-SH-05488"),
        ("no order here", None),
        (None, None),
    ],
)
def test_extract_order_no(raw: str | None, expected: str | None) -> None:
    assert extract_order_no(raw) == expected


def test_truncated_reference_is_never_treated_as_an_order_number() -> None:
    # The vendor's reference column cuts SG-SH-05785 down to SG-SH-0578.
    assert extract_order_no("SG-SH-0578") == "SG-SH-0578"
    assert extract_order_no("SG-SH-0578") != "SG-SH-05785"


# --- document typing --------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("فاتورة بيع أجل", LedgerDocType.SALES_INVOICE),
        # The return type contains the invoice type, so ordering matters.
        ("فاتورة مردود بيع أجل", LedgerDocType.SALES_RETURN),
        ("‫فاتورة مردود بيع أجل‬", LedgerDocType.SALES_RETURN),
        ("قيد يومية", LedgerDocType.JOURNAL_ENTRY),
        # The vendor template's dropdown values pair the Arabic with English.
        ("Sales Invoice - فاتورة بيع أجل", LedgerDocType.SALES_INVOICE),
        ("Sales Return - فاتورة مردود بيع أجل", LedgerDocType.SALES_RETURN),
        ("Commission - قيد عمولة", LedgerDocType.JOURNAL_ENTRY),
        ("Payment Received - قيد سداد", LedgerDocType.JOURNAL_ENTRY),
        # English alone, as when a value is typed or pasted rather than picked.
        # "Sales Return Invoice" holds "invoice", so the return test comes first.
        ("Sales Return Invoice", LedgerDocType.SALES_RETURN),
        ("credit note", LedgerDocType.SALES_RETURN),
        ("SALES INVOICE", LedgerDocType.SALES_INVOICE),
        ("Journal Entry", LedgerDocType.JOURNAL_ENTRY),
        ("Commission", LedgerDocType.JOURNAL_ENTRY),
        ("Payment received", LedgerDocType.JOURNAL_ENTRY),
        ("something else", LedgerDocType.UNKNOWN),
        (None, LedgerDocType.UNKNOWN),
    ],
)
def test_classify_doc_type(raw: str | None, expected: LedgerDocType) -> None:
    assert classify_doc_type(raw) == expected


# --- sheet detection --------------------------------------------------------


def test_each_role_picks_its_own_sheet_from_one_workbook() -> None:
    with open_workbook(WORKINGS) as workbook:
        assert detect_sheet(workbook, PORTAL_SCHEMA) == "E-SHop 1 Vendor "
        assert detect_sheet(workbook, PROMO_SCHEMA) == "Discount Base Sheet"


def test_the_legacy_recon_tab_is_not_mistaken_for_an_input() -> None:
    # 'RM eshp RECON' carries the portal columns and the promo columns bolted
    # together, so without a disqualifier it would satisfy either fingerprint.
    with open_workbook(WORKINGS) as workbook:
        assert detect_sheets(workbook, PORTAL_SCHEMA) == ["E-SHop 1 Vendor "]
        assert detect_sheets(workbook, PROMO_SCHEMA) == ["Discount Base Sheet"]


def test_other_categories_sheets_are_ignored() -> None:
    with open_workbook(WORKINGS) as workbook:
        for schema in (PORTAL_SCHEMA, PROMO_SCHEMA, LEDGER_SCHEMA):
            assert "Food Analysis" not in detect_sheets(workbook, schema)
            assert "Food Vendor Report 7-12" not in detect_sheets(workbook, schema)


def test_a_workbook_without_the_role_is_reported_not_guessed() -> None:
    with open_workbook(VENDOR_STATEMENT) as workbook:
        assert detect_sheet(workbook, PORTAL_SCHEMA) is None


# --- header location --------------------------------------------------------


def test_ledger_header_is_found_on_row_four() -> None:
    # Rows 1-3 hold Column1..Column9 and the account metadata.
    with open_workbook(VENDOR_STATEMENT) as workbook:
        match = find_header(workbook["Table002 (Page 1)"], LEDGER_SCHEMA)
        assert match is not None
        assert match.row == 4
        assert HEADER_SCAN_LIMIT >= 4


def test_ledger_columns_shift_between_pages() -> None:
    """Page 1 and page 2 place the same fields in different columns."""
    with open_workbook(VENDOR_STATEMENT) as workbook:
        page1 = find_header(workbook["Table002 (Page 1)"], LEDGER_SCHEMA)
        page2 = find_header(workbook["Table004 (Page 2)"], LEDGER_SCHEMA)
    assert page1 is not None and page2 is not None
    # Same logical field, different physical column — which is exactly why
    # nothing may be mapped by position.
    assert page1.columns["raw_ref"] == 4
    assert page2.columns["raw_ref"] == 3
    assert page1.columns["doc_date"] == 8
    assert page2.columns["doc_date"] == 7


def test_balance_header_is_matched_without_its_space() -> None:
    # The sheets write it as الرصيدالحالي; the spec documents الرصيد الحالي.
    with open_workbook(VENDOR_STATEMENT) as workbook:
        match = find_header(workbook["Sheet1"], LEDGER_SCHEMA)
    assert match is not None
    assert "balance" in match.columns


# --- portal export ----------------------------------------------------------


def test_portal_export_parses_to_the_expected_shape() -> None:
    with open_workbook(WORKINGS) as workbook:
        lines = list(read_portal(workbook, "E-SHop 1 Vendor ", max_rows=MAX_ROWS))

    assert len(lines) == 75
    assert len({line.order_no for line in lines}) == 37
    assert Counter(line.status for line in lines) == {
        OrderStatus.DELIVERED: 71,
        OrderStatus.RTO: 3,
        OrderStatus.RETURN: 1,
    }


def test_trailing_summary_rows_are_skipped() -> None:
    """Rows 80-81 restate the adjustments with no order number."""
    with open_workbook(WORKINGS) as workbook:
        lines = list(read_portal(workbook, "E-SHop 1 Vendor ", max_rows=MAX_ROWS))
    # -238.85 and -108 appear in those rows; neither may become an order line.
    assert all(line.gmv >= 0 for line in lines)
    assert len(lines) == 75


def test_portal_dates_are_read_from_excel_serials() -> None:
    with open_workbook(WORKINGS) as workbook:
        lines = list(read_portal(workbook, "E-SHop 1 Vendor ", max_rows=MAX_ROWS))
    assert min(line.order_date for line in lines) == date(2026, 7, 3)
    assert max(line.order_date for line in lines) == date(2026, 8, 25)


def test_prior_settlement_is_read_from_the_paid_column() -> None:
    """The clawback's source, which makes the settlement upload optional."""
    with open_workbook(WORKINGS) as workbook:
        paid = {
            line.order_no: line.amount_paid
            for line in read_portal(workbook, "E-SHop 1 Vendor ", max_rows=MAX_ROWS)
            if line.amount_paid
        }
    assert paid == {"SG-SH-03344": Decimal("238.85")}


def test_row_limit_is_enforced() -> None:
    with open_workbook(WORKINGS) as workbook:
        with pytest.raises(SheetFormatError, match="row limit"):
            list(read_portal(workbook, "E-SHop 1 Vendor ", max_rows=5))


# --- vendor statement -------------------------------------------------------


def test_multi_sheet_ledger_deduplicates_to_64() -> None:
    """Four sheets hold overlapping copies of the same transactions."""
    with open_workbook(VENDOR_STATEMENT) as workbook:
        sheets = detect_sheets(workbook, LEDGER_SCHEMA)
        entries = list(read_ledger(workbook, sheets, max_rows=MAX_ROWS))

    assert len(sheets) == 4
    assert len(entries) == 64
    assert Counter(e.doc_type for e in entries) == {
        LedgerDocType.SALES_INVOICE: 58,
        LedgerDocType.SALES_RETURN: 4,
        LedgerDocType.JOURNAL_ENTRY: 2,
    }


def test_dedup_survives_a_number_written_as_text() -> None:
    """One sheet stores 82.00 as text and another as the number 82."""
    with open_workbook(VENDOR_STATEMENT) as workbook:
        single = list(read_ledger(workbook, ["Sheet1"], max_rows=MAX_ROWS))
        both = list(read_ledger(workbook, ["Sheet1", "Sheet1 (2)"], max_rows=MAX_ROWS))
    assert len(single) == len(both) == 64


def test_dedup_keeps_same_day_invoices_for_different_orders_apart() -> None:
    """A template row may leave the document number blank; the order separates them."""
    first, second = (
        LedgerEntry(
            doc_type=LedgerDocType.SALES_INVOICE,
            doc_date=date(2026, 8, 5),
            debit=Decimal("108.00"),
            order_no=order_no,
        )
        for order_no in ("SG-SH-06115", "SG-SH-07435")
    )
    assert first.dedup_key() != second.dedup_key()


def test_credit_notes_carry_no_order_number_in_their_description() -> None:
    """Which is why they must be resolved by reference prefix plus amount."""
    with open_workbook(VENDOR_STATEMENT) as workbook:
        sheets = detect_sheets(workbook, LEDGER_SCHEMA)
        entries = list(read_ledger(workbook, sheets, max_rows=MAX_ROWS))
    returns = [e for e in entries if e.doc_type is LedgerDocType.SALES_RETURN]
    assert len(returns) == 4
    assert all(e.order_no is None for e in returns)
    assert all(e.raw_ref and len(e.raw_ref) == 10 for e in returns)


# --- promo catalogue --------------------------------------------------------


def test_promo_catalogue_holds_100_unique_skus() -> None:
    with open_workbook(WORKINGS) as workbook:
        promos = list(read_promo(workbook, "Discount Base Sheet", max_rows=MAX_ROWS))
    assert len(promos) == 100
    assert len({p.sku for p in promos}) == 100
