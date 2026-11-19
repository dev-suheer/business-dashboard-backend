"""Part B: every value of Travel's approved setup, in the tables R1 to R29.

Each table is made from the setup itself, in the editor's own words, so it
always holds what the approved setup holds. The walkthrough points at them by
number.
"""

from collections.abc import Callable

from app.config_model.models import (
    CategoryConfig,
    InputFile,
    InputSheet,
    Model,
    OutputTab,
    Raise,
)
from scripts.travel_guide.markup import (
    c,
    esc,
    f,
    kv,
    maybe_f,
    mut,
    reference,
    sub,
    table,
    tick,
)
from scripts.travel_guide.savepoints import GuideBroken, order_lists
from scripts.travel_guide.tabs import Added, added, written

SEVERITY = {"high": "High", "medium": "Medium", "info": "Info"}
FIGURE_KINDS = {"int": "Whole number", "money": "Money", "pct": "Percent"}
FORMATS = {
    "header": "Header",
    "title": "Title",
    "label": "Label",
    "money": "Money",
    "money_bold": "Money, bold",
    "money_bad": "Money, shaded as a problem",
    "money_warn": "Money, shaded as a warning",
    "money_vendor": "Money, vendor shading",
    "total": "Total (bold money)",
    "yellow": "Money, highlighted yellow",
    "yellow_text": "Text, highlighted yellow",
    "date": "Date",
    "rate": "Percentage",
    "high": "High severity",
    "info": "Information",
}


def letter(index: int) -> str:
    """The column letter of a 0-based column: 0 is A, 26 is AA."""
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def pills(items: list[str]) -> str:
    return " ".join(c(item) for item in items) if items else mut("empty")


