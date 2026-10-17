"""The travel reconciliation endpoint.

Its own route rather than a branch inside the eshop one: travel takes different
files and different parameters, and a multipart signature cannot be chosen at
runtime. The upload plumbing is shared; only the shape of a travel run is here.
"""

import logging
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.api.deps import get_current_user
from app.api.routes._uploads import XLSX_MEDIA_TYPE, cleanup, spool, xlsx_response
from app.core.config import settings
from app.recon.categories.travel.export import build_report
from app.recon.categories.travel.models import TravelRateConfig, TravelResult
from app.recon.categories.travel.pipeline import TravelRequest, run
from app.recon.excel import SheetFormatError
from app.recon.inputs import ReconInputError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reconcile/travel", tags=["reconcile"])


@router.post(
    "",
    dependencies=[Depends(get_current_user)],
    response_model=TravelResult,
    responses={
        200: {
            "content": {XLSX_MEDIA_TYPE: {}},
            "description": "The reconciliation, as JSON or as an .xlsx report.",
        }
    },
)
async def run_travel_reconciliation(
    portal: Annotated[
        UploadFile,
        File(description="Portal export. Hotel and flight tabs are both read."),
    ],
    vendor: Annotated[UploadFile, File(description="The vendor's claim workbook.")],
    wallet: Annotated[
        UploadFile | None,
        File(
            description="Optional wallet history, to prove the wallet share of each booking."
        ),
    ] = None,
    period_start: Annotated[date | None, Form()] = None,
    period_end: Annotated[date | None, Form()] = None,
    hotel_rate: Annotated[Decimal | None, Form()] = None,
    flight_rate: Annotated[Decimal | None, Form()] = None,
    vat_rate: Annotated[Decimal | None, Form()] = None,
    response_format: Annotated[Literal["json", "xlsx"], Query(alias="format")] = "json",
) -> Any:
    """
    Reconcile one travel claim and return the result.

    Every travel-shaped sheet in each workbook is read, so hotel and flight
    exports can be sent as two tabs of one file, and a claim workbook holding
    several weeks reconciles in one request. Rows belonging to another category
    are ignored rather than rejected.

    The wallet history is optional. Supplied, it proves that the wallet share of
    each booking actually left a wallet, and for the same amount.

    Rates default to the server configuration and can be overridden per run,
    which makes a back-dated recalculation a single request.

    Nothing is persisted. The response is the whole result; there is no job to
    fetch afterwards.
    """
    rates = TravelRateConfig(
        hotel_rate=hotel_rate if hotel_rate is not None else settings.TRAVEL_HOTEL_RATE,
        flight_rate=flight_rate
        if flight_rate is not None
        else settings.TRAVEL_FLIGHT_RATE,
        vat_rate=vat_rate if vat_rate is not None else settings.TRAVEL_VAT_RATE,
        base_tolerance=settings.TRAVEL_BASE_TOLERANCE,
        commission_tolerance=settings.TRAVEL_COMMISSION_TOLERANCE,
        rate_tolerance=settings.TRAVEL_RATE_TOLERANCE,
    )

    workdir = Path(tempfile.mkdtemp(prefix="recon-travel-"))
    report: Path | None = None
    # Set only once a response owns the temp files and will clean them up itself.
    handed_off = False
    try:
        request = TravelRequest(
            portal=await spool(portal, "portal", workdir),
            vendor=await spool(vendor, "vendor", workdir),
            wallet=(
                await spool(wallet, "wallet", workdir)
                if wallet is not None and wallet.filename
                else None
            ),
            rates=rates,
            period_start=period_start,
            period_end=period_end,
            max_rows=settings.MAX_ROWS_PER_SHEET,
        )
        # Parsing and reconciling are CPU-bound; off the event loop they go.
        result = await run_in_threadpool(run, request)

        if response_format == "json":
            return result

        report = Path(tempfile.mkstemp(prefix="recon-travel-", suffix=".xlsx")[1])
        await run_in_threadpool(build_report, result, report)
        filename = (
            f"reconciliation-travel-{result.period_start}-to-{result.period_end}.xlsx"
        )
        response = xlsx_response(report, filename, workdir)
        handed_off = True
        return response
    except (ReconInputError, SheetFormatError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    finally:
        # Covers every path that did not hand ownership to a FileResponse: the
        # JSON reply, a rejected upload, and a report that failed while building.
        if not handed_off:
            cleanup(workdir, report)
