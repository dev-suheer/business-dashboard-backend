"""The formula language: Excel-like, exact with money, and never eval."""

from datetime import date
from decimal import Decimal

import pytest

from app.expr import FormulaError, Values, evaluate, parse, references

ROW = {
    "portal": {"total_amount": Decimal("53.50"), "order_status": "Delivered"},
    "vendor": {"total": Decimal("57.50"), "customer_name": None},
    "missing": None,
    "when": date(2026, 9, 1),
}


def ev(formula: str, values: dict | None = None) -> object:
    return evaluate(formula, Values(values if values is not None else ROW))


def test_arithmetic_is_decimal_and_follows_precedence() -> None:
    assert ev("portal.total_amount - vendor.total") == Decimal("-4.00")
    assert ev("1 + 2 * 3") == Decimal(7)
    assert ev("(1 + 2) * 3") == Decimal(9)
    assert ev("-portal.total_amount") == Decimal("-53.50")
    assert ev("0.1 + 0.2") == Decimal("0.3")


def test_a_blank_spreads_through_arithmetic_until_n_stops_it() -> None:
    assert ev("missing.total - 1") is None
    assert ev("N(missing.total) - 1") == Decimal(-1)
    assert ev("vendor.customer_name") is None


def test_text_compares_without_case_and_joins_with_ampersand() -> None:
    assert ev('LOWER(portal.order_status) = "delivered"') is True
    assert ev('portal.order_status = "DELIVERED"') is True
    assert ev('EXACT(portal.order_status, "DELIVERED")') is False
    assert ev('"Total " & PLAIN(portal.total_amount) & "."') == "Total 53.50."
    assert ev('"[" & missing & "]"') == "[]"


def test_if_and_or_only_evaluate_what_they_need() -> None:
    assert ev("IF(0 = 0, 1, 1 / 0)") == Decimal(1)
    assert ev("AND(FALSE, 1 / 0)") is False
    assert ev("OR(TRUE, 1 / 0)") is True
    assert ev('IFS(1 > 2, "a", 2 > 1, "b")') == "b"
    with pytest.raises(FormulaError, match="Division by zero"):
        ev("1 / 0")


def test_functions_for_money_and_text() -> None:
    assert ev("MONEY(2.345)") == Decimal("2.35")
    assert ev("MONEY(-2.345)") == Decimal("-2.35")
    assert ev("ABS(-4)") == Decimal(4)
    assert ev("PERCENT(0.0345)") == "3.45%"
    assert ev('TEXTBEFORE("A261_99", "_")') == "A261"
    assert ev('IN("Canceled", "cancelled", "canceled")') is True
    assert (
        ev(
            'REGEX("Portal status Cancelled is not Delivered.", "status (.+) is not", 1)'
        )
        == "Cancelled"
    )
    assert ev('COALESCE(missing, "", "x")') == "x"
    assert ev("TEXT(when)") == "2026-09-01"


def test_aggregates_run_a_formula_over_a_named_set() -> None:
    rows = [
        Values({"due": Decimal("1.10")}),
        Values({"due": None}),
        Values({"due": Decimal(2)}),
    ]
    scope = Values({}, collections=lambda name: rows if name == "settled" else [])
    assert evaluate('SUMX("settled", N(due))', scope) == Decimal("3.10")
    assert evaluate('COUNTX("settled")', scope) == Decimal(3)
    assert evaluate('COUNTX("settled", NOT(ISBLANK(due)))', scope) == Decimal(2)
    assert evaluate('MAXX("settled", due)', scope) == Decimal(2)
    assert evaluate('ONLYX("settled", due)', scope) is None


def test_mistakes_are_caught_when_the_formula_is_read() -> None:
    with pytest.raises(FormulaError, match="Unknown function SUMIFF"):
        parse("SUMIFF(a, b)")
    with pytest.raises(FormulaError, match="IF takes 2 to 3"):
        parse("IF(a)")
    with pytest.raises(FormulaError):
        parse("1 +")
    with pytest.raises(FormulaError, match="Unknown name 'nope'"):
        ev("nope + 1")


def test_lineage_lists_every_name_a_formula_reads() -> None:
    assert references(
        parse("IF(ABS(N(amount_difference)) > param.tolerance, portal.total, 0)")
    ) == {
        "amount_difference",
        "param.tolerance",
        "portal.total",
    }


