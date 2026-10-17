# Every vendor-facing string pairs English with Arabic, which RUF001 reads as
# confusable characters.
# ruff: noqa: RUF001
"""The eShop vendor statement template, handed to vendors to fill in.

A vendor's own statement arrives as a PDF run through a converter, and most of
the ledger parser exists to undo what that does to it. The template is the same
ledger in a shape that needs none of it: one sheet, the parser's own Arabic
headers, the full order number on every invoice and return, and row checks that
tell the vendor what is wrong before the file is ever sent.

Write it with ``python -m app.recon.categories.eshop.template <output.xlsx>``.
"""

import argparse
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import xlsxwriter

from app.recon.categories.eshop.parser import LEDGER_SCHEMA
from app.recon.xlsx import MONEY

TEMPLATE_VERSION = "1.0"

INSTRUCTIONS_SHEET = "Instructions - التعليمات"
STATEMENT_SHEET = "Statement - كشف الحساب"

# 1-based, as the vendor sees them in Excel and as the instructions name them.
# The header must stay inside the parser's HEADER_SCAN_LIMIT.
LABEL_ROW = 9
HEADER_ROW = 10
FIRST_DATA_ROW = 11
DATA_ROWS = 2000
LAST_DATA_ROW = FIRST_DATA_ROW + DATA_ROWS - 1

# The metadata cell the running balance starts from.
OPENING_BALANCE = "$C$7"

ROW_OK = "OK ✓"

DATE_FORMAT = "yyyy/mm/dd"

# Each value carries the Arabic wording classify_doc_type keys on, so a picked
# value parses exactly as the vendor's own system's wording does. The English
# half is what the row check searches for.
SALES_INVOICE = "Sales Invoice - فاتورة بيع أجل"
SALES_RETURN = "Sales Return - فاتورة مردود بيع أجل"
COMMISSION = "Commission - قيد عمولة"
PAYMENT = "Payment Received - قيد سداد"
DOC_TYPES = (SALES_INVOICE, SALES_RETURN, COMMISSION, PAYMENT)


def _schema_header(field: str) -> str:
    """The header text the parser recognises for ``field``.

    Taken from the schema rather than copied, so the template cannot drift from
    what the parser reads.
    """
    return next(spec for spec in LEDGER_SCHEMA.columns if spec.field == field).aliases[
        0
    ]


@dataclass(frozen=True)
class _Column:
    english: str
    width: int
    # The ledger field this column feeds; None for a template-only column.
    field: str | None = None
    arabic: str = ""

    @property
    def header(self) -> str:
        return _schema_header(self.field) if self.field else self.arabic


# Order matters: the row formulas below address these as columns A to I.
COLUMNS = (
    _Column("Date", 13, field="doc_date"),
    _Column("Document Type", 34, field="doc_type"),
    _Column("Document No.", 14, field="doc_no"),
    _Column("Order Number", 16, field="order_no"),
    _Column("Description", 34, field="raw_desc"),
    _Column("Debit (Sales invoices)", 16, field="debit"),
    _Column("Credit (Returns, commission, payments)", 22, field="credit"),
    _Column("Running Balance (auto)", 17, field="balance"),
    _Column("Row Check (auto)", 52, arabic="التحقق"),
)
_DATE, _TYPE, _DOC_NO, _ORDER, _DESC, _DEBIT, _CREDIT, _BALANCE, _CHECK = range(9)

# The row check reports the first of these that applies.
_MSG_DATE = "Enter the date / أدخل التاريخ"
_MSG_TYPE = "Choose the document type / اختر نوع المستند"
_MSG_DOC_NO = "Enter the document number / أدخل رقم المستند"
_MSG_ORDER = "Enter the full order number (SG-SH-…) / أدخل رقم الطلب كاملاً"
_MSG_AMOUNT = "Enter one amount: Debit or Credit / أدخل مبلغاً واحداً: مدين أو دائن"
_MSG_INVOICE_SIDE = "Sales invoices go in Debit / فواتير البيع تُسجل في مدين"
_MSG_CREDIT_SIDE = "This type goes in Credit / هذا النوع يُسجل في دائن"
_MSG_ORDERING = "Date is earlier than a row above - sort oldest first / رتّب من الأقدم"

_NAVY = "#1F3864"
_PALE_BLUE = "#D9E1F2"
_GREY = "#F2F2F2"
_BORDER = "#BFBFBF"


