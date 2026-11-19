"""Part A: building Travel in the editor, step by step, with its save points.

The steps name the editor's own labels. Names and formulas quoted from the
setup are looked up in it, so a step cannot quote one the setup no longer has;
the save points are the ones ``savepoints.build`` checked.
"""

import base64
from pathlib import Path

from app.config_model.models import CategoryConfig, Named
from scripts.travel_guide.markup import c, esc, f, table
from scripts.travel_guide.savepoints import GuideBroken, SavePoint
from scripts.travel_guide.tabs import added

FIXTURES = "business-dashboard-backend/backend/tests/fixtures/travel/"
# Dialogs narrow enough to be shown at half the page's width.
NARROW = {"add_upload", "add_tab"}
WORDS = {6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}

SECTIONS = [
    "Read this first",
    "How Travel decides: one booking, start to finish",
    "Keep a way back, then start blank",
    "Rates and tolerances",
    "The uploads",
    "How orders are made",
    "The exception list",
    "Run values, stops, upload-row rules",
    "Order fields, lists of orders, figures",
    "Status rules and exceptions",
    "The wallet check",
    "Workbook settings",
    "The report workbook",
    "Check your Travel",
]


def _formula(items: list[Named], key: str) -> str:
    for item in items:
        if item.key == key:
            return item.formula
    raise GuideBroken(f"The walkthrough quotes {key}, which the setup no longer has")


