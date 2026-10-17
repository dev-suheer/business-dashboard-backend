from datetime import date
from decimal import Decimal

import pytest

from app.recon.cleaning import (
    clean_text,
    money,
    normalize_header,
    strip_format_chars,
    to_date,
    to_decimal,
)


@pytest.mark.parametrize(
    "raw",
    [
        "‫البيان‬",  # RLE ... PDF
        "‫البيان‬‏",  # plus a trailing RLM
        "‪البيان‬",
        "‎البيان‏",
    ],
)
def test_strips_every_bidi_wrapper(raw: str) -> None:
    assert strip_format_chars(raw) == "البيان"


def test_order_number_survives_bidi_wrapping() -> None:
    assert clean_text("‫-SG-SH-01397# طلب‬") == "-SG-SH-01397# طلب"


def test_header_normalisation_ignores_internal_whitespace() -> None:
    # The same header is written both ways across the vendor's pages.
    assert normalize_header("الرصيد الحالي") == normalize_header("الرصيدالحالي")
    # And English headers arrive with stray trailing spaces.
    assert normalize_header("Com % ") == normalize_header("com%")
    assert normalize_header("Date") == normalize_header("date")


@pytest.mark.parametrize("token", ["-", "--", "---", "", "  ", "n/a", "None"])
def test_converter_placeholders_become_none(token: str) -> None:
    assert clean_text(token) is None
    assert to_decimal(token) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,885.00", Decimal("1885.00")),
        ("1885", Decimal("1885")),
        (1885, Decimal("1885")),
        (0.1, Decimal("0.1")),  # not the binary expansion of 0.1
        ("(238.85)", Decimal("-238.85")),
        ("-238.85", Decimal("-238.85")),
        ("15%", Decimal("0.15")),
    ],
)
def test_numeric_coercion(raw: object, expected: Decimal) -> None:
    assert to_decimal(raw) == expected


def test_booleans_are_not_numbers() -> None:
    assert to_decimal(True) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (46225, date(2026, 7, 22)),  # Excel serial, as the portal export sends it
        (46206, date(2026, 7, 3)),
        ("12 May, 2026", date(2026, 5, 12)),  # discount sheet
        ("2026/08/12", date(2026, 8, 12)),  # vendor statement
        ("2026-08-12", date(2026, 8, 12)),
        (date(2026, 8, 12), date(2026, 8, 12)),
    ],
)
def test_date_coercion_across_all_three_shapes(raw: object, expected: date) -> None:
    assert to_date(raw) == expected


def test_excel_serial_epoch_matches_the_portal_data() -> None:
    # 46225 is the first in-cycle order date in the sample export. An epoch off
    # by one would shift every order relative to the promo cutoff, so this is
    # what anchors the 15% and 10% bases in test_golden.
    assert to_date(46225) == date(2026, 7, 22)
    assert to_date(46206) == date(2026, 7, 3)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.035", Decimal("10.04")),  # half up, not banker's rounding
        ("10.045", Decimal("10.05")),
        ("-10.035", Decimal("-10.04")),
        ("2246.8465", Decimal("2246.85")),
    ],
)
def test_money_rounds_half_up(raw: str, expected: Decimal) -> None:
    assert money(Decimal(raw)) == expected
