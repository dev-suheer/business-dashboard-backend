"""Models every reconciliation shares."""

from pydantic import BaseModel


class SheetManifest(BaseModel):
    """Which sheet was chosen for which role, so misdetection is visible."""

    role: str
    filename: str
    sheets: list[str]
    rows: int
