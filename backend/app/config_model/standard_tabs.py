"""The report tabs every category starts from, as plain tables.

The approved setups build their sheets from these (``seed_output.py``), and the
editor offers the same ones as templates (``GET /standard``), so a tab added in
the editor is the approved one until someone changes it.
"""

from app.config_model.models import (
    BlockRow,
    Cell,
    Contacts,
    OutputColumn,
    OutputTab,
    ReportFormat,
    Section,
)

# High is written red; medium and info alike.
_SEVERITY_STYLE = 'IF(severity = "high", "high", "info")'


def _column(
    key: str,
    header: str,
    width: int,
    value: str | None,
    *,
    kind: str = "text",
    style: str | None = None,
    excel: str | None = None,
) -> OutputColumn:
    return OutputColumn(
        key=key,
        header=header,
        width=width,
        group="meta",
        kind=kind,
        value=value,
        style=style,
        excel=excel,
    )


def exceptions_tab(key: str, title: str, status: str) -> OutputTab:
    """Every exception of the run, most severe first: its severity, code,
    order, the order's ``status`` (a formula on the exception), amount and
    detail."""
    column = _column
    return OutputTab(
        key=key,
        title=title,
        kind="table",
        header="plain",
        columns=[
            column("severity", "Severity", 12, "TEXT(severity)", style=_SEVERITY_STYLE),
            column("code", "Code", 32, "TEXT(code)", style=_SEVERITY_STYLE),
            column("order_no", "Order", 18, "order_no"),
            column("status", "Status", 36, f"TEXT({status})", style=_SEVERITY_STYLE),
            column("amount", "Amount", 16, "amount", kind="money", style='"money"'),
            column("detail", "Detail", 88, "detail"),
        ],
        sections=[
            Section(
                kind="rows",
                set="exceptions",
                tolerances=False,
            )
        ],
    )


def erp_invoices_tab(
    key: str,
    title: str,
    *,
    unit_cell: str,
    partner: str,
    order_id: str,
    bill_date: str,
    product: str,
    rows: str | None = None,
    upload: str | None = None,
    where: str = "TRUE",
    contact: str = "BLANK()",
    contacts: Contacts | None = None,
) -> OutputTab:
    """The B2C invoice import, ready for the ERP: one line for each of the
    ``rows`` set's members (or each of an ``upload``'s own rows) that ``where``
    holds for. Its unit price is the ``unit_cell`` (``tab.column``) of the
    line's row, before VAT; its contact is the name ``contacts`` finds,
    failing that ``contact``."""
    named = f"COALESCE(contact, {contact})" if contacts is not None else contact
    column = _column
    return OutputTab(
        key=key,
        title=title,
        kind="table",
        header="plain",
        columns=[
            column("partner", "Partner", 16, f"TEXT({partner})"),
            column(
                "order_id",
                "Invoice Partner Display Name",
                28,
                f"TEXT({order_id})",
                kind="id",
            ),
            column("contact", "Contact Name", 24, f"TEXT({named})"),
            column("bill_date", "Invoice/Bill Date", 18, bill_date, kind="datetime"),
            column(
                "product",
                "Invoice lines/Product",
                18,
                f"TEXT({product})",
                kind="string",
            ),
            column("quantity", "Invoice lines/Quantity", 22, "1"),
            column(
                "unit_price",
                "Invoice lines/Unit Price",
                24,
                None,
                kind="money",
                style='"money"',
                excel=f"={{cell:{unit_cell}}}/1.15",
            ),
            column(
                "taxes",
                "Invoice lines/Taxes",
                26,
                '"Domestic Sales (15%)"',
                kind="string",
            ),
        ],
        sections=[
            Section(
                kind="rows",
                set=rows,
                upload=upload,
                where=where,
                contacts=contacts,
                tolerances=False,
                autofilter=True,
            )
        ],
    )


