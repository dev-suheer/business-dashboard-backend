"""Sheet detection for travel — the collision suite.

The travel portal header is not unique: the Food export carries the same 38
columns, and both the client's worked reconciliation and ours bolt the claim's
columns onto it. Nothing can be told apart by fingerprint alone, so these tests
pin the disqualifiers that do the work, and pin that eshop's own detection is
unaffected.
"""

from app.recon.categories.eshop import parser as eshop
from app.recon.categories.travel import parser as travel
from app.recon.excel import detect_sheet, detect_sheets, open_workbook
from tests.conftest import TRAVEL_CLAIMS, TRAVEL_PORTAL, TRAVEL_WALLET, WORKINGS


def test_the_portal_schema_finds_every_travel_tab() -> None:
    with open_workbook(TRAVEL_PORTAL) as workbook:
        assert detect_sheets(workbook, travel.PORTAL_SCHEMA) == ["Hotel", "Flight"]


def test_the_food_export_is_not_mistaken_for_travel() -> None:
    """``Food Analysis`` carries the travel portal header verbatim."""
    with open_workbook(WORKINGS) as workbook:
        assert detect_sheets(workbook, travel.PORTAL_SCHEMA) == []
        assert detect_sheets(workbook, travel.VENDOR_SCHEMA) == []


def test_the_clients_worked_analysis_is_not_mistaken_for_an_input() -> None:
    """Claim 13 ships the client's own Analysis tab, which satisfies both
    fingerprints: it is the portal columns and the claim columns side by side."""
    with open_workbook(TRAVEL_CLAIMS["13"]) as workbook:
        assert "Analysis" in workbook.sheetnames
        assert detect_sheets(workbook, travel.PORTAL_SCHEMA) == ["Hotel", "Flight"]
        assert detect_sheets(workbook, travel.VENDOR_SCHEMA) == ["Vedor Report"]


def test_the_vendor_schema_survives_the_claims_column_drift() -> None:
    """One claim adds five hotel columns, one drops Supply Date, one drops
    Entitlement, and all four name their data sheet differently."""
    found = {}
    for name, path in TRAVEL_CLAIMS.items():
        with open_workbook(path) as workbook:
            found[name] = detect_sheets(workbook, travel.VENDOR_SCHEMA)
    assert found == {
        "10": ["Sheet1"],
        "11": ["Aug 01 to 08 Mosafer Report"],
        "12": ["Sheet1"],
        "13": ["Vedor Report"],
    }


def test_the_claim_ledger_is_optional() -> None:
    """Three of the four claims carry the vendor's own per-period block."""
    found = {}
    for name, path in TRAVEL_CLAIMS.items():
        with open_workbook(path) as workbook:
            found[name] = detect_sheet(workbook, travel.CLAIM_SCHEMA)
    assert found == {"10": "Sheet2", "11": None, "12": "summary ", "13": "Sheet2"}


def test_the_wallet_schema_matches_only_the_wallet_history() -> None:
    with open_workbook(TRAVEL_WALLET) as workbook:
        assert detect_sheet(workbook, travel.WALLET_SCHEMA) == "Sheet1"
    with open_workbook(TRAVEL_PORTAL) as workbook:
        assert detect_sheet(workbook, travel.WALLET_SCHEMA) is None


def test_the_two_categories_do_not_claim_each_others_sheets() -> None:
    with open_workbook(TRAVEL_PORTAL) as workbook:
        assert detect_sheet(workbook, eshop.PORTAL_SCHEMA) is None
        assert detect_sheet(workbook, eshop.LEDGER_SCHEMA) is None
        assert detect_sheet(workbook, eshop.SETTLEMENT_SCHEMA) is None
    with open_workbook(WORKINGS) as workbook:
        # The regression guard: eshop still finds its own portal export.
        assert detect_sheet(workbook, eshop.PORTAL_SCHEMA) == "E-SHop 1 Vendor "
