"""The categories, and each one's configuration: read, check, save.

The New reconciliation screen builds its upload boxes from the list, so a
category added later, or a file added to one, appears there without a frontend
change. The editor reads a category's setup, checks its edits, reads a file it
is given for its sheets and headers (keeping nothing), and saves: a save is a
new version, which the next run uses.
"""

import dataclasses
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.routes._uploads import spool
from app.config_model.check import lineage, problems
from app.config_model.models import CategoryConfig, InputFile, OutputTab
from app.config_model.rename import RenameError, rename
from app.config_model.seed import wallet_upload
from app.config_model.standard_tabs import (
    copy_tab,
    erp_invoices_tab,
    exceptions_tab,
    wallet_tab,
)
from app.db.models import Category, CategoryVersion, Run
from app.db.session import get_session
from app.expr.functions import ARITY
from app.expr.help import FUNCTIONS
from app.recon.excel import SheetFormatError
from app.services import configs, templates
from app.services.trace import Target, TraceTargetError, trace

router = APIRouter(
    prefix="/categories", tags=["categories"], dependencies=[Depends(get_current_user)]
)
functions_router = APIRouter(
    prefix="/functions", tags=["categories"], dependencies=[Depends(get_current_user)]
)
standard_router = APIRouter(
    prefix="/standard", tags=["categories"], dependencies=[Depends(get_current_user)]
)

SessionDep = Annotated[Session, Depends(get_session)]


class InputRolePublic(BaseModel):
    role: str
    title: str
    caption: str
    required: bool
    # False for a file the API accepts but the upload screen does not offer.
    shown: bool


class CategoryPublic(BaseModel):
    key: str
    name: str
    description: str
    # The configuration version a run started now would use.
    version: int
    saved_at: datetime
    note: str
    # Whether it can reconcile yet: a new category has no rules until they are set.
    ready: bool
    inputs: list[InputRolePublic]
    # Archived: kept, but no run can start on it; restore it to run it.
    archived: bool = False
    # What deleting it would remove besides its versions.
    runs: int = 0


class DeletedPublic(BaseModel):
    versions: int
    runs: int


class ProblemPublic(BaseModel):
    path: str
    message: str


class UsePublic(BaseModel):
    path: str
    formula: str
    uses: list[str]
    # Where each name it reads comes from: model.fields[3],
    # inputs[0].sheets[1].columns[2], model.metrics[4], model.key.
    reads: list[str]


class ConfigPublic(BaseModel):
    key: str
    name: str
    version: int
    saved_at: datetime
    note: str
    config: CategoryConfig


class VersionPublic(BaseModel):
    number: int
    note: str
    saved_at: datetime
    current: bool


class SaveRequest(BaseModel):
    config: CategoryConfig
    note: str = ""
    # The version the edit started from; saving fails if another was saved since.
    base_version: int


class CheckRequest(BaseModel):
    config: CategoryConfig


class CheckPublic(BaseModel):
    problems: list[ProblemPublic]
    lineage: list[UsePublic]


class RenameRequest(BaseModel):
    config: CategoryConfig
    # What is renamed: model.fields[3], model.metrics[2],
    # inputs[0].sheets[1].fields[2], model.row_fields[0].fields[1],
    # output.tabs[1].columns[5].
    path: str
    name: str


class RenamePublic(BaseModel):
    config: CategoryConfig
    # Every part of the setup the rename changed.
    changed: list[str]


class TraceTargetBody(BaseModel):
    """What to trace: a table column by its key, a dashboard cell by its block
    and place, or a breakdown column by its place ("name" for its names)."""

    tab: str
    column: str | None = None
    block: int | None = None
    value: int | None = None
    breakdown: int | str | None = None


class TraceRequest(BaseModel):
    config: CategoryConfig
    target: TraceTargetBody


class StepPublic(BaseModel):
    name: str
    kind: str
    # Where it comes from, in a few words.
    where: str | None
    formula: str | None
    # The editor element to go to.
    id: str | None
    # Already shown above in the same trace.
    repeat: bool
    children: list["StepPublic"]


class TracePublic(BaseModel):
    title: str
    root: StepPublic


class NewCategory(BaseModel):
    key: str
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    # Start from a copy of this category's current setup; blank when left out.
    copy_of: str | None = None


class TemplateColumn(BaseModel):
    header: str
    # The configured column this header feeds, when the sheet is a known one.
    field: str | None
    suggested_field: str
    suggested_type: str


class TemplateSheet(BaseModel):
    name: str
    # The configured sheet it is, if any.
    kind: str | None
    header_row: int | None
    columns: list[TemplateColumn]


class FileSheetsPublic(BaseModel):
    """A file read for its sheets and headers; the file is not kept."""

    filename: str
    sheets: list[TemplateSheet]