ORDERS = [
    Values({"status": "Delivered", "due": Decimal(10), "on": date(2026, 8, 1)}),
    Values({"status": "delivered", "due": Decimal(5), "on": date(2026, 8, 9)}),
    Values({"status": "Cancelled", "due": Decimal(7), "on": date(2026, 8, 20)}),
    Values({"status": "Food: late", "due": None, "on": None}),
    Values({"status": None, "due": Decimal(1), "on": date(2026, 8, 31)}),
]


def ex(formula: str, values: dict | None = None) -> object:
    scope = Values(
        values or {}, collections=lambda name: ORDERS if name == "orders" else None
    )
    return evaluate(formula, scope)


def test_sumif_and_countif_take_excel_criteria() -> None:
    # A plain value is "equal", whatever the case; the amount defaults to the value.
    assert ex('SUMIF("orders", status, "DELIVERED", due)') == Decimal(15)
    assert ex('COUNTIF("orders", status, "delivered")') == Decimal(2)
    assert ex('SUMIF("orders", due, ">5")') == Decimal(17)
    # <> keeps what differs, blanks included; "" and "<>" ask for blank and not.
    assert ex('COUNTIF("orders", status, "<>Cancelled")') == Decimal(4)
    assert ex('COUNTIF("orders", status, "")') == Decimal(1)
    assert ex('COUNTIF("orders", status, "<>")') == Decimal(4)
    # Numbers and dates compare as numbers and dates; text and blanks never do.
    assert ex('COUNTIF("orders", due, ">=5")') == Decimal(3)
    assert ex('COUNTIF("orders", due, "<5")') == Decimal(1)
    assert ex('COUNTIF("orders", on, ">=2026-08-09")') == Decimal(3)
    assert ex('COUNTIF("orders", due, 7)') == Decimal(1)
    # Wildcards: * a run, ? one character, ~ the character itself.
    assert ex('COUNTIF("orders", status, "deliver*")') == Decimal(2)
    assert ex('COUNTIF("orders", status, "Food?*")') == Decimal(1)
    assert ex('COUNTIF("orders", status, "Food~*")') == Decimal(0)
    # The criterion is worked out where the formula is, once.
    assert ex(
        'SUMIF("orders", status, wanted, due)', {"wanted": "Cancelled"}
    ) == Decimal(7)


def test_sumifs_and_countifs_need_every_criteria() -> None:
    assert ex(
        'SUMIFS("orders", due, status, "delivered", on, ">2026-08-01")'
    ) == Decimal(5)
    assert ex('COUNTIFS("orders", status, "<>", due, ">1")') == Decimal(3)
    assert ex('COUNTIFS("orders", due, ">0")') == Decimal(4)
    with pytest.raises(FormulaError, match="SUMIFS takes value, criteria pairs"):
        ex('SUMIFS("orders", due, status, "a", on)')


def test_sumif_adds_up_numbers_only() -> None:
    with pytest.raises(FormulaError, match="SUMIF needs a number"):
        ex('SUMIF("orders", status, "<>", status)')
    with pytest.raises(FormulaError, match="Unknown set 'nope'"):
        ex('COUNTIF("nope", status, "x")')


def test_criteria_read_numbers_as_people_write_them() -> None:
    amounts = [Values({"n": Decimal(v)}) for v in ("0.25", "0.75", "1500", "40")]
    scope = Values({}, collections=lambda name: amounts if name == "a" else None)
    count = lambda criteria: evaluate(f'COUNTIF("a", n, "{criteria}")', scope)  # noqa: E731
    assert count(">.5") == Decimal(3)
    assert count(">=1,000") == Decimal(1)
    assert count(">50%") == Decimal(3)
    assert count("<1E2") == Decimal(3)
    assert count("=1,500") == Decimal(1)


def test_a_criterion_that_is_not_a_number_against_numbers_says_so() -> None:
    with pytest.raises(
        FormulaError, match=r"COUNTIF: '>abc' is not a number or a date"
    ):
        ex('COUNTIF("orders", due, ">abc")')
    with pytest.raises(FormulaError, match=r"SUMIFS: '>=01/08/2026' is not a number"):
        ex('SUMIFS("orders", due, on, ">=01/08/2026")')
    # Text against text still compares as text.
    assert ex('COUNTIF("orders", status, ">c")') == Decimal(4)


def test_wildcards_match_text_only_and_a_blank_criterion_matches_blanks() -> None:
    assert ex('COUNTIF("orders", due, "1*")') == Decimal(0)
    assert ex('COUNTIF("orders", status, "*")') == Decimal(4)
    assert ex('COUNTIF("orders", status, missing)', {"missing": None}) == Decimal(1)