def build_vendor_template(path: Path) -> Path:
    book = xlsxwriter.Workbook(str(path))
    try:
        book.set_properties(
            {
                "title": "eShop Vendor Account Statement Template",
                "comments": f"Template version {TEMPLATE_VERSION}",
            }
        )
        fmt = _formats(book)
        # Added first, so it is the tab the vendor lands on.
        _instructions_sheet(book, fmt)
        _statement_sheet(book, fmt)
    finally:
        book.close()
    return path


def _formats(book: Any) -> dict[str, Any]:
    cell = {"border": 1, "border_color": _BORDER, "valign": "vcenter"}
    arabic = {"reading_order": 2, "align": "right"}
    wrap = {"text_wrap": True, "valign": "top"}
    section = {
        "bold": True,
        "font_size": 12,
        "font_color": _NAVY,
        "bottom": 2,
        "bottom_color": _NAVY,
        "valign": "vcenter",
    }
    return {
        "title": book.add_format(
            {"bold": True, "font_size": 16, "font_color": _NAVY, "valign": "vcenter"}
        ),
        "hint": book.add_format(
            {"italic": True, "font_color": "#595959", "valign": "vcenter"}
        ),
        "section": book.add_format(section),
        "section_ar": book.add_format({**section, **arabic}),
        "label": book.add_format({**cell, "bold": True, "bg_color": _GREY}),
        "header": book.add_format(
            {
                **cell,
                "bold": True,
                "bg_color": _NAVY,
                "font_color": "white",
                "align": "center",
                "text_wrap": True,
                "font_size": 12,
            }
        ),
        "header_en": book.add_format(
            {
                **cell,
                "bold": True,
                "bg_color": _PALE_BLUE,
                "font_color": _NAVY,
                "align": "center",
                "text_wrap": True,
            }
        ),
        # Data entry: unlocked, so the vendor can type here once the sheet is
        # protected. Everything else stays locked.
        "in_text": book.add_format({**cell, "locked": False, "num_format": "@"}),
        "in_date": book.add_format(
            {**cell, "locked": False, "num_format": DATE_FORMAT, "align": "center"}
        ),
        "in_money": book.add_format({**cell, "locked": False, "num_format": MONEY}),
        "in_any": book.add_format({**cell, "locked": False}),
        "calc_money": book.add_format({**cell, "bg_color": _GREY, "num_format": MONEY}),
        "calc_money_bold": book.add_format(
            {**cell, "bg_color": _GREY, "num_format": MONEY, "bold": True}
        ),
        "calc": book.add_format({**cell, "bg_color": _GREY}),
        "calc_int": book.add_format(
            {**cell, "bg_color": _GREY, "num_format": "0", "bold": True}
        ),
        "ok": book.add_format({"bg_color": "#C6EFCE", "font_color": "#006100"}),
        "bad": book.add_format({"bg_color": "#FFC7CE", "font_color": "#9C0006"}),
        # Instructions
        "en": book.add_format({**wrap}),
        "ar": book.add_format({**wrap, **arabic}),
        "step_no": book.add_format(
            {"bold": True, "font_color": _NAVY, "align": "center", "valign": "top"}
        ),
        "t_head": book.add_format(
            {**cell, **wrap, "bold": True, "bg_color": _PALE_BLUE, "font_color": _NAVY}
        ),
        "t_cell": book.add_format({**cell, **wrap}),
        "t_ar": book.add_format({**cell, **wrap, **arabic}),
        "t_date": book.add_format({**cell, "num_format": DATE_FORMAT, "valign": "top"}),
        "t_money": book.add_format({**cell, "num_format": MONEY, "valign": "top"}),
        "footer": book.add_format({"italic": True, "font_color": "#808080"}),
    }


# --- Statement sheet ---------------------------------------------------------