class FunctionPublic(BaseModel):
    name: str
    call: str
    description: str
    min_args: int
    max_args: int | None


class StandardPublic(BaseModel):
    # The wallet history upload, which the wallet check reads by its column names.
    wallet_upload: InputFile
    # The tables a report tab can start from, by name: the approved sheets.
    tabs: dict[str, OutputTab]


def _public(category: Category, runs: int = 0) -> CategoryPublic | None:
    version = category.current_version
    if version is None:
        return None
    config = configs.config_of(version)
    return CategoryPublic(
        key=category.key,
        name=category.name,
        description=category.description,
        version=version.number,
        saved_at=version.created_at,
        note=version.note,
        ready=config.model is not None,
        inputs=[
            InputRolePublic(
                role=f.role,
                title=f.title,
                caption=f.caption,
                required=f.required,
                shown=f.shown,
            )
            for f in config.inputs
        ],
        archived=not category.active,
        runs=runs,
    )


def _category(session: Session, key: str) -> Category:
    try:
        return configs.category(session, key)
    except configs.ConfigurationError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


def _invalid(exc: configs.ConfigInvalid) -> HTTPException:
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        {
            "message": str(exc),
            "problems": [{"path": p.path, "message": p.message} for p in exc.problems],
        },
    )


def _counts(session: Session, column: Any) -> dict[int, int]:
    """How many rows of a table each category has, by its id."""
    rows = session.execute(select(column, func.count()).group_by(column))
    return {row[0]: row[1] for row in rows}


@router.get("", response_model=list[CategoryPublic])
def read_categories(
    session: SessionDep, archived: bool = False
) -> list[CategoryPublic]:
    """The categories runs can use; with ``archived``, the archived ones too."""
    query = select(Category).order_by(Category.id)
    if not archived:
        query = query.where(Category.active)
    runs = _counts(session, Run.category_id)
    return [
        p
        for c in session.scalars(query)
        if (p := _public(c, runs.get(c.id, 0))) is not None
    ]


@router.post("", response_model=CategoryPublic, status_code=status.HTTP_201_CREATED)
def create_category(body: NewCategory, session: SessionDep) -> CategoryPublic:
    """A new category at version 1: blank, or a copy of another's setup."""
    try:
        created = configs.create(
            session, body.key, body.name, body.description, copy_of=body.copy_of
        )
    except configs.ConfigInvalid as exc:
        raise _invalid(exc) from exc
    except configs.ConfigurationError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    public = _public(created)
    assert public is not None
    return public


@router.post("/{key}/archive", response_model=CategoryPublic)
def archive_category(key: str, session: SessionDep) -> CategoryPublic:
    """Hide a category from runs and the categories list. Its versions and
    runs are kept, and it can be restored."""
    _category(session, key)
    public = _public(configs.archive(session, key))
    assert public is not None
    return public


@router.post("/{key}/restore", response_model=CategoryPublic)
def restore_category(key: str, session: SessionDep) -> CategoryPublic:
    _category(session, key)
    public = _public(configs.restore(session, key))
    assert public is not None
    return public


@router.delete("/{key}", response_model=DeletedPublic)
def delete_category(key: str, confirm: str, session: SessionDep) -> DeletedPublic:
    """Delete an archived category for good, with its versions and every run
    of it, their uploads and reports. ``confirm`` repeats the key."""
    _category(session, key)
    if confirm != key:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Repeat the category's key in 'confirm' to delete it",
        )
    try:
        deleted = configs.delete(session, key)
    except configs.ConfigInvalid as exc:
        raise _invalid(exc) from exc
    return DeletedPublic(versions=deleted.versions, runs=deleted.runs)


@router.get("/{key}/config", response_model=ConfigPublic)
def read_config(key: str, session: SessionDep) -> ConfigPublic:
    """The setup runs use now."""
    category = _category(session, key)
    version = category.current_version
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{key} has no setup yet")
    return ConfigPublic(
        key=category.key,
        name=category.name,
        version=version.number,
        saved_at=version.created_at,
        note=version.note,
        config=configs.config_of(version),
    )


@router.put("/{key}/config", response_model=VersionPublic)
def save_config(key: str, body: SaveRequest, session: SessionDep) -> VersionPublic:
    """Save an edited setup as the next version. The next run uses it.

    Refused (409) when another version was saved since ``base_version``, and
    (422) with each problem and where it is when the setup does not check out.
    """
    _category(session, key)
    try:
        saved = configs.save(session, key, body.config, body.note, body.base_version)
    except configs.ConfigConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except configs.ConfigInvalid as exc:
        raise _invalid(exc) from exc
    return VersionPublic(
        number=saved.number, note=saved.note, saved_at=saved.created_at, current=True
    )


