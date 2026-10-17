"""The reconciliation endpoint.

Uploads are streamed to temporary files rather than read into memory, and the
pipeline runs in a worker thread so a large workbook cannot stall the event loop.
"""

import logging
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.concurrency import run_in_threadpool

from app.api.deps import get_current_user
from app.api.routes._uploads import XLSX_MEDIA_TYPE, cleanup, spool, xlsx_response
from app.core.config import settings
from app.recon.engine import ReconRequest, reconcile
from app.recon.excel import SheetFormatError
from app.recon.export import build_report
from app.recon.inputs import ReconInputError
from app.recon.models import RateConfig, ReconResult
from app.recon.registry import UnknownCategoryError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reconcile", tags=["reconcile"])


@router.post(
    "",
    dependencies=[Depends(get_current_user)],
    response_model=ReconResult,
    responses={
        200: {
            "content": {XLSX_MEDIA_TYPE: {}},
            "description": "The reconciliation, as JSON or as an .xlsx report.",
        }
    },
)
async def run_reconciliation(
    portal: Annotated[UploadFile, File(description="Portal export (order lines).")],
    vendor: Annotated[UploadFile, File(description="Vendor account statement.")],
    promo: Annotated[UploadFile, File(description="Discount base sheet (promo SKUs).")],
    settlements: Annotated[
        UploadFile | None,
        File(description="Optional prior settlement history, for clawbacks."),
    ] = None,
    category: Annotated[str, Form()] = "eshop",
    period_start: Annotated[date | None, Form()] = None,
    period_end: Annotated[date | None, Form()] = None,
    standard_rate: Annotated[Decimal | None, Form()] = None,
    promo_rate: Annotated[Decimal | None, Form()] = None,
    promo_cutoff: Annotated[date | None, Form()] = None,
    response_format: Annotated[Literal["json", "xlsx"], Query(alias="format")] = "json",
) -> Any:
    """
    Reconcile one vendor cycle and return the result.

    Each file is searched for the sheet its role needs, so a workbook holding
    several tabs can be uploaded to more than one field. The vendor statement is
    different: every ledger-shaped sheet in it is read and the union
    deduplicated, because the PDF converter emits overlapping copies.

    Rates default to the server configuration and can be overridden per run,
    which makes a back-dated recalculation a single request.

    Nothing is persisted. The response is the whole result; there is no job to
    fetch afterwards.
    """
    rates = RateConfig(
        standard_rate=standard_rate
        if standard_rate is not None
        else settings.STANDARD_RATE,
        promo_rate=promo_rate if promo_rate is not None else settings.PROMO_RATE,
        promo_cutoff=promo_cutoff
        if promo_cutoff is not None
        else settings.PROMO_CUTOFF,
        line_tolerance=settings.LINE_TOLERANCE,
        order_tolerance=settings.ORDER_TOLERANCE,
    )

    workdir = Path(tempfile.mkdtemp(prefix="recon-"))
    report: Path | None = None
    # Set only once a response owns the temp files and will clean them up itself.
    handed_off = False
    try:
        request = ReconRequest(
            category=category,
            portal=await spool(portal, "portal", workdir),
            vendor=await spool(vendor, "vendor", workdir),
            promo=await spool(promo, "promo", workdir),
            settlements=(
                await spool(settlements, "settlements", workdir)
                if settlements is not None and settlements.filename
                else None
            ),
            rates=rates,
            period_start=period_start,
            period_end=period_end,
            max_rows=settings.MAX_ROWS_PER_SHEET,
        )
        # Parsing and reconciling are CPU-bound; off the event loop they go.
        result = await run_in_threadpool(reconcile, request)

        if response_format == "json":
            return result

        report = Path(tempfile.mkstemp(prefix="recon-report-", suffix=".xlsx")[1])
        await run_in_threadpool(build_report, result, report)
        filename = (
            f"reconciliation-{result.category}-"
            f"{result.period_start}-to-{result.period_end}.xlsx"
        )
        # The report outlives this function, so ownership passes to the response
        # and the files are removed once it has actually been sent.
        response = xlsx_response(report, filename, workdir)
        handed_off = True
        return response
    except UnknownCategoryError as exc:
        # KeyError's str() is the repr of its argument, quotes and all.
        raise HTTPException(status_code=422, detail=exc.args[0])
    except (ReconInputError, SheetFormatError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    finally:
        # Covers every path that did not hand ownership to a FileResponse: the
        # JSON reply, a rejected upload, and a report that failed while building.
        if not handed_off:
            cleanup(workdir, report)