def _statement_sheet(book: Any, fmt: dict[str, Any]) -> None:
    sheet = book.add_worksheet(STATEMENT_SHEET)
    sheet.set_tab_color("#2E7D32")
    for col, column in enumerate(COLUMNS):
        sheet.set_column(col, col, column.width)

    last_col = len(COLUMNS) - 1
    sheet.set_row(0, 26)
    sheet.merge_range(
        0,
        0,
        0,
        last_col,
        "eShop Vendor Account Statement  |  كشف حساب المورد - المتجر الإلكتروني",
        fmt["title"],
    )
    sheet.merge_range(
        1,
        0,
        1,
        last_col,
        "Fill in the white cells only - grey cells calculate automatically. "
        "Read the Instructions sheet first.   |   "
        "املأ الخانات البيضاء فقط - الخانات الرمادية تُحتسب تلقائياً. "
        "اقرأ ورقة التعليمات أولاً.",
        fmt["hint"],
    )

    _metadata(sheet, fmt)

    label, header = LABEL_ROW - 1, HEADER_ROW - 1
    sheet.set_row(label, 32)
    sheet.set_row(header, 22)
    for col, column in enumerate(COLUMNS):
        sheet.write(label, col, column.english, fmt["header_en"])
        # The row the parser reads.
        sheet.write(header, col, column.header, fmt["header"])

    for row in range(FIRST_DATA_ROW - 1, LAST_DATA_ROW):
        _data_row(sheet, fmt, row)

    _validations(sheet)

    check_range = f"I{FIRST_DATA_ROW}:I{LAST_DATA_ROW}"
    sheet.conditional_format(
        check_range,
        {"type": "cell", "criteria": "==", "value": f'"{ROW_OK}"', "format": fmt["ok"]},
    )
    sheet.conditional_format(
        check_range,
        {
            "type": "formula",
            "criteria": f'=AND($I{FIRST_DATA_ROW}<>"",$I{FIRST_DATA_ROW}<>"{ROW_OK}")',
            "format": fmt["bad"],
        },
    )

    sheet.freeze_panes(HEADER_ROW, 0)
    sheet.set_selection(FIRST_DATA_ROW - 1, 0, FIRST_DATA_ROW - 1, 0)
    sheet.set_landscape()
    sheet.set_paper(9)  # A4
    sheet.fit_to_pages(1, 0)
    sheet.repeat_rows(label, header)
    # No password: this guards the headers and formulas against accidents, not
    # against a vendor who means to change them.
    sheet.protect("", {"format_columns": True, "format_rows": True})


def _metadata(sheet: Any, fmt: dict[str, Any]) -> None:
    """The statement's header block, rows 3 to 7. The parser never reads it."""
    inputs = (
        ("Vendor name / اسم المورد", "in_any"),
        ("Merchant ID / رقم التاجر", "in_text"),
        ("Statement from / من تاريخ", "in_date"),
        ("Statement to / إلى تاريخ", "in_date"),
        ("Opening balance / الرصيد الافتتاحي", "in_money"),
    )
    first, last = FIRST_DATA_ROW, LAST_DATA_ROW
    totals = (
        ("Total debit / إجمالي المدين", f"=SUM(F{first}:F{last})", "calc_money"),
        ("Total credit / إجمالي الدائن", f"=SUM(G{first}:G{last})", "calc_money"),
        (
            "Closing balance / الرصيد الختامي",
            f"={OPENING_BALANCE}+H3-H4",
            "calc_money_bold",
        ),
        (
            "Rows with issues / صفوف بها أخطاء",
            f'=SUMPRODUCT((I{first}:I{last}<>"")*(I{first}:I{last}<>"{ROW_OK}"))',
            "calc_int",
        ),
    )
    for offset, (text, style) in enumerate(inputs):
        row = 2 + offset
        sheet.set_row(row, 18)
        sheet.merge_range(row, 0, row, 1, text, fmt["label"])
        sheet.merge_range(row, 2, row, 4, "", fmt[style])
    for offset, (text, formula, style) in enumerate(totals):
        row = 2 + offset
        sheet.merge_range(row, 5, row, 6, text, fmt["label"])
        sheet.write_formula(row, 7, formula, fmt[style], 0)
    sheet.merge_range(6, 5, 6, 6, "Currency / العملة", fmt["label"])
    sheet.write(6, 7, "SAR - ريال سعودي", fmt["calc"])

    sheet.data_validation(
        "C5:C6",
        {
            "validate": "date",
            "criteria": "between",
            "minimum": date(2020, 1, 1),
            "maximum": date(2100, 12, 31),
            "error_title": "Invalid date / تاريخ غير صحيح",
            "error_message": "Enter a date as YYYY/MM/DD.\nأدخل التاريخ بالشكل YYYY/MM/DD.",
        },
    )
    sheet.data_validation(
        "C7",
        {
            "validate": "decimal",
            "criteria": "between",
            "minimum": -1_000_000_000,
            "maximum": 1_000_000_000,
            "input_title": "Opening balance",
            "input_message": (
                "Balance brought forward at the start of the period. 0 if none.\n"
                "الرصيد المرحّل في بداية الفترة، أو 0."
            ),
            "error_message": "Enter a number without currency.\nأدخل رقماً بدون عملة.",
        },
    )
    sheet.data_validation(
        "C3",
        {
            "validate": "any",
            "input_title": "Vendor name / اسم المورد",
            "input_message": "As registered with us.\nكما هو مسجل لدينا.",
        },
    )