# The wallet history's counts, as the Wallet sheet ends with them.
_WALLET_COUNTS = (
    ("Wallet-paid rows checked", "wallet_check.rows_checked"),
    ("Found, same amount", "wallet_check.matched"),
    ("Found, amount disagreed", "wallet_check.amount_mismatch"),
    ("Outside the history's window", "wallet_check.out_of_window"),
    ("Missing from the history", "wallet_check.missing"),
    ("Wallet-paid rows with no SG Order ID, not checked", "wallet_check.unkeyed"),
    ("History movements with no portal row", 'COUNTX("wallet_unmatched")'),
)
_WALLET_COVERS = (
    "IF(AND(NOT(ISBLANK(wallet_check.window_start)), "
    "NOT(ISBLANK(wallet_check.window_end))), "
    'TEXT(wallet_check.window_start) & " to " & TEXT(wallet_check.window_end), '
    '"no dated movements")'
)


def wallet_tab(key: str, title: str, requires: str = "wallet") -> OutputTab:
    """The wallet check: each wallet-paid row against the history, then the
    history's movements no row had, then what the history held. Written only
    when a wallet history was uploaded (``requires``)."""
    column = _column
    money = '"money"'

    def label(text: str, value: str | None = None) -> BlockRow:
        cells = [Cell(col=0, text=text, format="label")]
        if value is not None:
            cells.append(Cell(col=2, value=value))
        return BlockRow(cells=cells)

    def heading(text: str) -> BlockRow:
        return BlockRow(cells=[Cell(col=0, text=text, format="title")])

    return OutputTab(
        key=key,
        title=title,
        kind="table",
        header="plain",
        requires=requires,
        columns=[
            column("vertical", "Vertical", 10, "vertical", kind="string"),
            column("sg_order_id", "SG Order ID", 12, "sg_order_id", kind="string"),
            column("order", "Order", 22, "booking_id"),
            column("order_date", "Order Date", 12, "order_date", kind="date"),
            column(
                "portal_wallet",
                "Portal Wallet",
                14,
                "portal_wallet_amount",
                kind="money",
                style=money,
            ),
            column(
                "history_paid",
                "History Paid",
                14,
                "history_paid",
                kind="money",
                style=money,
            ),
            column(
                "history_refunded",
                "History Refunded",
                16,
                "history_refunded",
                kind="money",
                style=money,
            ),
            column("movements", "Movements", 11, "events"),
            column("state", "State", 16, "TEXT(state)", kind="string"),
            column(
                "difference",
                "Difference",
                13,
                "diff",
                kind="money",
                style='IF(state = "amount_mismatch", "money_bad", "money")',
            ),
        ],
        sections=[
            Section(kind="rows", set="wallet_checks", tolerances=False),
            Section(
                kind="block",
                gap=1,
                rows=[heading("Wallet movements with no portal row")],
            ),
            Section(
                kind="rows",
                set="wallet_unmatched",
                tolerances=False,
                values={
                    "vertical": "vertical",
                    "sg_order_id": "sg_order_id",
                    "order_date": "event_date",
                    "portal_wallet": "amount",
                    "state": "IF(ISBLANK(message), BLANK(), message)",
                },
                styles={"portal_wallet": money},
            ),
            Section(
                kind="block",
                gap=1,
                rows=[
                    heading("Wallet history"),
                    label("History covers", _WALLET_COVERS),
                    *(label(text, value) for text, value in _WALLET_COUNTS),
                ],
            ),
        ],
    )


def copy_tab(
    key: str,
    title: str,
    source: str,
    sheet: str,
    header_format: ReportFormat | None = "header",
    requires: str | None = None,
) -> OutputTab:
    """One of the uploads, its ``sheet`` kind finding the header, as it
    arrived: nothing left out and nothing added until the setup says so."""
    return OutputTab(
        key=key,
        title=title,
        kind="table",
        requires=requires,
        source=source,
        sheet=sheet,
        header_format=header_format,
    )
