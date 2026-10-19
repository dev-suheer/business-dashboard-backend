"""The food reconciliation endpoint.

Its own route: food takes a portal export and a Chefz order report, and no
rate or period fields. The upload plumbing is shared.
"""

import logging
import tempfile
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.api.deps import get_current_user
from app.api.routes._uploads import XLSX_MEDIA_TYPE, cleanup, spool, xlsx_response
from app.core.config import settings
from app.recon.categories.food.export import build_report
from app.recon.categories.food.models import FoodResult
from app.recon.categories.food.pipeline import FoodRequest, run
from app.recon.excel import SheetFormatError
from app.recon.inputs import ReconInputError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reconcile/food", tags=["reconcile"])


@router.post(
    "",
    dependencies=[Depends(get_current_user)],
    response_model=FoodResult,
    responses={
        200: {
            "content": {XLSX_MEDIA_TYPE: {}},
            "description": "The reconciliation, as JSON or as an .xlsx report.",
        }
    },
)
async def run_food_reconciliation(
    portal: Annotated[
        UploadFile,
        File(description="Portal export. Only Vertical = Food rows are read."),
    ],
    vendor: Annotated[UploadFile, File(description="The Chefz order report.")],
    response_format: Annotated[Literal["json", "xlsx"], Query(alias="format")] = "json",
) -> Any:
    """
    Reconcile one food cycle and return the result.

    Rows are paired on Partner Order ID = order_id. Dates are not compared.
    Only Delivered rows are settled. A break stays in the response and out of
    the settled payable. The Excel report opens on Dashboard, then Orders,
    Exceptions, Cashback, and the uploaded Portal and Vendor sheets.

    Nothing is persisted. The response is the whole result.
    """
    workdir = Path(tempfile.mkdtemp(prefix="recon-food-"))
    report: Path | None = None
    handed_off = False
    try:
        request = FoodRequest(
            portal=await spool(portal, "portal", workdir),
            vendor=await spool(vendor, "vendor", workdir),
            max_rows=settings.MAX_ROWS_PER_SHEET,
        )
        result = await run_in_threadpool(run, request)
        if response_format == "json":
            return result

        report = Path(tempfile.mkstemp(prefix="recon-food-", suffix=".xlsx")[1])
        await run_in_threadpool(
            build_report,
            result,
            report,
            {"portal": request.portal.path, "vendor": request.vendor.path},
        )
        start = result.summary.portal_date_start
        end = result.summary.portal_date_end
        span = f"-{start}-to-{end}" if start and end else ""
        response = xlsx_response(report, f"reconciliation-food{span}.xlsx", workdir)
        handed_off = True
        return response
    except (ReconInputError, SheetFormatError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        if not handed_off:
            cleanup(workdir, report)
