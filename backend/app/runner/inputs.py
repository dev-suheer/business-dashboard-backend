"""Reading an upload into rows, as its configuration describes.

Every sheet of the file that matches a configured sheet is read. Each column
is typed as configured, a row missing a must-have value is skipped (a totals
line, a spacer), the sheet's own fields are worked out, and a row the sheet's
filter rejects is not read at all. A row that repeats an earlier one, by the
sheet's ``unique`` keys, is dropped (and reported, if it says so), and a row a
``reject`` holds for refuses the whole upload.
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from openpyxl.workbook import Workbook

from app.config_model.models import InputFile, InputSheet, Raise
from app.config_model.runtime import schema_of
from app.expr import Scope, Values, evaluate, text, truthy
from app.recon.cleaning import clean_text, id_text, rate_fraction, to_date, to_decimal
from app.recon.excel import (
    SheetFormatError,
    detect_sheet,
    detect_sheets,
    iter_records,
    open_workbook,
)
from app.recon.exceptions import ReconException, make_exception
from app.recon.inputs import ReconInputError, Upload
from app.recon.models import SheetManifest
from app.runner.result import Row


def _money(value: Any) -> Decimal:
    # A blank amount is zero. ``or`` also turns a written 0.00 into 0, as the
    # v1 readers did, so a zero prints the same way in every message.
    return to_decimal(value) or Decimal(0)


READERS: dict[str, Callable[[Any], Any]] = {
    "text": clean_text,
    "id": id_text,
    "date": to_date,
    "money": _money,
    "decimal": to_decimal,
    "rate": rate_fraction,
}


def read_upload(
    spec: InputFile,
    upload: Upload,
    *,
    max_rows: int,
    scope: Scope,
    detected: list[SheetManifest],
    exceptions: list[ReconException],
) -> dict[str, list[Row]]:
    """The upload's rows, by the row set each sheet's rows join."""
    rows: dict[str, list[Row]] = {}
    with open_workbook(upload.path) as workbook:
        for sheet in spec.sheets:
            schema = schema_of(sheet)
            if sheet.first_only:
                first = detect_sheet(workbook, schema)
                found = [first] if first is not None else []
            else:
                found = detect_sheets(workbook, schema)
            if not found:
                if sheet.optional:
                    continue
                raise ReconInputError(
                    f"No {schema.name} sheet found in {upload.filename!r} "
                    f"uploaded as {upload.role!r}.{sheet.missing_hint}"
                )
            read = _read_sheet(
                spec.role, sheet, workbook, found, max_rows, scope, exceptions
            )
            rows.setdefault(sheet.set or spec.role, []).extend(read)
            counted = sum(
                1 for row in read if truthy(evaluate(sheet.counted, Values(row, scope)))
            )
            detected.append(
                SheetManifest(
                    role=sheet.manifest_role or spec.role,
                    filename=upload.filename,
                    sheets=found,
                    rows=counted,
                )
            )
    if spec.empty_message and not any(rows.values()):
        raise ReconInputError(
            spec.empty_message.replace("{filename}", repr(upload.filename))
        )
    return rows


def _exception(spec: Raise, item: Scope) -> ReconException:
    return make_exception(
        spec.code,
        order_no=text(evaluate(spec.order_no, item)) if spec.order_no else None,
        amount=evaluate(spec.amount, item) if spec.amount else None,
        detail=text(evaluate(spec.detail, item)),
    )


def complete(sheet: InputSheet, values: dict[str, Any], scope: Scope) -> dict[str, Any]:
    """Work out the sheet's own fields on a row whose columns are read."""
    item = Values(values, parent=scope)
    for named in sheet.fields:
        values[named.key] = evaluate(named.formula, item)
    return values


def _read_sheet(
    role: str,
    sheet: InputSheet,
    workbook: Workbook,
    names: list[str],
    max_rows: int,
    scope: Scope,
    exceptions: list[ReconException],
) -> list[Row]:
    schema = schema_of(sheet)
    must_have = [c.field for c in sheet.columns if c.skip_if_blank]
    unique = sheet.unique
    seen: set[tuple[str, ...]] = set()
    rows: list[Row] = []
    for name in names:
        for index, record in enumerate(
            iter_records(workbook[name], schema, max_rows=max_rows), start=1
        ):
            values: dict[str, Any] = {
                c.field: READERS[c.type](record.get(c.field)) for c in sheet.columns
            }
            if any(values[f] is None for f in must_have):
                continue
            values["sheet"] = name
            values["row"] = Decimal(index)
            item = Values(complete(sheet, values, scope), parent=scope)
            if sheet.filter and not truthy(evaluate(sheet.filter, item)):
                continue
            if unique is not None and truthy(evaluate(unique.when, item)):
                key = tuple(text(evaluate(k, item)) for k in unique.keys)
                if key in seen:
                    if unique.exception is not None:
                        exceptions.append(_exception(unique.exception, item))
                    continue
                seen.add(key)
            for reject in sheet.rejects:
                if truthy(evaluate(reject.when, item)):
                    raise SheetFormatError(text(evaluate(reject.message, item)))
            rows.append(Row(role, name, index, values))
    return rows