def _data_row(sheet: Any, fmt: dict[str, Any], row: int) -> None:
    """One empty, formatted entry row with its two formulas.

    The formulas are written with a blank cached result, so a file read before
    any spreadsheet recalculates still shows the unused rows as empty — and the
    parser skips them as it skips any row with no type and no date.
    """
    r = row + 1
    for col, style in (
        (_DATE, "in_date"),
        (_TYPE, "in_any"),
        (_DOC_NO, "in_text"),
        (_ORDER, "in_text"),
        (_DESC, "in_any"),
        (_DEBIT, "in_money"),
        (_CREDIT, "in_money"),
    ):
        sheet.write_blank(row, col, None, fmt[style])
    sheet.write_formula(
        row,
        _BALANCE,
        # Running sums rather than "previous balance + this row", so a blank
        # row in between cannot break the chain.
        f'=IF(COUNTA(A{r}:G{r})=0,"",{OPENING_BALANCE}'
        f"+SUM($F${FIRST_DATA_ROW}:F{r})-SUM($G${FIRST_DATA_ROW}:G{r}))",
        fmt["calc_money"],
        "",
    )
    sheet.write_formula(row, _CHECK, _check_formula(r), fmt["calc"], "")


def _check_formula(r: int) -> str:
    """The row check for Excel row ``r``: blank, the first problem, or OK."""
    is_invoice = f'ISNUMBER(SEARCH("Invoice",B{r}))'
    is_return = f'ISNUMBER(SEARCH("Return",B{r}))'
    rules = [
        (f'A{r}=""', _MSG_DATE),
        (f'B{r}=""', _MSG_TYPE),
        (f'C{r}=""', _MSG_DOC_NO),
        (f'AND(D{r}="",OR({is_invoice},{is_return}))', _MSG_ORDER),
        (f"OR(COUNT(F{r}:G{r})<>1,SUM(F{r}:G{r})<=0)", _MSG_AMOUNT),
        (f'AND({is_invoice},G{r}<>"")', _MSG_INVOICE_SIDE),
        (f'AND(NOT({is_invoice}),F{r}<>"")', _MSG_CREDIT_SIDE),
    ]
    if r > FIRST_DATA_ROW:
        rules.append((f"A{r}<MAX($A${FIRST_DATA_ROW}:A{r - 1})", _MSG_ORDERING))

    formula = f'"{ROW_OK}"'
    for condition, message in reversed(rules):
        formula = f'IF({condition},"{message}",{formula})'
    return f'=IF(COUNTA(A{r}:G{r})=0,"",{formula})'


