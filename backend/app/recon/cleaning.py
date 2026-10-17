"""Value coercion for spreadsheets that arrive from a PDF converter.

Everything monetary is a :class:`~decimal.Decimal`; ``float`` never appears.
"""

import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

# Excel's day zero. Serial 1 is 1900-01-01, and Excel's phantom 1900-02-29 means
# this origin is only correct for serials of 61 and above — which every date in
# this domain comfortably is.
_EXCEL_EPOCH = date(1899, 12, 30)
_EXCEL_LEAP_BUG_CUTOFF = 61

# The converter writes these instead of leaving a cell empty.
_NULL_TOKENS = frozenset({"", "-", "--", "---", "n/a", "na", "none", "null"})

# Comma, ARABIC THOUSANDS SEPARATOR, THIN SPACE, NO-BREAK SPACE: all four are
# used as digit groupers in the source files.
_THOUSANDS = re.compile("[,\u066c\u2009\u00a0]")
_WHITESPACE = re.compile(r"\s+")

CENTS = Decimal("0.01")

_DATE_FORMATS = (
    "%Y/%m/%d",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d %B, %Y",
    "%d %b, %Y",
    "%d %B %Y",
    "%d %b %Y",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
)


def strip_format_chars(value: str) -> str:
    """Drop every Unicode ``Cf`` character.

    The converter wraps text in bidirectional controls — U+202B RLE, U+202C PDF,
    U+200F RLM and friends — which are invisible but break every regex and
    equality test. Removing the whole category is the only reliable defence.
    """
    return "".join(ch for ch in value if unicodedata.category(ch) != "Cf")


def clean_text(value: Any) -> str | None:
    """Normalise a cell to trimmed text, or ``None`` if it carries no value."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    text = _WHITESPACE.sub(
        " ", strip_format_chars(unicodedata.normalize("NFKC", value))
    ).strip()
    if text.casefold() in _NULL_TOKENS:
        return None
    return text or None


def normalize_header(value: Any) -> str:
    """Collapse a header to a comparable key.

    All whitespace is removed rather than merely collapsed, because the same
    header appears as both ``الرصيد الحالي`` and ``الرصيدالحالي`` across sheets,
    and English headers arrive with stray trailing spaces (``"Com % "``).
    """
    if value is None:
        return ""
    text = strip_format_chars(unicodedata.normalize("NFKC", str(value)))
    return _WHITESPACE.sub("", text).casefold()


def to_decimal(value: Any) -> Decimal | None:
    """Coerce a cell to ``Decimal``, or ``None`` when it holds no number.

    Handles thousands separators (``"1,885.00"``), the converter's ``"-"``
    placeholders, and accountancy negatives (``"(238.85)"``).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # str() first: Decimal(float) would preserve binary rounding error.
        return Decimal(str(value))

    text = clean_text(value)
    if text is None:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    # \u066b is the ARABIC DECIMAL SEPARATOR.
    text = _THOUSANDS.sub("", text).replace("\u066b", ".").lstrip("+")
    if text.endswith("%"):
        try:
            return Decimal(text[:-1]) / Decimal(100)
        except InvalidOperation:
            return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    return -amount if negative else amount


def to_date(value: Any) -> date | None:
    """Coerce a cell to a ``date``.

    Three shapes occur in the source workbooks: a real datetime, an Excel serial
    number (``46225``) when the column carries no date format, and text in
    either ``YYYY/MM/DD`` or ``12 May, 2026`` form.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, int | float | Decimal):
        return _from_excel_serial(Decimal(str(value)))

    text = clean_text(value)
    if text is None:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # A serial that survived as text.
    serial = to_decimal(text)
    if serial is not None:
        return _from_excel_serial(serial)
    return None


def _from_excel_serial(serial: Decimal) -> date | None:
    days = int(serial)
    if days < _EXCEL_LEAP_BUG_CUTOFF:
        return None
    return _EXCEL_EPOCH + timedelta(days=days)


def money(value: Decimal) -> Decimal:
    """Quantize to 2dp, half up.

    The single rounding point in the engine: every monetary boundary goes
    through here so results cannot drift between call sites.
    """
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)