@router.post("/{key}/check", response_model=CheckPublic)
def check_config(key: str, body: CheckRequest, session: SessionDep) -> CheckPublic:
    """What is wrong with an edited setup, and what each of its formulas reads."""
    _category(session, key)
    return CheckPublic(
        problems=[
            ProblemPublic(path=p.path, message=p.message) for p in problems(body.config)
        ],
        lineage=[
            UsePublic(path=u.path, formula=u.formula, uses=u.uses, reads=u.reads)
            for u in lineage(body.config)
        ],
    )


@router.post("/{key}/rename", response_model=RenamePublic)
def rename_in_config(
    key: str, body: RenameRequest, session: SessionDep
) -> RenamePublic:
    """Rename a field, a figure or a column, and every formula, total and block
    that uses it. Nothing is saved: the setup comes back renamed, to be edited
    on and saved as usual. A rename that would change what a formula reads is
    refused (422), with where."""
    _category(session, key)
    try:
        renamed = rename(body.config, body.path, body.name)
    except RenameError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"message": exc.message, "path": exc.path},
        ) from exc
    return RenamePublic(config=renamed.config, changed=renamed.changed)


@router.post("/{key}/trace", response_model=TracePublic)
async def trace_config(
    key: str, body: TraceRequest, session: SessionDep
) -> TracePublic:
    """How a report column, figure or breakdown column of an edited setup is
    worked out, step by step, down to the uploads' columns, the status rules
    and the rates. No file is read."""
    _category(session, key)
    target = Target(**body.target.model_dump())
    try:
        found = await run_in_threadpool(trace, body.config, target)
    except TraceTargetError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return TracePublic.model_validate(dataclasses.asdict(found))


@router.get("/{key}/versions", response_model=list[VersionPublic])
def read_versions(key: str, session: SessionDep) -> list[VersionPublic]:
    category = _category(session, key)
    return [
        VersionPublic(
            number=v.number,
            note=v.note,
            saved_at=v.created_at,
            current=v.id == category.current_version_id,
        )
        for v in configs.versions(session, category)
    ]


@router.get("/{key}/versions/{number}", response_model=ConfigPublic)
def read_version(key: str, number: int, session: SessionDep) -> ConfigPublic:
    category = _category(session, key)
    try:
        version: CategoryVersion = configs.version(session, category, number)
    except configs.ConfigurationError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return ConfigPublic(
        key=category.key,
        name=category.name,
        version=version.number,
        saved_at=version.created_at,
        note=version.note,
        config=configs.config_of(version),
    )


@router.post("/{key}/sheets/{role}", response_model=FileSheetsPublic)
async def read_file_sheets(
    key: str,
    role: str,
    file: Annotated[UploadFile, File(description="A file of this upload.")],
    session: SessionDep,
) -> FileSheetsPublic:
    """Read a file for its sheets and headers, with the configured column each
    header feeds when the upload is set up already: to start an upload's
    columns, or add a sheet, from it. Nothing is kept."""
    category = _category(session, key)
    if not configs.KEY.match(role):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Name an upload with lowercase letters, digits or _",
        )
    version = category.current_version
    config = configs.config_of(version) if version else configs.blank(key)
    workdir = Path(tempfile.mkdtemp(prefix="recon-sheets-"))
    try:
        upload = await spool(file, role, workdir)
        sheets = await run_in_threadpool(
            templates.read_sheets, upload.path, config.input(role)
        )
        return FileSheetsPublic(
            filename=upload.filename,
            sheets=[TemplateSheet.model_validate(s) for s in sheets],
        )
    except SheetFormatError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@functions_router.get("", response_model=list[FunctionPublic])
def read_functions() -> list[FunctionPublic]:
    """The functions a formula can call, as the editor's help lists them."""
    return [
        FunctionPublic(
            name=name,
            call=call,
            description=description,
            min_args=ARITY[name][0],
            max_args=ARITY[name][1],
        )
        for name, (call, description) in sorted(FUNCTIONS.items())
    ]


@standard_router.get("", response_model=StandardPublic)
def read_standard() -> StandardPublic:
    """The ready-made pieces the editor adds: the wallet history upload, which
    the wallet check reads by its names, and the tables a report tab can start
    from."""
    return StandardPublic(
        wallet_upload=wallet_upload(),
        tabs={
            # Its Status is left blank: what an order's status is depends on
            # the category's own fields.
            "exceptions": exceptions_tab("exceptions", "Exceptions", status='""'),
            # Its rows and its unit price's cell are the category's to name.
            "erp_invoices": erp_invoices_tab(
                "b2c_invoices",
                "B2C Invoices ERP",
                unit_cell="orders.unit_price",
                partner='""',
                order_id="key",
                bill_date="BLANK()",
                product='""',
            ),
            "wallet": wallet_tab("wallet", "Wallet"),
            # The upload and the sheet it copies are the category's to name.
            "copy": copy_tab("copy", "Copy", source="", sheet=""),
        },
    )
