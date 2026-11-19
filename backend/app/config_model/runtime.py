"""The configuration a run is reconciling under.

The category pipelines read sheet schemas, severities and labels from deep
inside their parsers and exporters. Rather than thread a configuration through
every call, the run service activates one for the duration of a run, and each
of those places asks for its piece of it here, falling back to the code's own
value when no configuration is active (as in unit tests of a single parser).

A ``ContextVar`` keeps concurrent runs apart: each run executes in its own
thread with its own context.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from app.config_model.models import CategoryConfig, InputSheet
from app.recon.excel import ColumnSpec, SheetSchema

_ACTIVE: ContextVar[CategoryConfig | None] = ContextVar("active_config", default=None)


@contextmanager
def use_config(config: CategoryConfig) -> Iterator[CategoryConfig]:
    token = _ACTIVE.set(config)
    try:
        yield config
    finally:
        _ACTIVE.reset(token)


def active_config() -> CategoryConfig | None:
    return _ACTIVE.get()


def schema_of(sheet: InputSheet) -> SheetSchema:
    """A configured sheet as the readers take it."""
    return SheetSchema(
        name=sheet.name,
        columns=tuple(
            ColumnSpec(c.field, tuple(c.headers), required=c.required)
            for c in sheet.columns
        ),
        fingerprint=tuple(sheet.fingerprint),
        exclude=tuple(sheet.exclude),
    )


def sheet_schema(role: str, key: str, default: SheetSchema) -> SheetSchema:
    """How to recognise and read one kind of sheet in the ``role`` upload."""
    config = _ACTIVE.get()
    sheet = config.sheet(role, key) if config else None
    return default if sheet is None else schema_of(sheet)


def severity(code: str, default: str) -> str:
    config = _ACTIVE.get()
    return (config.severity(code) if config else None) or default


def label(group: str, value: str, default: str) -> str:
    config = _ACTIVE.get()
    return config.labels.get(group, {}).get(value, default) if config else default