def _validations(sheet: Any) -> None:
    first, last = FIRST_DATA_ROW, LAST_DATA_ROW

    def col(letter: str) -> str:
        return f"{letter}{first}:{letter}{last}"

    sheet.data_validation(
        col("A"),
        {
            "validate": "date",
            "criteria": "between",
            "minimum": date(2020, 1, 1),
            "maximum": date(2100, 12, 31),
            "input_title": "Date / التاريخ",
            "input_message": "YYYY/MM/DD, e.g. 2026/08/03\nمثال: 2026/08/03",
            "error_title": "Invalid date / تاريخ غير صحيح",
            "error_message": "Enter a date as YYYY/MM/DD.\nأدخل التاريخ بالشكل YYYY/MM/DD.",
        },
    )
    sheet.data_validation(
        col("B"),
        {
            "validate": "list",
            "source": list(DOC_TYPES),
            "input_title": "Document type / نوع المستند",
            "input_message": "Pick from the list.\nاختر من القائمة.",
            "error_title": "Use the list / استخدم القائمة",
            "error_message": (
                "Choose a value from the dropdown list.\nاختر قيمة من القائمة المنسدلة."
            ),
        },
    )
    sheet.data_validation(
        col("C"),
        {
            "validate": "any",
            "input_title": "Document no. / رقم المستند",
            "input_message": (
                "Your invoice, return or journal number.\n"
                "رقم الفاتورة أو المردود أو القيد في نظامكم."
            ),
        },
    )
    sheet.data_validation(
        col("D"),
        {
            "validate": "custom",
            # Blank, or SG-SH- followed by digits only.
            "value": (
                f'=OR(D{first}="",AND(UPPER(LEFT(D{first},6))="SG-SH-",'
                f"ISNUMBER(-MID(D{first},7,20))))"
            ),
            "input_title": "Order number / رقم الطلب",
            "input_message": (
                "Full number, e.g. SG-SH-05785. Required for invoices and returns; "
                "blank for commission and payments.\n"
                "الرقم كاملاً، مطلوب للفواتير والمرتجعات، ويُترك فارغاً للعمولات والدفعات."
            ),
            "error_title": "Invalid order number",
            "error_message": (
                "Must look like SG-SH-05785, never shortened.\n"
                "يجب أن يكون بالشكل SG-SH-05785 دون اختصار."
            ),
        },
    )
    for letter, title, message in (
        ("F", "Debit / مدين", "Sales invoices only.\nفواتير البيع فقط."),
        (
            "G",
            "Credit / دائن",
            "Returns, commission and payments.\nالمردودات والعمولات والدفعات.",
        ),
    ):
        sheet.data_validation(
            col(letter),
            {
                "validate": "decimal",
                "criteria": ">=",
                "value": 0,
                "input_title": title,
                "input_message": f"{message}\nNumbers only / أرقام فقط",
                "error_title": "Invalid amount / مبلغ غير صحيح",
                "error_message": (
                    "Enter a positive number without currency.\n"
                    "أدخل رقماً موجباً بدون رمز العملة."
                ),
            },
        )


# --- Instructions sheet ------------------------------------------------------

# Laid out on the Statement's own column widths (A to H), so the worked example
# at the bottom reads exactly like the sheet the vendor fills in.
_INSTRUCTION_WIDTHS = (13, 34, 14, 16, 26, 13, 13, 17)

_PURPOSE = (
    "Please list every transaction on our account in your books for the "
    "statement period: sales invoices, sales returns, commission entries and "
    "payments received. We reconcile this statement against our portal orders, "
    "so complete and accurate rows mean faster and correct payouts.",
    "يرجى إدراج جميع الحركات المسجلة على حسابنا في دفاتركم خلال فترة الكشف: "
    "فواتير البيع، ومردودات البيع، وقيود العمولة، والدفعات المستلمة. تتم مطابقة "
    "هذا الكشف مع طلبات المنصة، لذا فإن البيانات الكاملة والدقيقة تعني صرف "
    "المستحقات بشكل أسرع وأدق.",
)

_STEPS = (
    (
        f"Open the sheet '{STATEMENT_SHEET}'. Fill in your vendor name, merchant ID, "
        "statement period and opening balance at the top.",
        f"افتح ورقة «{STATEMENT_SHEET}» وأدخل اسم المورد ورقم التاجر وفترة الكشف "
        "والرصيد الافتتاحي في الأعلى.",
    ),
    (
        f"Enter one row per transaction, starting at row {FIRST_DATA_ROW}, oldest "
        "date first. Do not leave empty rows between transactions.",
        f"أدخل حركة واحدة في كل صف بدءاً من الصف {FIRST_DATA_ROW}، مرتبة من الأقدم "
        "إلى الأحدث، دون ترك صفوف فارغة بين الحركات.",
    ),
    (
        "Choose the Document Type from the dropdown list. Do not type your own value.",
        "اختر نوع المستند من القائمة المنسدلة، ولا تكتب قيمة من عندك.",
    ),
    (
        "For every Sales Invoice and Sales Return, write the FULL order number "
        "exactly as on the portal, e.g. SG-SH-05785. Never shorten it.",
        "لكل فاتورة بيع ومردود بيع، اكتب رقم الطلب كاملاً كما يظهر في المنصة، مثل "
        "SG-SH-05785، ولا تختصره أبداً.",
    ),
    (
        "Put each amount in ONE column only: Debit for sales invoices; Credit for "
        "returns, commission and payments. Numbers only, no currency symbol.",
        "ضع كل مبلغ في عمود واحد فقط: مدين لفواتير البيع، ودائن للمردودات والعمولات "
        "والدفعات. أرقام فقط بدون رمز العملة.",
    ),
    (
        f"Running Balance and Row Check fill in automatically. Fix every row until "
        f"Row Check shows '{ROW_OK}' and 'Rows with issues' at the top is 0.",
        f"يُحتسب الرصيد الجاري وعمود التحقق تلقائياً. صحّح كل صف حتى يظهر «{ROW_OK}» "
        "في عمود التحقق ويصبح عدد «صفوف بها أخطاء» في الأعلى صفراً.",
    ),
    (
        "Check that the Closing balance at the top matches the balance in your "
        "accounting system.",
        "تأكد من أن الرصيد الختامي في الأعلى يطابق الرصيد في نظامكم المحاسبي.",
    ),
    (
        "Save as Excel Workbook (.xlsx) and send it to the admin. Do not send a "
        "PDF, CSV or photo.",
        "احفظ الملف بصيغة Excel (.xlsx) وأرسله إلى المسؤول. لا ترسل ملف PDF أو CSV "
        "أو صورة.",
    ),
)