class Reference:
    def __init__(self, seed: CategoryConfig) -> None:
        if seed.model is None:
            raise GuideBroken("The approved setup has no model")
        self.seed = seed
        self.model: Model = seed.model
        self.uploads = {f.role: f for f in seed.inputs}
        self.tabs = {t.key: t for t in seed.output.tabs}
        self.added = {a.tab.key: a for a in added(seed)}
        self.by_orders = order_lists(seed)
        # The rows of each sheet join a list: its own, or the upload's.
        self.row_lists = {
            (sheet.set or upload.role): upload
            for upload in seed.inputs
            for sheet in upload.sheets
        }
        self.about = {s.key: s.about for s in self.model.sets}

    # --- Shared pieces ---------------------------------------------------------

    def list_named(self, name: str) -> str:
        """A list as the editor's list picker shows it."""
        if name == "entities":
            return f"{c(name)} · every order"
        if name in self.row_lists:
            return f"{c(name)} · rows of {esc(self.row_lists[name].title)}"
        return f"{c(name)} · {esc(self.about.get(name) or '')}"

    def raised(self, rule: Raise) -> str:
        return f"{c(rule.code)}<br>{f(rule.detail)}"

    def listed_under(self, rule: Raise, own: str) -> str:
        if rule.order_no is None:
            return esc(own)
        return f"the order whose key is… {f(rule.order_no)}"

    # --- The tables --------------------------------------------------------------

    def all(self) -> list[tuple[int, str, str]]:
        """Each table's number, title and HTML."""
        made: list[tuple[int, str, str]] = []
        parts: list[tuple[str, str, Callable[[], str]]] = [
            ("Rates and tolerances", "Rates and tolerances dialog", self.r1),
            (
                "Portal export · the upload and its sheet",
                "the card's Settings",
                self.r2,
            ),
            (
                "Portal export · columns",
                "+ Column on the card; the column's panel",
                self.r3,
            ),
            (
                "Portal export · worked-out fields",
                "+ Worked-out field under the grid",
                self.r4,
            ),
            (
                "Vendor claim · the upload and its two sheets",
                "the card's Settings",
                self.r5,
            ),
            (
                "Vendor claim · the claim sheet's columns",
                "the card's first tab",
                self.r6,
            ),
            (
                "Vendor claim · the claim sheet's worked-out fields",
                "the card's first tab",
                self.r7,
            ),
            (
                "Vendor claim · the claim period ledger's columns (no fields)",
                "the card's second tab",
                self.r8,
            ),
            (
                "Wallet history and Finance report",
                "+ Add an upload; the finance report's Settings and columns",
                self.r9,
            ),
            (
                "Lists of rows and of orders",
                "Rules and exceptions → Lists of orders and rows",
                self.r10,
            ),
            ("Members, and the order of the orders", "How orders are made", self.r11),
            ("The exception list", "Rules and exceptions → Exception list", self.r12),
            (
                "Run values and stops",
                "Rules and exceptions → Run values; Stop the run",
                self.r13,
            ),
            ("Upload-row rules", "Rules and exceptions → Upload rows", self.r14),
            ("Order fields", "+ Field on the Orders card", self.r15),
            ("Figures", "+ Figure on the Figures card", self.r16),
            ("Status rules", "Rules and exceptions → Order status", self.r17),
            ("Order exceptions", "Rules and exceptions → Order exceptions", self.r18),
            ("Once per run", "Rules and exceptions → Once per run", self.r19),
            (
                "The wallet check",
                "Wallet history → Settings → Turn on the wallet check",
                self.r20,
            ),
            (
                "Workbook settings",
                "Report workbook card → Workbook settings",
                self.r21,
            ),
            ("The report's tabs", "+ Tab on the Report workbook card", self.r22),
            ("Orders · columns", "the Orders grid; the column's panel", self.r23),
            ("Orders · look-up columns", "Orders → Tab settings", self.r24),
            ("Orders · sheet layout", "Orders → Tab settings → Sheet layout", self.r25),
            (
                "Dashboard · blocks",
                "the Dashboard on the card; a block's or cell's panel",
                self.r26,
            ),
            (
                "Dashboard · breakdown table",
                "the breakdown's first header; a column's panel",
                self.r27,
            ),
            (
                "B2C Invoices ERP · what to change",
                "+ Tab → Table, Start from ERP invoices",
                self.r28,
            ),
            (
                "Exceptions, Wallet, Portal, Vendor and Finance Report tabs",
                "+ Tab → Table, Start from …; Tab settings",
                self.r29,
            ),
        ]
        for number, (title, where, build) in enumerate(parts, 1):
            made.append((number, title, reference(number, title, where, build())))
        return made

    # R1
    def r1(self) -> str:
        return table(
            [
                "What it is",
                "Name in formulas",
                "Kind",
                "Value",
                "One run may change it",
            ],
            (
                [
                    esc(p.label),
                    c(p.key),
                    esc(p.kind.capitalize()),
                    esc(p.value),
                    tick(p.overridable),
                ]
                for p in self.seed.parameters
            ),
            widths=["", "", "12%", "10%", "17%"],
        )

    # R2-R9: the uploads.
    def upload(self, upload: InputFile) -> list[tuple[str, str]]:
        message = upload.empty_message
        return [
            ("Title on the upload screen", esc(upload.title)),
            ("Caption", esc(upload.caption)),
            ("Every run needs this file", tick(upload.required)),
            ("Offer it on the upload screen", tick(upload.shown)),
            (
                "Message when the file has its sheet but no rows",
                esc(message) if message else mut("empty: the usual message"),
            ),
        ]

    def sheet(
        self, upload: InputFile, sheet: InputSheet, step: str
    ) -> list[tuple[str, str]]:
        repeated = mut("Kept")
        if sheet.unique is not None:
            keys = "".join(f"<li>{f(k)}</li>" for k in sheet.unique.keys)
            repeated = (
                f"+ Drop rows that repeat an earlier one; values to compare:<ol>{keys}</ol>"
                f"Compare only rows where {f(sheet.unique.when)}"
            )
            if sheet.unique.exception is not None:
                repeated += (
                    f"<br>Raise an exception for each row dropped: in step {step}, "
                    "from R12"
                )
        rejects = "".join(
            f"<div>When a row {f(r.when)} · Message {f(r.message)}</div>"
            for r in sheet.rejects
        ) or mut("none")
        hint = sheet.missing_hint
        return [
            ("Key", c(sheet.key)),
            ("Called in messages", esc(sheet.name)),
            ("Must have the headers", pills(sheet.fingerprint)),
            ("Is not, with", pills(sheet.exclude)),
            (
                "When missing",
                f"{tick(sheet.optional)} The file may come without this sheet · message: "
                + (esc(hint) if hint else mut("empty")),
            ),
            (
                "Several matching",
                f"{tick(sheet.first_only)} Read only the first sheet that matches",
            ),
            ("Only rows where", maybe_f(sheet.filter, "empty: every row")),
            (
                "They join the list",
                c(sheet.set) if sheet.set else mut(f"empty: {upload.role}"),
            ),
            ("Repeated rows", repeated),
            ("Refuse the upload", rejects),
            ("Counted when", f(sheet.counted)),
            (
                "Listed as",
                c(sheet.manifest_role)
                if sheet.manifest_role
                else mut(f"empty: {upload.role}"),
            ),
        ]

    def columns(self, sheet: InputSheet) -> str:
        return table(
            ["Name", "Header names", "Read as", "Must have", "Blank = not a row"],
            (
                [
                    c(col.field),
                    pills(col.headers),
                    esc(col.type),
                    tick(col.required),
                    tick(col.skip_if_blank),
                ]
                for col in sheet.columns
            ),
            widths=["22%", "", "9%", "9%", "11%"],
        )

    def fields(self, sheet: InputSheet) -> str:
        own = {col.field for col in sheet.columns}
        return table(
            ["Name", "Formula"],
            (
                [
                    c(field.key)
                    + (
                        "<br><span class='small'>replaces the column</span>"
                        if field.key in own
                        else ""
                    ),
                    f(field.formula),
                ]
                for field in sheet.fields
            ),
            widths=["22%", ""],
        )

    def r2(self) -> str:
        portal = self.uploads["portal"]
        return kv(self.upload(portal) + self.sheet(portal, portal.sheets[0], "7.2"))

    def r3(self) -> str:
        return self.columns(self.uploads["portal"].sheets[0])

    def r4(self) -> str:
        return self.fields(self.uploads["portal"].sheets[0])

    def r5(self) -> str:
        vendor = self.uploads["vendor"]
        first, second = vendor.sheets
        return (
            kv(self.upload(vendor))
            + sub("First sheet")
            + kv(self.sheet(vendor, first, "7.2"))
            + sub("Second sheet")
            + kv(self.sheet(vendor, second, "7.2"))
        )

    def r6(self) -> str:
        return self.columns(self.uploads["vendor"].sheets[0])

    def r7(self) -> str:
        return self.fields(self.uploads["vendor"].sheets[0])

    def r8(self) -> str:
        sheet = self.uploads["vendor"].sheets[1]
        if sheet.fields:
            raise GuideBroken("R8 says the claim period ledger has no fields")
        return self.columns(sheet)

    def standard(self, upload: InputFile) -> str:
        sheet = upload.sheets[0]
        types = {col.type for col in sheet.columns}
        rows = [
            [c(col.field), pills(col.headers), tick(col.required)]
            + ([] if len(types) == 1 else [esc(col.type)])
            for col in sheet.columns
        ]
        head = ["Name", "Header names", "Must have"] + (
            [] if len(types) == 1 else ["Read as"]
        )
        title = f"Columns (all {next(iter(types))})" if len(types) == 1 else "Columns"
        return (
            sub(esc(upload.title))
            + kv(
                [
                    ("Called in formulas", c(upload.role)),
                    ("Sheet key", c(sheet.key)),
                    ("Called in messages", esc(sheet.name)),
                    ("Must have the headers", pills(sheet.fingerprint)),
                    ("Is not, with", pills(sheet.exclude)),
                ]
            )
            + f"<p class='small'>{title}</p>"
            + table(head, rows, numbered=False)
            + kv(self.upload(upload))
        )

    def r9(self) -> str:
        found = [f for f in self.seed.inputs if f.kind in ("wallet", "reference")]
        how = {
            "wallet": "Added whole by + Add an upload → Wallet history: check it.",
            "reference": "+ Add an upload → A file to look things up in, then typed "
            "by hand: everything below.",
        }
        return "".join(
            f"<p class='small'>{esc(how[upload.kind])}</p>" + self.standard(upload)
            for upload in found
        )

    # R10-R11: lists and members.
    def r10(self) -> str:
        return table(
            ["Name", "Made from", "Only those where", "Plain words", "Added in"],
            (
                [
                    c(s.key),
                    self.list_named(s.over),
                    f(s.where),
                    esc(s.about or ""),
                    "step 9.2" if s.over in self.by_orders else "step 6.2",
                ]
                for s in self.model.sets
            ),
            widths=["13%", "20%", "", "22%", "9%"],
        )

    def r11(self) -> str:
        rows = []
        for member in self.model.members:
            own = tick(bool(member.primary))
            if member.primary:
                own += " the earliest by " + ", then ".join(
                    f(p) for p in member.primary
                )
            rows.append(
                [
                    self.list_named(member.set),
                    f(member.key),
                    c(member.name),
                    own,
                    c(member.repeats) if member.repeats else "—",
                ]
            )
        sort = [
            [esc("By" if n == 0 else "then by"), f(formula)]
            for n, formula in enumerate(self.model.sort)
        ]
        return (
            table(
                [
                    "Rows of",
                    "Joined by",
                    "Kept as",
                    "One row is its own",
                    "The others, kept as",
                ],
                rows,
                widths=["24%", "12%", "11%", "", "12%"],
            )
            + sub("The order of the orders (set in step 10.4)")
            + table(["", "Formula"], sort, numbered=False, widths=["20%", ""])
        )

    # R12: the exception list, and the dropped rows' exception.
    def r12(self) -> str:
        codes = table(
            ["Code", "In words", "Severity"],
            (
                [c(e.code), esc(e.about or ""), esc(SEVERITY[e.severity])]
                for e in self.seed.exceptions
            ),
            widths=["34%", "", "10%"],
        )
        dropped = []
        for upload in self.seed.inputs:
            for sheet in upload.sheets:
                exception = sheet.unique.exception if sheet.unique else None
                if exception is None:
                    continue
                dropped.append(
                    sub(
                        f"The repeated rows' exception of the {esc(sheet.name)} sheet (step 7.2)"
                    )
                    + kv(
                        [
                            ("Raise", c(exception.code)),
                            ("Message", f(exception.detail)),
                            ("Amount", maybe_f(exception.amount, "none")),
                            ("Listed under", self.listed_under(exception, "no order")),
                        ]
                    )
                )
        return codes + "".join(dropped)

    # R13: run values and stops.
    def r13(self) -> str:
        values = table(
            ["Name", "Worked out as"],
            ([c(v.key), f(v.formula)] for v in self.model.context),
            widths=["22%", ""],
        )
        stops = table(
            ["Plain words", "When", "Message"],
            ([esc(s.about or ""), f(s.when), f(s.message)] for s in self.model.stops),
            widths=["28%", "", ""],
        )
        return sub("Run values") + values + sub("Stop the run") + stops

    # R14: upload-row rules.
    def r14(self) -> str:
        return table(
            [
                "Plain words · For each row of",
                "When",
                "Raise · Message",
                "Amount",
                "Listed under",
            ],
            (
                [
                    f"{esc(r.about or '')}<br>{self.list_named(r.over)}",
                    f(r.when),
                    self.raised(r),
                    maybe_f(r.amount, "none"),
                    self.listed_under(r, "no order"),
                ]
                for r in self.model.row_rules
            ),
            widths=["20%", "20%", "", "15%", "12%"],
        )

    # R15-R16: order fields and figures.
    def r15(self) -> str:
        return table(
            ["Name", "Formula"],
            ([c(x.key), f(x.formula)] for x in self.model.fields),
            widths=["26%", ""],
        )

    def r16(self) -> str:
        return table(
            ["Name", "Formula"],
            ([c(x.key), f(x.formula)] for x in self.model.metrics),
            widths=["32%", ""],
        )

    # R17-R19: status rules and exceptions.
    def r17(self) -> str:
        for rule in self.model.rules:
            if rule.exception is not None or rule.checks:
                raise GuideBroken("R17 has no place for a status rule's exception")
        return table(
            ["Plain words", "When", "The order is", "Note"],
            (
                [esc(r.about or ""), f(r.when), c(r.verdict), maybe_f(r.note, "empty")]
                for r in self.model.rules
            ),
            widths=["17%", "17%", "13%", ""],
        )

    def r18(self) -> str:
        if self.model.checks:
            raise GuideBroken("The guide has no place for checks")
        return table(
            ["Plain words · For", "When", "Raise · Message", "Amount", "Listed under"],
            (
                [
                    f"{esc(r.about or '')}<br><span class='small'>"
                    + (
                        f"For each of an order's {esc(r.each)}"
                        if r.each
                        else "For each order"
                    )
                    + "</span>",
                    f(r.when),
                    self.raised(r),
                    maybe_f(r.amount, "none"),
                    self.listed_under(r, "this order"),
                ]
                for r in self.model.findings
            ),
            widths=["17%", "22%", "", "15%", "9%"],
        )

    def r19(self) -> str:
        return table(
            ["Plain words", "When", "Raise · Message", "Amount", "Listed under"],
            (
                [
                    esc(r.about or ""),
                    f(r.when),
                    self.raised(r),
                    maybe_f(r.amount, "none"),
                    self.listed_under(r, "no order"),
                ]
                for r in self.model.run_rules
            ),
            widths=["20%", "16%", "", "17%", "9%"],
        )

    # R20: the wallet check.
    def r20(self) -> str:
        wallet = self.model.wallet
        if wallet is None:
            raise GuideBroken("Travel checks a wallet history")
        types = table(
            ["Wallet Type", "means"],
            ([c(name), c(vertical)] for name, vertical in wallet.wallet_types.items()),
            numbered=False,
        )
        return kv(
            [
                ("Rows checked", self.list_named(wallet.rows)),
                (
                    "Rows left out",
                    self.list_named(wallet.excluded)
                    if wallet.excluded
                    else mut("none"),
                ),
                ("Vertical", f(wallet.vertical)),
                ("Wallet types", types),
                (
                    "Characters to strip",
                    c(wallet.strip) if wallet.strip else mut("empty"),
                ),
                ("SG Order ID", f(wallet.sg_order_id)),
                ("Reference", f(wallet.reference)),
                ("Order date", f(wallet.order_date)),
                ("Wallet amount", f(wallet.amount)),
                ("Tolerance", f(wallet.tolerance)),
            ]
        )

    # R21: workbook settings.
    def r21(self) -> str:
        history = self.model.history
        labels = [
            ("Orders reconciled", history.counted),
            ("Out of", history.total),
            ("Called", history.unit),
            ("Net payable", history.net_payable),
            ("Vendor", history.vendor),
            ("Period from", history.period_start),
            ("Period to", history.period_end),
            ("Report file name", history.report_name),
        ]
        texts = table(
            ["Key", "Formula"],
            ([c("{" + t.key + "}"), f(t.formula)] for t in self.seed.output.texts),
            numbered=False,
            widths=["30%", ""],
        )
        words = "".join(
            f"<p>Group {c(group)}:</p>"
            + table(
                ["Value", "is written"],
                ([c(value), esc(text)] for value, text in values.items()),
                numbered=False,
            )
            for group, values in self.seed.labels.items()
        )
        return (
            sub("The History screen and the file name")
            + kv([(label, f(formula)) for label, formula in labels])
            + sub("Texts the sheets can name")
            + texts
            + sub("Words for values")
            + words
        )

    # R22: the tabs.
    def r22(self) -> str:
        rows = []
        for tab in self.seed.output.tabs:
            way: Added = self.added[tab.key]
            what = way.kind
            start = esc(way.start) if way.start else "—"
            if way.present:
                start += (
                    "<br><span class='small'>there already in a new category</span>"
                )
            choices = "<br>".join(f"{esc(k)}: {esc(v)}" for k, v in way.choices) or "—"
            rows.append(
                [
                    esc(tab.title),
                    c(tab.key),
                    esc(what),
                    start,
                    choices,
                    esc(written(self.seed, tab)),
                ]
            )
        return table(
            ["Title", "Key", "What it is", "Start from", "In the dialog", "Written"],
            rows,
            widths=["15%", "14%", "11%", "17%", "", "19%"],
        )

    # R23-R25: the Orders tab.
    def r23(self) -> str:
        tab = self.tabs["orders"]
        rows = []
        for n, col in enumerate(tab.columns):
            formula = maybe_f(col.value, "none")
            extra = []
            if col.key in tab.tolerances:
                extra.append(f"Shade the difference above {f(tab.tolerances[col.key])}")
            if col.excel:
                extra.append(f"Excel formula {f(col.excel)}")
            if col.style:
                extra.append(f"Style {f(col.style)}")
            if col.color:
                extra.append(f"Header colour {c(col.color)}")
            rows.append(
                [
                    f"<span class='mut'>{letter(n)}</span>",
                    esc(col.header),
                    c(col.key),
                    esc(col.kind),
                    esc(col.width),
                    esc(col.group),
                    formula + "".join(f"<br>{x}" for x in extra),
                ]
            )
        return table(
            ["", "Header", "Key", "Shown as", "Width", "Band", "Formula (in the bar)"],
            rows,
            widths=["4%", "15%", "19%", "7%", "6%", "7%", ""],
        )

    def r24(self) -> str:
        tab = self.tabs["orders"]
        lookups = [x.lookup for x in tab.lookup_columns]
        if not lookups or any(x is None for x in lookups):
            raise GuideBroken("R24 shows the finance report's look-up columns")
        first = lookups[0]
        assert first is not None
        if any(
            x is not None
            and (x.sheet, x.match, x.key_column, x.key_value)
            != (first.sheet, first.match, first.key_column, first.key_value)
            for x in lookups
        ):
            raise GuideBroken("R24 shows look-up columns that look up alike")
        copy = self.tabs[first.sheet]
        upload = next(u for u in self.seed.inputs if u.role == copy.source)
        sheet = next(s for s in upload.sheets if s.key == copy.sheet)
        headers = {col.field: col.headers[0] for col in sheet.columns}
        if first.key_column is not None:
            header = next(x.header for x in tab.columns if x.key == first.key_column)
            by = f"the column {esc(header)}"
        else:
            by = f"a formula: {f(first.key_value or '')}"
        columns = table(
            ["Header", "Key", "Bring back", "Shown as", "Width", "Band"],
            (
                [
                    esc(x.header),
                    c(x.key),
                    esc(headers.get(x.lookup.value, x.lookup.value)),
                    esc(x.kind),
                    esc(x.width),
                    esc(x.group),
                ]
                for x in tab.lookup_columns
                if x.lookup is not None
            ),
            numbered=False,
        )
        return (
            kv(
                [
                    (
                        "Look-up columns",
                        f"+ Look-up column, once for each of the {len(tab.lookup_columns)} "
                        "below, in order",
                    ),
                    ("Looked up in (each)", esc(copy.title)),
                    ("Find each row by (each)", by),
                    (
                        "In its column (each)",
                        esc(headers.get(first.match, first.match)),
                    ),
                ]
            )
            + columns
        )

    def section_head(self, n: int, section_kind: str) -> str:
        names = {
            "rows": "+ Rows of a list",
            "totals": "+ Totals row",
            "block": "+ Fixed rows",
        }
        return sub(f"Part {n} · {names[section_kind]}")

    def r25(self) -> str:
        tab = self.tabs["orders"]
        out = []
        for n, s in enumerate(tab.sections, 1):
            when = "always" if s.when is None else f"only when {s.when} has rows"
            settings: list[tuple[str, str]] = [
                ("Blank rows before · Written", f"{s.gap} · {esc(when)}")
            ]
            if s.kind == "rows":
                if s.upload is not None or s.contacts is not None or s.where != "TRUE":
                    raise GuideBroken("R25 shows a part listing a list's members only")
                settings += [
                    (
                        "The rows",
                        f"the members of a list: {self.list_named(s.set or '')}",
                    ),
                    ("Only rows where", mut("every row")),
                    ("Contact names", mut("none")),
                    ("Title row above", esc(s.title) if s.title else mut("none")),
                    (
                        "Also",
                        f"{tick(s.autofilter)} Filter arrows on the header row "
                        f"{tick(s.tolerances)} Shade a difference column beyond its tolerance "
                        f"{tick(s.lookups)} Fills the look-up columns",
                    ),
                    (
                        "Values",
                        f"{tick(bool(s.values))} These rows have values of their own",
                    ),
                ]
                if s.values:
                    raise GuideBroken("R25 has no place for a part's own values")
            elif s.kind == "totals":
                formats = (
                    ", ".join(
                        f"{esc(k)}: {esc(FORMATS[v])}" for k, v in s.formats.items()
                    )
                    or "all left on Total (bold money)"
                )
                settings += [
                    ("Adds up the rows of", self.list_named(s.set or "")),
                    ("Label in column A", c(s.label) if s.label else mut("none")),
                    ("Formats", formats),
                ]
            out.append(self.section_head(n, s.kind) + kv(settings))
            if s.kind == "block":
                out.append(self.block_rows(tab, s.rows))
        return "".join(out)

    def block_rows(self, tab: OutputTab, rows: list) -> str:  # type: ignore[type-arg]
        def column(col: int | str | None) -> str:
            if col is None:
                return "no"
            if isinstance(col, int):
                return f"column {letter(col)}"
            n = next(i for i, x in enumerate(tab.columns) if x.key == col)
            return f"{letter(n)} · {esc(tab.columns[n].header)}"

        body = []
        for r, row in enumerate(rows, 1):
            if not row.cells:
                body.append(
                    f"<tr><td class='n'>{r}</td><td colspan='5' class='mut'>"
                    "a blank line: + Row, with no cells</td></tr>"
                )
                continue
            for i, cell in enumerate(row.cells):
                if cell.value is not None:
                    holds, value = "Worked out", f(cell.value)
                elif cell.formula is not None:
                    holds, value = "Excel formula", c(cell.formula)
                elif cell.metric is not None:
                    holds, value = "Figure", c(cell.metric)
                else:
                    holds, value = "Text", esc(cell.text or "")
                merged = (
                    "no merge"
                    if cell.to_col is None
                    else f"merged to {column(cell.to_col)}"
                )
                fmt = FORMATS[cell.format] if cell.format else "Plain"
                n = f"<td class='n'>{r}</td>" if i == 0 else "<td class='n'></td>"
                body.append(
                    f"<tr>{n}<td>{column(cell.col)}</td><td>{esc(merged)}</td>"
                    f"<td>{esc(holds)}</td><td>{value}</td><td>{esc(fmt)}</td></tr>"
                )
            if row.height is not None:
                raise GuideBroken("R25 has no place for a row's height")
        head = "".join(
            f"<th>{h}</th>"
            for h in ("Row", "Column", "Merged", "Holds", "Value", "Format")
        )
        return (
            "<table><colgroup><col style='width:5%'><col style='width:11%'>"
            "<col style='width:17%'><col style='width:11%'><col><col style='width:15%'>"
            f"</colgroup><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"
        )

    # R26-R27: the Dashboard.
    def r26(self) -> str:
        tab = self.tabs["dashboard"]
        out = []
        for b, block in enumerate(tab.blocks, 1):
            label = esc(block.label) if block.label else mut("empty")
            value_label = esc(block.value_label) if block.value_label else mut("empty")
            out.append(
                sub(
                    f"Block {b} · Label {label} · Label of the figures {value_label} · "
                    f"{tick(block.tall)} A tall header row"
                )
                + table(
                    ["Header", "Figure", "Written as"],
                    (
                        [esc(h), c(v.metric), esc(FIGURE_KINDS[v.kind])]
                        for h, v in zip(block.headers, block.values, strict=True)
                    ),
                    widths=["40%", "", "20%"],
                )
            )
        return "".join(out)

    def r27(self) -> str:
        spec = self.tabs["dashboard"].breakdown
        if spec is None:
            raise GuideBroken("Travel's Dashboard has a breakdown table")
        totals = "<br>".join(
            f"{c(t.key)} = {f(t.formula)}" for t in spec.totals
        ) or mut("none")
        ranked = (
            "The name"
            if spec.rank_by is None
            else f"{esc(spec.columns[spec.rank_by].header)}, most first"
        )
        total = {
            "sum": "Adds the rows up",
            "whole": "Worked out on every member at once, unsplit",
        }[spec.total]
        settings = kv(
            [
                ("Header of the names", esc(spec.name_header)),
                ("Name formula (in the bar)", f(spec.name)),
                ("Rows from", c(spec.set)),
                (
                    "Each member split into",
                    c(spec.parts) if spec.parts else mut("Not split"),
                ),
                ("Each part's weight", f(spec.basis)),
                ("Totals of a name's parts", totals),
                ("Ranked by", ranked),
                ("TOTAL row", total),
            ]
        )
        adds = {"count": "One: a count", "sum": "A value: a sum"}
        columns = table(
            [
                "Header",
                "Each member adds",
                "Formula (in the bar)",
                "Only where",
                "Written as",
            ],
            (
                [
                    esc(col.header),
                    esc(adds[col.add]),
                    f(col.when if col.add == "count" else col.value),
                    "—" if col.add == "count" else f(col.when),
                    esc(FIGURE_KINDS[col.kind]),
                ]
                for col in spec.columns
            ),
            widths=["12%", "11%", "30%", "", "10%"],
        )
        return settings + columns

    # R28-R29: the tabs added from the standard sheets.
    def changes(self, way: Added) -> str:
        if not way.changes:
            return mut("nothing to change")
        return "<br>".join(
            f"{esc(ch.where)} → {esc(ch.setting)}: "
            + (f(ch.value) if ch.formula else esc(ch.value))
            for ch in way.changes
        )

    def r28(self) -> str:
        way = self.added["b2c_invoices"]
        if way.changes is None:
            raise GuideBroken("B2C Invoices ERP starts from ERP invoices")
        rows = [
            [
                esc(ch.where),
                esc(ch.setting),
                f(ch.value) if ch.formula else esc(ch.value),
            ]
            for ch in way.changes
        ]
        dialog = "; ".join(f"{k}: {v}" for k, v in way.choices)
        return (
            f"<p>Added by + Tab → Table, Start from {c('ERP invoices')}; {esc(dialog)}. "
            "It comes with its eight columns; change these, everything else stays as it "
            "came:</p>"
            + table(["Where", "Setting", "Value"], rows, widths=["24%", "22%", ""])
        )

    def r29(self) -> str:
        rows = []
        for key, way in self.added.items():
            if key == "b2c_invoices" or way.changes is None:
                continue
            dialog = "<br>".join(f"{esc(k)}: {esc(v)}" for k, v in way.choices) or "—"
            start = "there already" if way.present else esc(way.start)
            rows.append(
                [esc(way.tab.title), c(way.tab.key), start, dialog, self.changes(way)]
            )
        return table(
            ["Title", "Key", "Start from", "In the dialog", "Then change"],
            rows,
            widths=["13%", "15%", "13%", "21%", ""],
        )
