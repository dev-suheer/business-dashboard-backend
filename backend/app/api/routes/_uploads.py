"""Upload plumbing shared by the reconciliation endpoints.

Uploads are streamed to temporary files rather than read into memory: openpyxl
needs a seekable file, and the whole point of streaming is that a workbook is
never fully resident. Every category's endpoint needs the same spooling, the
same size guard and the same hand-off of temp files to a FileResponse, so it
lives here once.
"""

import shutil
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

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


def cleanup(workdir: Path, report: Path | None) -> None:
    shutil.rmtree(workdir, ignore_errors=True)
    if report is not None:
        report.unlink(missing_ok=True)


def xlsx_response(report: Path, filename: str, workdir: Path) -> FileResponse:
    """Hand the report and its temp files to the response that will send them.

    The files outlive the request handler, so ownership passes to the response
    and they are removed once it has actually gone out.
    """
    return FileResponse(
        report,
        media_type=XLSX_MEDIA_TYPE,
        filename=filename,
        background=BackgroundTask(cleanup, workdir, report),
    )