_DONTS = (
    (
        f"Do not rename, move or delete the header rows ({LABEL_ROW} and "
        f"{HEADER_ROW}) or any column.",
        f"لا تغيّر أسماء صفوف العناوين ({LABEL_ROW} و{HEADER_ROW}) أو الأعمدة، "
        "ولا تنقلها أو تحذفها.",
    ),
    (
        "Do not merge cells, and do not add subtotal, total or blank separator rows.",
        "لا تدمج الخلايا، ولا تضف صفوف مجاميع فرعية أو إجماليات أو صفوفاً فاصلة.",
    ),
    (
        "Do not copy the headers to another sheet, and do not add other sheets "
        "with transactions.",
        "لا تنسخ العناوين إلى ورقة أخرى، ولا تضف أوراقاً أخرى تحتوي على حركات.",
    ),
    (
        "When pasting from your system, use Paste Values (Ctrl+Shift+V) so the "
        "dropdowns and checks keep working.",
        "عند اللصق من نظامكم استخدم «لصق القيم فقط» (Ctrl+Shift+V) حتى تبقى "
        "القوائم والتحققات تعمل.",
    ),
    (
        f"One vendor per file. The template holds up to {DATA_ROWS:,} "
        "transactions; contact the admin if you need more.",
        f"ملف واحد لكل مورد. يتسع النموذج لـ {DATA_ROWS:,} حركة، تواصل مع "
        "المسؤول إذا احتجت أكثر.",
    ),
)


def _column_guide() -> list[tuple[str, ...]]:
    required = "Required / إلزامي"
    auto = "Automatic / تلقائي"
    amount = "One of Debit or Credit / مدين أو دائن"
    rows = (
        (required, "YYYY/MM/DD", "2026/08/03", "Document date.\nتاريخ المستند."),
        (
            required,
            "Dropdown list",
            SALES_INVOICE,
            "Pick from the list.\nاختر من القائمة.",
        ),
        (
            required,
            "Text",
            "756954",
            "Your invoice, return or journal number.\n"
            "رقم الفاتورة أو المردود أو القيد في نظامكم.",
        ),
        (
            "Invoices & returns / للفواتير والمرتجعات",
            "SG-SH-#####",
            "SG-SH-05785",
            "Full number, never shortened. Blank for commission and payments.\n"
            "الرقم كاملاً دون اختصار، ويُترك فارغاً للعمولات والدفعات.",
        ),
        ("Optional / اختياري", "Text", "Order SG-SH-05785", "Any note.\nأي ملاحظة."),
        (amount, "Number, 2 decimals", "425.01", "Sales invoices.\nفواتير البيع."),
        (
            amount,
            "Number, 2 decimals",
            "168.00",
            "Returns, commission and payments.\nالمردودات والعمولات والدفعات.",
        ),
        (
            auto,
            "-",
            "6,961.75",
            "Opening balance + debits - credits.\nالرصيد الافتتاحي + المدين - الدائن.",
        ),
        (
            auto,
            "-",
            ROW_OK,
            "Shows what to fix on the row.\nيوضح ما يجب تصحيحه في الصف.",
        ),
    )
    return [
        (chr(ord("A") + index), f"{column.english} / {column.header}", *row)
        for index, (column, row) in enumerate(zip(COLUMNS, rows, strict=True))
    ]


