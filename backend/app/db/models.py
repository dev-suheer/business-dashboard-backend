"""Every table, imported in one place so Alembic sees them all."""

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Account(Base):
    """A login: its username and the hash of its password, never the password.
    Seeded from ``app/seed/users.json``."""

    __tablename__ = "account"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Category(Base):
    """A kind of reconciliation: eShop, Travel, Food, and any added later."""

    __tablename__ = "category"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    active: Mapped[bool] = mapped_column(default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # The configuration runs use now. Saving a change adds a version and moves this.
    current_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("category_version.id", use_alter=True)
    )

    current_version: Mapped["CategoryVersion | None"] = relationship(
        foreign_keys=[current_version_id], post_update=True, lazy="joined"
    )


class CategoryVersion(Base):
    """One saved configuration of a category. Never edited: a change is a new version.

    Version 1 of each category is the approved v1 behaviour, generated from the
    code (``app/seed/v1/``).
    """

    __tablename__ = "category_version"
    __table_args__ = (UniqueConstraint("category_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("category.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    # A CategoryConfig (app/config_model/models.py), as JSON.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Run(Base):
    """One reconciliation, kept for later review.

    Its id is the run number the user sees (Run 0042). A failed run is kept
    too, with the reason, so a file that did not match its setup is on record.
    """

    __tablename__ = "run"
    __table_args__ = (
        CheckConstraint(
            "status in ('running', 'completed', 'failed')", name="status_known"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("category.id"), index=True)
    # The configuration the run was reconciled under.
    version_id: Mapped[int | None] = mapped_column(ForeignKey("category_version.id"))
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.RUNNING)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    # The rates and tolerances the run used, after any per-run override.
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    # Key figures for the history list: counted, not counted, net payable…
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # {"by_severity": {"high": 5, …}, "by_code": {"BOOKING_BASE_MISMATCH": 1, …}}
    exception_counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)

    report_filename: Mapped[str | None] = mapped_column(String(255))
    report_path: Mapped[str | None] = mapped_column(Text)
    report_sha256: Mapped[str | None] = mapped_column(String(64))
    report_size: Mapped[int | None] = mapped_column(BigInteger)
    result_path: Mapped[str | None] = mapped_column(Text)

    category: Mapped[Category] = relationship(lazy="joined")
    version: Mapped[CategoryVersion | None] = relationship(lazy="joined")
    files: Mapped[list["RunFile"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="RunFile.id",
        lazy="selectin",
    )


class RunFile(Base):
    """One uploaded workbook of a run, kept as it arrived."""

    __tablename__ = "run_file"
    __table_args__ = (UniqueConstraint("run_id", "role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("run.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(BigInteger)
    # Proves later that the stored file is the one that was reconciled.
    sha256: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(Text)
    # Which sheets were read for this role, and how many rows.
    manifest: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    run: Mapped[Run] = relationship(back_populates="files")
