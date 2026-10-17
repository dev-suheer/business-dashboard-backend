"""The vocabulary shared by every category's pipeline.

An upload and the one error that means "this file is not usable" are the only
things the API layer and a category pipeline both need to name. They live here
rather than in one category's engine so that a second category can speak the
same language without importing the first one's machinery.
"""

from dataclasses import dataclass
from pathlib import Path


class ReconInputError(ValueError):
    """An upload is not usable: wrong shape, wrong sheet, or self-contradictory."""


@dataclass(frozen=True)
class Upload:
    """One uploaded workbook, already streamed to disk."""

    role: str
    filename: str
    path: Path