_DOC_TYPE_GUIDE = (
    (
        SALES_INVOICE,
        "Debit / مدين",
        "Required / مطلوب",
        "An order sold to our customer, invoiced at the full order value.",
        "طلب مباع لعميلنا، يُفوتر بكامل قيمة الطلب.",
    ),
    (
        SALES_RETURN,
        "Credit / دائن",
        "Required / مطلوب",
        "An order returned or not received by the customer. Reverses its invoice.",
        "طلب مُرتجع أو لم يستلمه العميل، ويعكس فاتورته.",
    ),
    (
        COMMISSION,
        "Credit / دائن",
        "Leave blank / فارغ",
        "Our commission posted to the account.",
        "قيد عمولتنا المسجل على الحساب.",
    ),
    (
        PAYMENT,
        "Credit / دائن",
        "Leave blank / فارغ",
        "A payment received from us.",
        "دفعة مستلمة منا.",
    ),
)

_EXAMPLE_OPENING = Decimal("6536.74")
# (date, type, doc no, order no, description, debit, credit)
_EXAMPLE = (
    (date(2026, 8, 3), SALES_INVOICE, "756954", "SG-SH-05785", "Order SG-SH-05785",
     Decimal("425.01"), None),
    (date(2026, 8, 3), SALES_INVOICE, "756956", "SG-SH-06247", "Order SG-SH-06247",
     Decimal("329.00"), None),
    (date(2026, 8, 16), SALES_RETURN, "36814", "SG-SH-06118",
     "Customer did not receive the order", None, Decimal("168.00")),
    (date(2026, 8, 20), COMMISSION, "9931", "", "Commission for the July cycle",
     None, Decimal("1048.43")),
    (date(2026, 8, 20), PAYMENT, "9932", "", "Bank transfer received",
     None, Decimal("5000.00")),
)  # fmt: skip


def _instructions_sheet(book: Any, fmt: dict[str, Any]) -> None:
    sheet = book.add_worksheet(INSTRUCTIONS_SHEET)
    sheet.set_tab_color(_NAVY)
    sheet.hide_gridlines(2)
    for col, width in enumerate(_INSTRUCTION_WIDTHS):
        sheet.set_column(col, col, width)

    sheet.set_row(0, 30)
    sheet.merge_range(
        0,
        0,
        0,
        7,
        "eShop Vendor Account Statement - How to fill  |  كشف حساب المورد - طريقة التعبئة",
        fmt["title"],
    )
    row = 2
    row = _section(sheet, fmt, row, "Purpose", "الغرض")
    row = _bilingual(sheet, fmt, row, *_PURPOSE)

    row = _section(sheet, fmt, row + 1, "How to fill it in", "طريقة التعبئة")
    for number, (english, arabic) in enumerate(_STEPS, start=1):
        row = _bilingual(sheet, fmt, row, english, arabic, marker=f"{number}.")

    row = _section(sheet, fmt, row + 1, "The columns", "الأعمدة")
    row = _table(
        sheet,
        fmt,
        row,
        ((0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 7)),
        ("Column", "Header (English / Arabic)", "Required", "Format", "Example",
         "Notes / ملاحظات"),
        _column_guide(),
    )  # fmt: skip

    row = _section(sheet, fmt, row + 1, "Document types", "أنواع المستندات")
    row = _table(
        sheet,
        fmt,
        row,
        ((0, 1), (2, 2), (3, 3), (4, 5), (6, 7)),
        ("Dropdown value / القيمة في القائمة", "Amount in", "Order number",
         "When to use", "متى يُستخدم"),
        _DOC_TYPE_GUIDE,
        arabic_cols=(4,),
    )  # fmt: skip

    row = _section(sheet, fmt, row + 1, "Please do not", "يرجى عدم")
    for english, arabic in _DONTS:
        row = _bilingual(sheet, fmt, row, english, arabic, marker="✗")

    row = _section(
        sheet, fmt, row + 1, "Worked example (illustration only)", "مثال توضيحي"
    )
    row = _example(sheet, fmt, row)

    sheet.write(
        row + 1,
        0,
        f"Template version {TEMPLATE_VERSION}. Questions? Contact the admin.  |  "
        "لأي استفسار تواصل مع المسؤول.",
        fmt["footer"],
    )
    sheet.set_landscape()
    sheet.set_paper(9)  # A4
    sheet.fit_to_pages(1, 0)
    sheet.protect()


