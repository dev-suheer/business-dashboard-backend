"""The function library as the editor lists it: how each is called, and what it does."""

from app.expr.functions import ARITY

# name -> (how it is called, what it does)
FUNCTIONS: dict[str, tuple[str, str]] = {
    "IF": (
        "IF(condition, then, else)",
        "then when the condition holds, else otherwise (blank if left out)",
    ),
    "IFS": (
        "IFS(condition, value, …)",
        "the value of the first condition that holds; blank if none does",
    ),
    "AND": ("AND(condition, …)", "whether every condition holds"),
    "OR": ("OR(condition, …)", "whether any condition holds"),
    "NOT": ("NOT(condition)", "whether the condition does not hold"),
    "SUMX": (
        'SUMX("set", value)',
        "the value added up over every member of the set, blanks left out",
    ),
    "COUNTX": (
        'COUNTX("set", condition)',
        "how many members of the set the condition holds for; how many members, without one",
    ),
    "MINX": ('MINX("set", value)', "the smallest value over the set"),
    "MAXX": ('MAXX("set", value)', "the largest value over the set"),
    "ONLYX": (
        'ONLYX("set", value)',
        "the one value every member shares; blank when they differ",
    ),
    "FIRSTX": ('FIRSTX("set", value)', "the value on the set's first member"),
    "LASTX": ('LASTX("set", value)', "the value on the set's last member"),
    "COALESCEX": (
        'COALESCEX("set", value)',
        "the first value over the set that is not blank",
    ),
    "DISTINCTX": ('DISTINCTX("set", value)', "how many different values the set holds"),
    "SUMIF": (
        'SUMIF("set", value, "criteria", amount)',
        "the amount added up over the members whose value meets the criteria: "
        '"Delivered" (any case), "<>Cancelled", ">1,000", ">=0.5", ">50%", '
        '">=2026-08-01" (dates as YYYY-MM-DD), "Food*" (* and ? match text only), '
        '"" or a blank value for blank, "<>" for not blank; the value itself '
        "without an amount",
    ),
    "SUMIFS": (
        'SUMIFS("set", amount, value, "criteria", …)',
        "the amount added up over the members that meet every criteria",
    ),
    "COUNTIF": (
        'COUNTIF("set", value, "criteria")',
        "how many members' value meets the criteria, written as for SUMIF "
        '("Delivered", ">100", "<>")',
    ),
    "COUNTIFS": (
        'COUNTIFS("set", value, "criteria", …)',
        "how many members meet every criteria",
    ),
    "JOINX": (
        'JOINX("set", value, ", ", sorted)',
        "the set's different values joined into one text, sorted if asked",
    ),
    "LABEL": (
        'LABEL("group", value)',
        "the wording the setup gives a value, e.g. a verdict's label",
    ),
    "LOOKUP": (
        'LOOKUP("table", key)',
        "the value a lookup table holds for the key; blank if none",
    ),
    "PREFIXCOUNT": (
        'PREFIXCOUNT("table", text)',
        "how many of the table's keys start with the text",
    ),
    "PREFIXKEYS": (
        'PREFIXKEYS("table", text)',
        "the table's keys that start with the text, joined",
    ),
    "MATCHPREFIX": (
        'MATCHPREFIX("table", text, amount, tolerance)',
        "the one key the text starts, the amount deciding between several; blank otherwise",
    ),
    "N": ("N(value)", "the value, or 0 when it is blank"),
    "ISBLANK": ("ISBLANK(value)", "whether the value is blank"),
    "BLANK": ("BLANK()", "a blank"),
    "COALESCE": ("COALESCE(value, …)", "the first value that is not blank"),
    "ABS": ("ABS(number)", "the number without its sign"),
    "ROUND": ("ROUND(number, digits)", "the number rounded half up"),
    "MONEY": ("MONEY(number)", "the number rounded half up to cents"),
    "MIN": ("MIN(number, …)", "the smallest number"),
    "MAX": ("MAX(number, …)", "the largest number"),
    "SUM": ("SUM(number, …)", "the numbers added up"),
    "LOWER": ("LOWER(text)", "the text in lower case"),
    "UPPER": ("UPPER(text)", "the text in upper case"),
    "TRIM": ("TRIM(text)", "the text without spaces at either end"),
    "CLEAN": (
        "CLEAN(text)",
        'the text as the readers clean it: spacing evened out, "-" read as blank',
    ),
    "TEXT": ("TEXT(value)", "the value as text"),
    "PLAIN": ("PLAIN(number)", "the number as text, never in scientific notation"),
    "PERCENT": ("PERCENT(rate, digits)", "a rate as a percentage text: 0.05 is 5.00%"),
    "LEN": ("LEN(text)", "how many characters the text has"),
    "EXACT": ("EXACT(a, b)", "whether two texts are the same, case included"),
    "IN": (
        "IN(value, option, …)",
        "whether the value is one of the options, case aside",
    ),
    "CONTAINS": ("CONTAINS(text, part)", "whether the text holds the part, case aside"),
    "REGEX": (
        'REGEX(text, "pattern", group)',
        "the part of the text a regular expression matches",
    ),
    "TEXTBEFORE": ("TEXTBEFORE(text, delimiter)", "the text before the delimiter"),
    "TEXTAFTER": ("TEXTAFTER(text, delimiter)", "the text after the delimiter"),
    "SUBSTITUTE": (
        "SUBSTITUTE(text, old, new)",
        "the text with every old part replaced",
    ),
    "QUOTE": ("QUOTE(text)", "the text in quotes, as messages show a sheet name"),
    "DATEVALUE": (
        'DATEVALUE(text, "%y%m%d")',
        "the text read as a date in that pattern; blank when it is not one",
    ),
    "INTEGER": (
        "INTEGER(text)",
        "the text read as a whole number; blank when it is not one",
    ),
    "IDTEXT": ("IDTEXT(cell)", 'a cell as an order id: 8986.0 is "8986"'),
    "ISNUMBER": ("ISNUMBER(value)", "whether the value is a number"),
    "ISTEXT": ("ISTEXT(value)", "whether the value is text"),
    "ISDATE": ("ISDATE(value)", "whether the value is a date"),
}

assert set(FUNCTIONS) == set(ARITY), set(FUNCTIONS) ^ set(ARITY)