class Walkthrough:
    def __init__(
        self,
        seed: CategoryConfig,
        points: list[SavePoint],
        shots: dict[str, Path],
    ) -> None:
        if seed.model is None or len(points) != 13:
            raise GuideBroken("The walkthrough has 13 save points")
        self.seed = seed
        self.model = seed.model
        self.points = points
        self.shots = shots
        self.figures: list[str] = []

    # --- Pieces ------------------------------------------------------------------

    def step(self, number: int) -> str:
        return (
            f'<h2 class="step" id="s{number}"><span class="badge">{number}</span>'
            f"{esc(SECTIONS[number - 1])}</h2>"
        )

    def do(self, number: str, body: str) -> str:
        return f'<div class="do"><span class="badge">{number}</span><div>{body}</div></div>'

    def save(self, number: int) -> str:
        point = self.points[number - 1]
        return (
            f'<div class="save"><b>SAVE POINT {point.number}</b> · Click <b>Save</b>, '
            f"write {c(point.changed)} as what changed, and save. The setup check is "
            "empty here.</div>"
        )

    def figure(self, key: str, caption: str) -> str:
        self.figures.append(key)
        path = self.shots.get(key)
        if path is None:
            image = f'<div class="mut">[screenshot: {esc(key)}]</div>'
        else:
            data = base64.b64encode(path.read_bytes()).decode()
            width = ' style="max-width:96mm"' if key in NARROW else ""
            image = f'<img src="data:image/png;base64,{data}" alt=""{width}>'
        return f"<figure>{image}<figcaption>{esc(caption)}</figcaption></figure>"

    # --- The sections ------------------------------------------------------------

    def html(self) -> str:
        return (
            '<h1 class="part">Part A · Walkthrough</h1>'
            + self.s1()
            + self.s2()
            + self.s3()
            + self.s4()
            + self.s5()
            + self.s6()
            + self.s7()
            + self.s8()
            + self.s9()
            + self.s10()
            + self.s11()
            + self.s12()
            + self.s13()
            + self.s14()
        )

    def s1(self) -> str:
        boxes = [
            ("Uploads", "files, their columns, fields worked out on each row"),
            ("Lists of rows", "named parts of the rows: the claim's invoice lines…"),
            ("Orders", "one per booking: its rows gathered by booking ID"),
            ("Order fields", "worked out on each booking"),
            ("Lists of orders", "matched, unmatched…"),
            ("Figures", "totals for the whole run"),
            ("Rules", "each booking's status, its exceptions, the stops"),
            ("Report", "the workbook's tabs, columns and layout"),
        ]
        grid = "".join(
            f'<div><span class="n">{n}</span><b>{esc(t)}</b>{esc(d)}</div>'
            for n, (t, d) in enumerate(boxes, 1)
        )
        return (
            self.step(1)
            + "<p>Travel reconciles hotel and flight bookings. The <b>portal export</b> says "
            "what customers booked and paid through SG; the vendor's weekly <b>claim</b> says "
            "what the vendor bills. Travel matches them booking by booking, works out what "
            "each side's commission should be, raises an exception for every difference, and "
            "writes a report workbook. The <b>wallet history</b> (proves the wallet part of "
            "each payment) and the <b>finance report</b> (adds each booking's cashback) are "
            "optional.</p>"
            + f'<div class="map">{grid}</div>'
            + "<p>You build them in that order, because each part reads the ones before it. "
            "<b>This part</b> shows how to do each kind of step, once, and what it does. "
            "<b>Part B</b> holds every value to type, in tables <b>R1</b>, <b>R2</b>… that each "
            "step points to; keep it open beside the editor. Tables list items in order: add "
            f"them in that order. {c('Like this')} and {f('formulas like this')} are typed "
            "exactly; ☑ means ticked.</p>"
            + '<div class="watch"><b>Watch out. Save at the save points.</b> Work lives in the '
            "browser until you click Save: don't reload the page between save points. Save is "
            "refused while the setup check lists a problem; at each save point it is empty "
            "(checked when this guide was made). When nothing is selected, the right-hand "
            "panel lists the problems; click one to jump to it.</div>"
            + '<div class="watch"><b>Watch out. Some names can only be typed while the item is '
            "new</b>, during the visit to the dialog that added it: a rate's <b>Name in "
            "formulas</b>, a list's <b>Name</b>, a member's <b>Kept as</b> and <b>The others, "
            "kept as</b>, a text's key. Fields, figures, columns, sheet keys and run values "
            "can be renamed any time, everywhere at once.</div>"
        )

    def s2(self) -> str:
        m = self.model
        sets = {s.key for s in m.sets}
        for name in (
            "lines",
            "adjustments",
            "skipped",
            "booked",
            "matched",
            "unmatched",
        ):
            if name not in sets:
                raise GuideBroken(f"The walkthrough quotes the list {name}")
        names = {member.name for member in m.members} | {
            member.repeats for member in m.members if member.repeats
        }
        for name in ("lines", "portal", "repeats", "adjustments"):
            if name not in names:
                raise GuideBroken(f"The walkthrough quotes the member {name}")
        match_state = _formula(m.fields, "match_state")
        _formula(m.fields, "v_expected")
        base = next((x for x in m.findings if x.code == "BOOKING_BASE_MISMATCH"), None)
        if base is None:
            raise GuideBroken("The walkthrough quotes BOOKING_BASE_MISMATCH")
        matched = _formula(m.metrics, "bookings_matched")
        rules = table(
            ["When", "The order is"],
            ([f(r.when), c(r.verdict)] for r in m.rules),
            widths=["", "22%"],
        )
        count = WORDS.get(len(m.rules), str(len(m.rules)))
        return (
            self.step(2)
            + f"<p><b>Rows.</b> A run reads the uploads' rows and sorts them into "
            f"<b>lists</b>: the claim's rows become {c('lines')} (invoice lines), "
            f"{c('adjustments')} and {c('skipped')} by the worked-out field {c('kind')}; the "
            f"portal rows with a booking ID are {c('booked')}. Worked-out fields clean each "
            f"row first: the portal's {c('booking_id')} field, for instance, upper-cases and "
            "trims the raw column.</p>"
            + f"<p><b>Orders.</b> Every row with the same booking ID joins one order: its "
            f"claim {c('lines')}, its {c('portal')} row (the earliest, if the export repeats "
            f"it; the others go to {c('repeats')}) and any {c('adjustments')} that point back "
            "at it.</p>"
            + "<p><b>Order fields</b> are then worked out on each booking, in turn: totals of "
            "its claim lines, the portal's amounts, and comparisons. For example "
            f"{c('match_state')} is {f(match_state)}, and the vendor's expected commission "
            f"{c('v_expected')} adds up each claim line's {c('expected')}, its amount times "
            f"{c('line_rate')}: the hotel or flight rate, plus VAT for an international "
            "booking.</p>"
            + "<p><b>Status.</b> Each booking gets one verdict: the status rules are tried "
            f"from the top, and the first that holds decides. Travel's {count}:</p>"
            + rules
            + "<p><b>Exceptions.</b> Then every order exception is checked on every booking, "
            f"whatever its verdict. For instance {c(base.code)} is raised when {f(base.when)}: "
            "a matched booking whose base amounts differ by more than the tolerance. "
            "Upload-row rules raise exceptions for single rows, and the once-per-run rule for "
            "the run.</p>"
            + f"<p><b>Figures and the report.</b> Figures add up over the lists "
            f"({c('bookings_matched')} is {f(matched)}). The report's Orders tab lists the "
            f"{c('matched')} bookings, a totals row, a block of fixed rows comparing SG with "
            f"the vendor, then the {c('unmatched')} bookings with their reason; the Dashboard "
            "shows the figures. The other tabs start from the standard sheets: the "
            "exceptions, the wallet check, the ERP invoice import and copies of the "
            "uploads.</p>"
        )

    def s3(self) -> str:
        return (
            self.step(3)
            + self.do(
                "3.1",
                "<b>Configuration</b> → Travel row → <b>Duplicate</b>. Change <b>Name</b> to "
                f"{c('Travel approved')} (the key becomes {c('travel_approved')}) and click "
                "<b>Create</b>. Back on Categories, archive it: <b>Archive</b> → "
                "<b>Archive</b>. It's your way back: restore it and duplicate it as "
                f"{c('Travel')} if you ever need the approved setup again.",
            )
            + self.do(
                "3.2",
                "Optional, a reference report: <b>New reconciliation</b> → Travel, with "
                f"{c('portal.xlsx')} and {c('claim_13.xlsx')} from {c(FIXTURES)}. Keep the "
                "report to compare with at the end.",
            )
            + self.do(
                "3.3",
                "Archive Travel, tick <b>Show archived</b>, click <b>Delete</b> in its row, "
                f"type {c('travel')} and click <b>Delete</b>.",
            )
            + self.do(
                "3.4",
                f"<b>New category</b>: Name {c('Travel')}, Called in the system "
                f"{c('travel')}, Start from <b>Blank</b> → <b>Create</b>. The editor opens "
                "with one report tab, <b>Exceptions</b>: the standard exceptions table, its "
                "Status column still empty until step 13.2 (setup format 1, engine "
                f"{c('generic')} are set for you).",
            )
        )

    def s4(self) -> str:
        return (
            self.step(4)
            + self.do(
                "4.1",
                "<b>Rates and tolerances</b> (top bar) → <b>+ Rate or tolerance</b> for each "
                f"row of {c('R1')}. Type the <b>Name in formulas</b> straight away. Formulas "
                f"read each as {c('param.name')}; a rate that one run may change can be "
                "overridden on New reconciliation.",
            )
            + self.save(1)
        )

    def s5(self) -> str:
        return (
            self.step(5)
            + self.do(
                "5.1",
                "<b>+ Add an upload</b> → <b>Rows to reconcile</b>, Title "
                f"{c('Portal export')}, Called in formulas {c('portal')}, no template → "
                f"<b>Add</b>. Its sheet's key is {c('portal')} already.",
            )
            + self.do(
                "5.2",
                f"Card → <b>Settings</b>: set the upload and its sheet from {c('R2')}. "
                "Header lists are typed comma separated and set when you leave the box.",
            )
            + self.do(
                "5.3",
                f"<b>The columns</b> ({c('R3')}): <b>+ Column</b> at the right end of the "
                f"card's header row adds {c('New column')}. In the panel: <b>Name</b> "
                f"(Enter), replace {c('New column')} in <b>Header names</b> and add others "
                "with <b>Another name…</b>, <b>Read as</b>, and the two ticks. Add them in "
                "order, by hand: importing the file instead takes every header of it, in its "
                "order, which isn't the same setup.",
            )
            + self.do(
                "5.4",
                f"<b>The worked-out fields</b> ({c('R4')}): <b>+ Worked-out field</b> under "
                f"the grid adds {c('new_field')}; its <b>Name</b> in the panel, its formula in "
                "the bar at the top (<b>fx</b>). A field that replaces its column shares the "
                "column's place on the grid; it shows in the column's panel under <b>Worked "
                "out by a field of the same name</b>.",
            )
            + self.figure(
                "upload_column",
                "An upload column selected: its name, header names and how it is read, in "
                "the panel.",
            )
            + '<div class="why"><b>Why.</b> A column\'s Name is what formulas call it: SG '
            f"Order ID is {c('sg_order_id')}. A sheet is recognised by any of its header "
            "names, so a file that writes SG Commission % still reads. A field named like "
            "its column replaces it for everything after it, so rules read the cleaned "
            "value.</div>"
            + self.do(
                "5.5",
                f"The <b>Vendor claim</b> the same way ({c('vendor')}). In its Settings "
                f"({c('R5')}) type {c('claim')} over the first sheet's key, then <b>+ Sheet</b> "
                "→ <b>nothing: I add them myself</b> → <b>Add the sheet</b> for the second. "
                "Leave <b>+ Raise an exception for each row dropped</b> for step 7.2. The card "
                f"gets a tab per sheet: columns {c('R6')}, fields {c('R7')}, second sheet "
                f"{c('R8')}.",
            )
            + self.do(
                "5.6",
                "<b>+ Add an upload</b> → <b>Wallet history</b> → <b>Add</b>. It comes whole, "
                f"called {c('wallet')}; check it against {c('R9')}. Its names, sheet key and "
                "columns are fixed: the wallet check reads them by name.",
            )
            + self.do(
                "5.7",
                "<b>+ Add an upload</b> → <b>A file to look things up in</b>, Title "
                f"{c('Finance report')}, Called in formulas {c('finance')}, no template → "
                "<b>Add</b>. Then the same way as 5.2 and 5.3, from the Finance report part "
                f"of {c('R9')}: its Settings (type {c('report')} over the sheet's key) and "
                "its columns, each with <b>Must have</b> ticked. It is a file to look things "
                "up in: copied into the report, where the Orders look-up columns find each "
                "booking's cashback, and no part of the reconciliation.",
            )
            + self.figure("add_upload", "Add an upload.")
            + self.figure(
                "vendor_sheet", "Upload Settings: the vendor claim's second sheet."
            )
            + self.save(2)
        )

    def s6(self) -> str:
        return (
            self.step(6)
            + self.do(
                "6.1",
                "Orders card → <b>Start how orders are made</b> → <b>Gather rows by a key</b> "
                "→ <b>Start</b>.",
            )
            + self.do(
                "6.2",
                "<b>Rules and exceptions</b> → <b>Lists of orders and rows</b> → <b>+ List</b> "
                f"for the lists of {c('R10')} added in step 6.2: <b>Name</b> (now), <b>Made "
                "from</b>, <b>Only those where</b>, and the plain words in the top line. The "
                "rest need order fields: step 9.2.",
            )
            + self.figure("lists", "Lists, with their plain words.")
            + self.do(
                "6.3",
                f"<b>How orders are made</b>: Travel needs the three members of {c('R11')}, in "
                f"that order. Start made {c('portal')} and {c('vendor')}, which can't be "
                "renamed or moved, so replace them:"
                "<ol><li>Remove the vendor member.</li>"
                "<li><b>+ Rows of another list</b>: set it as member 1.</li>"
                "<li>Remove Start's portal member.</li>"
                "<li><b>+ Rows of another list</b>: member 2. Tick <b>One row is its own</b>, "
                "then type the formulas (<b>+ Then by</b> for each after the first) and both "
                "names.</li>"
                "<li><b>+ Rows of another list</b>: member 3.</li></ol>"
                "Leave groups, lookup tables and row fields empty; the sort waits for step "
                "10.4.",
            )
            + self.figure("members", "How orders are made: the three members.")
            + self.save(3)
        )

    def s7(self) -> str:
        return (
            self.step(7)
            + self.do(
                "7.1",
                f"<b>Rules and exceptions</b> → <b>Exception list</b>: for each code of "
                f"{c('R12')}, type it in the {c('NEW_EXCEPTION_CODE')} box → <b>Add "
                "exception</b>, then its words and <b>Severity</b> (a new code starts as "
                "Medium). Rules pick their exception from this list.",
            )
            + self.figure("exception_list", "The exception list.")
            + self.do(
                "7.2",
                "Vendor claim → <b>Settings</b> → first sheet's <b>Repeated rows</b> → <b>+ "
                "Raise an exception for each row dropped</b>, set as at the end of "
                f"{c('R12')}.",
            )
            + self.save(4)
        )

    def s8(self) -> str:
        return (
            self.step(8)
            + self.do(
                "8.1",
                f"<b>Run values</b> → <b>+ Run value</b> for each of {c('R13')}: worked out "
                "once per run (the dates the portal export covers, the claim's week). Then "
                "<b>Stop the run</b> → <b>+ Stop</b>: when one holds, the run stops with its "
                "message.",
            )
            + self.do(
                "8.2",
                f"<b>Upload rows</b> → <b>+ Row rule</b> for each of {c('R14')}. A rule's "
                "exception: <b>Raise</b> (the code), <b>Message</b>, <b>Amount</b>, and "
                "<b>Listed under</b>: no order, or the order whose key is… a formula.",
            )
            + self.save(5)
        )

    def s9(self) -> str:
        return (
            self.step(9)
            + self.do(
                "9.1",
                f"Orders card → <b>+ Field</b> for each of {c('R15')}: Name in the panel, "
                "formula in the bar. A field reads its rows by the members' names "
                f"({c('lines')}, {c('portal')}, {c('repeats')}, {c('adjustments')}), its key "
                f"as {c('key')}, and the fields above it. A wrong place: <b>Move up</b>.",
            )
            + self.save(6)
            + self.do(
                "9.2",
                f"The lists of {c('R10')} added in step 9.2, the lists of orders, the same "
                "way as in step 6.2.",
            )
            + self.save(7)
            + self.do(
                "9.3",
                f"Figures card → <b>+ Figure</b> for each of {c('R16')}: worked out once for "
                "the run, from the lists.",
            )
            + self.save(8)
        )

    def s10(self) -> str:
        return (
            self.step(10)
            + self.do(
                "10.1",
                "<b>Order status</b>: the rule Start made becomes the last rule of "
                f"{c('R17')}; change it. <b>+ Status rule</b> adds a rule just above the last, "
                "so adding rules 1, 2, 3… in turn puts them in order. Each: plain words, "
                "<b>When</b>, the verdict in the first choice's box, and its <b>Note</b>.",
            )
            + self.figure(
                "status", "Order status: plain words, condition, verdict, note."
            )
            + self.do(
                "10.2",
                f"<b>Order exceptions</b> → <b>+ Exception rule</b> for each of {c('R18')}: "
                "<b>For</b> each order (or each of an order's repeats), <b>When</b>, and its "
                "exception.",
            )
            + self.do(
                "10.3", f"<b>Once per run</b> → <b>+ Exception rule</b>: {c('R19')}."
            )
            + self.do(
                "10.4",
                "<b>How orders are made</b> → <b>The order of the orders</b>: the sort at the "
                f"end of {c('R11')}.",
            )
            + self.save(9)
        )

    def s11(self) -> str:
        return (
            self.step(11)
            + self.do(
                "11.1",
                "Wallet history → <b>Settings</b> → <b>Turn on the wallet check</b>, then "
                f"{c('R20')}. Each <b>+ Wallet type</b>: type the Wallet Type over its name "
                "(set when you leave the box) and what it means. Each booked portal row with "
                "a wallet share is looked for in the wallet history by its SG Order ID.",
            )
            + self.save(10)
        )

    def s12(self) -> str:
        return (
            self.step(12)
            + self.do(
                "12.1",
                "Report workbook card → <b>Workbook settings</b>: everything in "
                f"{c('R21')}. <b>+ Text</b> (type its key now); for the words, type the "
                f"group's name in the {c('new_group')} box → <b>Add a group of words</b>, then "
                "<b>+ Value</b> for each and type over the name it gets.",
            )
            + self.save(11)
        )

    def s13(self) -> str:
        tabs = added(self.seed)
        strip = ", ".join(t.tab.title for t in tabs)
        exceptions = next(t for t in tabs if t.present)
        if not exceptions.changes:
            raise GuideBroken("Step 13.2 sets the Exceptions table's Status")
        return (
            self.step(13)
            + self.do(
                "13.1",
                f"The tabs of {c('R22')}. Every <b>+ Tab</b> on the Report workbook card "
                "opens <b>Add a tab to the report</b>: <b>What it is</b>, a <b>Table</b> or a "
                "<b>Dashboard</b>; for a table, what it <b>Starts from</b>: <b>Empty</b>, or "
                "one of the standard sheets (Exceptions, Copy of an upload, Wallet check, "
                "Finance report, ERP invoices), and what that one asks next. Then a "
                "<b>Title</b> and a <b>Key</b> (type over the one made from the title). A new "
                "tab goes last, and its <b>Tab settings</b> move it.",
            )
            + self.figure(
                "add_tab", "Add a tab: a table starting from a copy of an upload."
            )
            + self.do(
                "13.2",
                "<b>Exceptions</b> is there already. Click its <b>Status</b> column and type "
                f"its formula in the bar, from {c('R29')}: what an order's status is, written "
                "beside each of its exceptions.",
            )
            + self.do(
                "13.3",
                "<b>Orders</b>: <b>+ Tab</b> → <b>Table</b>, Start from <b>Empty</b>, The rows "
                f"it lists {c('matched')}. Make its {c('New column')} column 1 of {c('R23')}, "
                "then <b>+ Column</b> for each other: <b>Header</b>, <b>Key</b>, <b>Shown "
                "as</b>, <b>Width</b>, <b>Band</b> in the panel, the formula in the bar; a diff "
                "column also has <b>Shade the difference above</b>.",
            )
            + self.figure(
                "orders_column",
                "An Orders column: header, key, shown as, width and band in the panel; its "
                "formula in the bar.",
            )
            + self.do(
                "13.4",
                "Orders → <b>Tab settings</b> → <b>Sheet layout</b>: part 1 is there, add parts "
                f"2 to 4 of {c('R25')}. Tick each part's <b>Also</b> boxes as {c('R25')} has "
                "them, all but <b>Fills the look-up columns</b>: the look-up columns come in "
                "13.8, once the Finance Report tab they look in is there. Fixed rows "
                "start with one empty row: <b>+ Row</b> for each "
                "other, <b>+ Cell</b> for each cell: its column, merge, what it holds (Text, "
                "Figure, Excel formula or Worked out), the value and the format; "
                f"{c('{col:order_total}')} is that column's letter and {c('{row:totals}')} the "
                "totals row.",
            )
            + self.figure("sheet_layout", "Sheet layout: the Orders tab's parts.")
            + self.do(
                "13.5",
                "<b>Dashboard</b>: <b>+ Tab</b> → <b>Dashboard</b>. Its first block is there: "
                f"click its name and set block 1 of {c('R26')}; click each header cell for "
                "Header, Figure, Written as; <b>+ Value</b> and <b>+ Block</b> for the rest. "
                f"Then <b>+ Breakdown table</b> and {c('R27')}: click its first header for the "
                "table's settings; its first column, Order Count, is there; <b>+ Column</b> "
                "for each other, and click a column for its own settings.",
            )
            + self.figure(
                "dashboard",
                "The dashboard designer: blocks of figures, and the breakdown table with its "
                "settings in the panel.",
            )
            + self.do(
                "13.6",
                "Move Dashboard and Orders before Exceptions: Dashboard's Tab settings → "
                "<b>← Move</b> twice, Orders' → <b>← Move</b> once.",
            )
            + self.save(12)
            + self.do(
                "13.7",
                f"The rest, in order, from {c('R22')}: <b>Wallet</b> (Start from Wallet "
                "check); <b>B2C Invoices ERP</b> (Start from ERP invoices, The rows it bills "
                f"{c('matched')}), then the changes of {c('R28')}: click each column it names "
                "for its formula in the bar or its Excel formula in the panel, and its Tab "
                "settings → Sheet layout for part 1's <b>Contact names</b>; <b>Portal</b>, "
                "<b>Vendor</b> and <b>Finance Report</b> (Start from Copy of an upload, with "
                "its <b>Upload</b> and <b>Its sheet</b>), then each one's Tab settings → "
                f"<b>Header row</b> from {c('R29')}. Type each "
                f"Title and Key as {c('R22')} has them. Check the tab strip reads "
                f"{esc(strip)}.",
            )
            + self.figure(
                "erp_layout",
                "The ERP tab's sheet layout: the rows it bills, and where its contact names "
                "come from.",
            )
            + '<div class="why"><b>Copies.</b> A copy writes its upload as it arrived. Its '
            "Tab settings can leave columns out (<b>+ Column left out</b>, by header; the "
            "columns after one move left), and <b>+ Column</b> on its grid adds columns after "
            "the upload's last. Travel's copies do neither.</div>"
            + self.figure(
                "copy_settings",
                "A copy's Tab settings: the upload it copies, its header row and the columns "
                "it leaves out.",
            )
            + self.do(
                "13.8",
                "Orders → <b>Tab settings</b> → <b>Look-up columns</b> → <b>+ Look-up "
                f"column</b>, once for each column of {c('R24')}, in order: its <b>Header</b> "
                "and <b>Key</b>, <b>Looked up in</b>, <b>Find each row by</b>, <b>In its "
                f"column</b> and <b>Bring back</b> as {c('R24')} has them → <b>Add the "
                "column</b>; then, in the column's panel, its <b>Shown as</b>, <b>Width</b> "
                "and <b>Band</b>. A new one starts looking where the one before it looks. "
                "Then <b>Sheet layout</b>: tick <b>Fills the look-up columns</b> on the parts "
                f"{c('R25')} ticks it on.",
            )
            + '<div class="why"><b>Look-up columns.</b> Each cell is an Excel lookup of '
            "the booking's SG Order ID in the Finance Report sheet, so clicking it in the "
            "report shows where the cashback comes from. No rule or figure reads them, and "
            "without a finance report they are left out.</div>"
            + self.figure(
                "lookup_columns",
                "The Orders tab's look-up columns: where they look and how each row is found.",
            )
            + self.save(13)
        )

    def s14(self) -> str:
        return (
            self.step(14)
            + self.do(
                "14.1",
                "In a terminal:"
                '<pre class="block">cd business-dashboard-backend/backend\n'
                "uv run python -m scripts.compare_category travel</pre>"
                f"It prints {c('travel is identical to app/seed/v1/travel.json.')} or lists "
                "each difference by place, e.g. "
                f"{c('model.fields[portal_base].formula')} with what was expected and what "
                "was found; “(order)” means all are there but in another order. Fix, save, "
                "run it again. It only reads the database.",
            )
            + self.do(
                "14.2",
                "Run Travel on the same files as the reference report; the two reports "
                "should match.",
            )
        )