def _lines(text: str, width: int) -> int:
    """Roughly how many lines ``text`` wraps to in a cell ``width`` wide."""
    return sum(
        max(1, math.ceil(len(part) / (width * 1.05))) for part in text.split("\n")
    )


def _height(lines: int) -> float:
    # Excel does not auto-fit wrapped text in merged cells, so the height is set.
    return 15.0 * lines + 4


def _span_width(first: int, last: int) -> int:
    return sum(_INSTRUCTION_WIDTHS[first : last + 1])


def _section(
    sheet: Any, fmt: dict[str, Any], row: int, english: str, arabic: str
) -> int:
    sheet.set_row(row, 22)
    sheet.merge_range(row, 0, row, 3, english, fmt["section"])
    sheet.merge_range(row, 4, row, 7, arabic, fmt["section_ar"])
    return row + 1


def _bilingual(
    sheet: Any,
    fmt: dict[str, Any],
    row: int,
    english: str,
    arabic: str,
    *,
    marker: str = "",
) -> int:
    """English across B:D, Arabic across E:H, one row per instruction."""
    if marker:
        sheet.write(row, 0, marker, fmt["step_no"])
    sheet.merge_range(row, 1, row, 3, english, fmt["en"])
    sheet.merge_range(row, 4, row, 7, arabic, fmt["ar"])
    sheet.set_row(
        row,
        _height(
            max(_lines(english, _span_width(1, 3)), _lines(arabic, _span_width(4, 7)))
        ),
    )
    return row + 1


def _table(
    sheet: Any,
    fmt: dict[str, Any],
    row: int,
    spans: Sequence[tuple[int, int]],
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    arabic_cols: Sequence[int] = (),
) -> int:
    for values, style in ((headers, "t_head"), *((r, "t_cell") for r in rows)):
        lines = 1
        for index, ((first, last), value) in enumerate(zip(spans, values, strict=True)):
            cell_fmt = (
                fmt["t_ar"]
                if style == "t_cell" and index in arabic_cols
                else fmt[style]
            )
            if first == last:
                sheet.write_string(row, first, value, cell_fmt)
            else:
                sheet.merge_range(row, first, row, last, value, cell_fmt)
            lines = max(lines, _lines(value, _span_width(first, last)))
        sheet.set_row(row, _height(lines))
        row += 1
    return row


def _example(sheet: Any, fmt: dict[str, Any], row: int) -> int:
    """A filled-in sample, under English-only titles.

    The titles must never repeat the Arabic headers in one row: the parser reads
    every sheet that carries them, and would take this sample as real data.
    """
    titles = [column.english.split(" (")[0] for column in COLUMNS[: _BALANCE + 1]]
    for col, title in enumerate(titles):
        sheet.write_string(row, col, title, fmt["t_head"])
    sheet.set_row(row, 20)
    row += 1
    sheet.merge_range(
        row, 0, row, _CREDIT, "Opening balance / الرصيد الافتتاحي", fmt["t_cell"]
    )
    sheet.write_number(row, _BALANCE, float(_EXAMPLE_OPENING), fmt["t_money"])
    row += 1

    balance = _EXAMPLE_OPENING
    for when, doc_type, doc_no, order_no, desc, debit, credit in _EXAMPLE:
        balance += (debit or 0) - (credit or 0)
        sheet.write_datetime(row, _DATE, when, fmt["t_date"])
        for col, text in ((_TYPE, doc_type), (_DOC_NO, doc_no), (_ORDER, order_no),
                          (_DESC, desc)):  # fmt: skip
            sheet.write_string(row, col, text, fmt["t_cell"])
        for col, amount in ((_DEBIT, debit), (_CREDIT, credit)):
            if amount is None:
                sheet.write_blank(row, col, None, fmt["t_money"])
            else:
                sheet.write_number(row, col, float(amount), fmt["t_money"])
        sheet.write_number(row, _BALANCE, float(balance), fmt["t_money"])
        sheet.set_row(row, _height(max(_lines(doc_type, 34), _lines(desc, 26))))
        row += 1
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        default=Path("eShop_Vendor_Statement_Template.xlsx"),
        help="Where to write the template (default: %(default)s)",
    )
    args = parser.parse_args()
    print(build_vendor_template(args.output))


if __name__ == "__main__":
    main()
