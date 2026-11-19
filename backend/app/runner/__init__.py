"""The generic runner: any category, from its configuration."""

from app.runner.result import Entity, Row, RunResult
from app.runner.run import reconcile, run

__all__ = ["Entity", "Row", "RunResult", "reconcile", "run"]
