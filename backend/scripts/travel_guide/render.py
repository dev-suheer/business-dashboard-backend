"""The guide as one HTML document, and printed to an A4 PDF."""

from datetime import date
from pathlib import Path

from app.config_model.models import CategoryConfig
from app.config_model.seed import seed_path
from app.core.config import BACKEND_DIR
from scripts.travel_guide.markup import c, esc
from scripts.travel_guide.reference import Reference
from scripts.travel_guide.savepoints import SavePoint
from scripts.travel_guide.walkthrough import SECTIONS, Walkthrough

TITLE = "Rebuilding Travel from scratch"
STYLE = Path(__file__).with_name("style.css")
FOOTER = (
    '<div style="width:100%;margin:0 15mm;display:flex;justify-content:space-between;'
    "font-family:'Noto Sans',sans-serif;font-size:7.5pt;color:#5d6b78\">"
    f"<span>{TITLE}</span>"
    '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>'
)


def document(
    seed: CategoryConfig,
    points: list[SavePoint],
    shots: dict[str, Path],
    made: date,
) -> str:
    tables = Reference(seed).all()
    part_a = Walkthrough(seed, points, shots).html()
    part_b = '<h1 class="part">Part B · Reference</h1>' + "".join(
        h for _, _, h in tables
    )
    steps = "".join(f"<li>{n}. {esc(t)}</li>" for n, t in enumerate(SECTIONS, 1))
    refs = "".join(f"<li>R{n} · {esc(t)}</li>" for n, t, _ in tables)
    source = seed_path(seed.category).relative_to(BACKEND_DIR)
    cover = (
        '<section class="cover"><div class="kicker">Recon Portal · configuration drill</div>'
        f"<h1>{TITLE}</h1>"
        '<p class="lead">Part A walks you through building Travel in the editor and what '
        f"each part does. Part B holds every value to type, in tables R1 to R{len(tables)}.</p>"
        f'<div class="contents"><div><h3>Part A · Walkthrough</h3><ol>{steps}</ol></div>'
        f"<div><h3>Part B · Reference</h3><ol>{refs}</ol></div></div>"
        f'<p class="made">Made on {made.isoformat()} from {c(source)}, Travel\'s approved '
        "version 1. Every value of Part B is read from that file, and each of the "
        f"{len(points)} save points was built and passed the setup check; the last is the "
        "approved setup itself. The screenshots are of today's editor.</p></section>"
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{TITLE}</title><style>{STYLE.read_text()}</style></head>"
        f"<body>{cover}{part_a}{part_b}</body></html>"
    )


def pdf(html: str, out: Path) -> None:
    """Print ``html`` to ``out`` with Chrome, a footer on every page."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception:
            browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="load")
        page.pdf(
            path=str(out),
            format="A4",
            print_background=True,
            display_header_footer=True,
            header_template="<div></div>",
            footer_template=FOOTER,
            margin={"top": "15mm", "bottom": "17mm", "left": "15mm", "right": "15mm"},
        )
        browser.close()
