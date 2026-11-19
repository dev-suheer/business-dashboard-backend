"""Upload plumbing shared by the endpoints that take workbooks: runs, and a
file read for its sheets.

Uploads are streamed to temporary files rather than read into memory: openpyxl
needs a seekable file, and the whole point of streaming is that a workbook is
never fully resident. Both endpoints need the same spooling and the same size
guard, so it lives here once.
"""

from pathlib import Path

from fastapi import HTTPException, status

# Starlette's, which FastAPI's subclasses: /runs reads its files from the raw form.
from starlette.datastructures import UploadFile

from app.core.config import settings
from app.recon.inputs import Upload

# 1 MiB at a time: large enough that the syscall overhead is irrelevant, small
# enough that the buffer never matters.
CHUNK_SIZE = 1024 * 1024

ALLOWED_SUFFIXES = frozenset({".xlsx", ".xlsm", ".xltx"})

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def spool(file: UploadFile, role: str, into: Path) -> Upload:
    """Stream an upload to disk, rejecting it the moment it grows too large.

    The size is checked as the bytes arrive rather than afterwards.
    """
    filename = file.filename or f"{role}.xlsx"
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"{role!r} must be an Excel workbook ({', '.join(sorted(ALLOWED_SUFFIXES))}); "
                f"got {filename!r}"
            ),
        )

    path = into / f"{role}{suffix}"
    written = 0
    with path.open("wb") as sink:
        while chunk := await file.read(CHUNK_SIZE):
            written += len(chunk)
            if written > settings.MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=(
                        f"{role!r} exceeds the upload limit of "
                        f"{settings.MAX_UPLOAD_BYTES:,} bytes"
                    ),
                )
            sink.write(chunk)
    if written == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{role!r} is empty",
        )
    return Upload(role=role, filename=filename, path=path)
