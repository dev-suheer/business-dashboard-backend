"""Screenshots of the editor showing Travel, for the walkthrough.

The editor is served for the screenshots alone, against the test database
(``TEST_DATABASE_URL``) and never the real one: an API on its own port, with a
throwaway login and file store, and Vite on its own port, proxying to it. The
grids show their headers. Nothing the editor shows is saved.
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.config_model.models import CategoryConfig
from app.config_model.seed import load_seed
from app.core.config import BACKEND_DIR, settings
from app.core.security import get_password_hash
from app.db.session import get_engine
from scripts.travel_guide.savepoints import GuideBroken

FRONTEND_DIR = BACKEND_DIR.parents[1] / "business-dashboard"
USER = "guide"
PASSWORD = "guide only, test database"
VIEW_WIDTH, VIEW_HEIGHT = 1480, 900

# Vite, with the app's own config, proxying /api to the API started here.
_VITE = """
import { createServer } from 'vite'
const server = await createServer({
  configFile: 'vite.config.js',
  clearScreen: false,
  server: {
    host: '127.0.0.1',
    port: Number(process.env.GUIDE_PORT),
    strictPort: true,
    proxy: { '/api': process.env.GUIDE_API },
  },
})
await server.listen()
console.log('ready')
"""


@dataclass(frozen=True)
class Shot:
    key: str
    caption: str


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait(url: str, process: subprocess.Popen[bytes], what: str) -> None:
    for _ in range(240):
        if process.poll() is not None:
            raise GuideBroken(f"{what} stopped before it was ready")
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except urllib.error.HTTPError:
            return
        except OSError:
            time.sleep(0.25)
    raise GuideBroken(f"{what} did not start")


def _test_database() -> str:
    url = settings.TEST_DATABASE_URL
    if not url or url == settings.DATABASE_URL:
        raise GuideBroken(
            "The screenshots need TEST_DATABASE_URL, apart from the real database"
        )
    with get_engine(url).connect() as connection:
        config = connection.execute(
            text(
                "select v.config from category c join category_version v "
                "on v.id = c.current_version_id where c.key = 'travel'"
            )
        ).scalar_one_or_none()
    if config is None or CategoryConfig.model_validate(config) != load_seed("travel"):
        raise GuideBroken(
            "Travel on the test database is not the approved setup: run the tests "
            "(they leave it so) and try again"
        )
    return url


@contextmanager
def _login(database: str) -> Iterator[None]:
    """A throwaway login on the test database, removed after. One already
    called so is used as it is, with this password, and left there."""
    engine = get_engine(database)
    with engine.begin() as connection:
        added = connection.execute(
            text(
                "insert into account (username, password_hash) values (:u, :h) "
                "on conflict (username) do update set password_hash = :h "
                "returning (xmax = 0)"
            ),
            {"u": USER, "h": get_password_hash(PASSWORD)},
        ).scalar_one()
    try:
        yield
    finally:
        if added:
            with engine.begin() as connection:
                connection.execute(
                    text("delete from account where username = :u"), {"u": USER}
                )


@contextmanager
def _servers(work: Path) -> Iterator[tuple[str, str]]:
    """The API and the editor, on free ports; their base URLs."""
    database = _test_database()
    with _login(database), _serving(work, database) as urls:
        yield urls


@contextmanager
def _serving(work: Path, database: str) -> Iterator[tuple[str, str]]:
    api_port, web_port = _free_port(), _free_port()
    api = f"http://127.0.0.1:{api_port}"
    web = f"http://127.0.0.1:{web_port}"
    env = {
        **os.environ,
        "DATABASE_URL": database,
        "RUN_STORAGE_DIR": str(work / "data"),
        "COOKIE_SECURE": "false",
    }
    logs = (work / "api.log").open("wb")
    processes: list[subprocess.Popen[bytes]] = []
    try:
        api_process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(api_port)],
            cwd=BACKEND_DIR,
            env=env,
            stdout=logs,
            stderr=subprocess.STDOUT,
        )
        processes.append(api_process)
        _wait(f"{api}/api/v1/standard", api_process, "The API")
        web_process = subprocess.Popen(
            ["node", "--input-type=module", "-e", _VITE],
            cwd=FRONTEND_DIR,
            env={
                **os.environ,
                "GUIDE_PORT": str(web_port),
                "GUIDE_API": api,
                "VITE_API_URL": "",
            },
            stdout=logs,
            stderr=subprocess.STDOUT,
        )
        processes.append(web_process)
        _wait(web, web_process, "The editor")
        yield api, web
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        logs.close()


def capture(out: Path) -> dict[str, Path]:
    """Every screenshot, by its key, written to ``out``."""
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="travel-guide-"))
    taken: dict[str, Path] = {}
    try:
        with _servers(work) as (_api, web), sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="chrome")
            except Exception:
                browser = p.chromium.launch()
            context = browser.new_context(
                viewport={"width": VIEW_WIDTH, "height": VIEW_HEIGHT},
                device_scale_factor=1.6,
            )
            login = context.request.post(
                f"{web}/api/v1/login/access-token",
                form={"username": USER, "password": PASSWORD},
            )
            if not login.ok:
                raise GuideBroken(f"Could not sign in to the editor: {login.text()}")
            page = context.new_page()
            page.goto(f"{web}/categories/travel")
            page.get_by_role("button", name="Rules and exceptions").wait_for()
            page.wait_for_timeout(2000)
            for key, take in _SHOTS.items():
                path = out / f"{key}.png"
                take(page, path)
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
                taken[key] = path
            browser.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return taken


# --- The screenshots -------------------------------------------------------------


# Scroll the canvas so a node's top left corner is at (left, top) of it.
_PLACE = """(n, [left, top]) => {
  let w = n.parentElement
  while (w && !(w.scrollHeight > w.clientHeight && /auto|scroll/.test(getComputedStyle(w).overflowY)))
    w = w.parentElement
  const r = n.getBoundingClientRect(), v = w.getBoundingClientRect()
  w.scrollTop += r.top - v.top - top
  w.scrollLeft += r.left - v.left - left
}"""


def _select(page: Any, element: str, left: int = 520, top: int = 150) -> None:
    """Select an element of the canvas, then show it ``left`` and ``top``
    pixels from the canvas's corner."""
    node = page.locator(f'[data-el="{element}"]').first
    node.scroll_into_view_if_needed()
    node.click()
    _place(page, element, left, top)


def _place(page: Any, element: str, left: int, top: int) -> None:
    node = page.locator(f'[data-el="{element}"]').first
    node.evaluate(_PLACE, [left, top])
    page.wait_for_timeout(600)


def _dialog(page: Any, name: str) -> Any:
    found = page.get_by_role("dialog", name=name)
    found.wait_for()
    page.wait_for_timeout(300)
    return found


def _tab(page: Any, title: str) -> None:
    page.get_by_role("button", name=title, exact=True).last.click()
    page.wait_for_timeout(400)


def _rules(page: Any, step: str) -> Any:
    page.get_by_role("button", name="Rules and exceptions").click()
    dialog = _dialog(page, "Rules and exceptions")
    dialog.get_by_role("button", name=step).first.click()
    page.wait_for_timeout(400)
    return dialog


def upload_column(page: Any, path: Path) -> None:
    _select(page, "in:portal:portal:stated_rate")
    page.screenshot(path=path)


def add_upload(page: Any, path: Path) -> None:
    page.get_by_role("button", name="+ Upload", exact=True).click()
    _dialog(page, "Add an upload").screenshot(path=path)


def vendor_sheet(page: Any, path: Path) -> None:
    card = (
        page.locator("section, div")
        .filter(has_text="Vendor claim")
        .filter(has=page.get_by_role("button", name="Settings", exact=True))
        .last
    )
    card.get_by_role("button", name="Settings", exact=True).first.click()
    dialog = _dialog(page, "Vendor claim · settings")
    dialog.locator("input[value='claim_periods']").first.evaluate(
        "n => n.scrollIntoView({block: 'start'})"
    )
    page.wait_for_timeout(300)
    dialog.screenshot(path=path)


def lists(page: Any, path: Path) -> None:
    _rules(page, "Lists of orders and rows").screenshot(path=path)


def members(page: Any, path: Path) -> None:
    page.get_by_role("button", name="How orders are made", exact=True).first.click()
    _dialog(page, "How orders are made").screenshot(path=path)


def status(page: Any, path: Path) -> None:
    _rules(page, "Order status").screenshot(path=path)


def exception_list(page: Any, path: Path) -> None:
    _rules(page, "Exception list").screenshot(path=path)


def add_tab(page: Any, path: Path) -> None:
    page.get_by_role("button", name="+ Tab", exact=True).click()
    dialog = _dialog(page, "Add a tab to the report")
    dialog.locator("select").first.select_option(label="Copy of an upload")
    page.wait_for_timeout(300)
    dialog.screenshot(path=path)


def orders_column(page: Any, path: Path) -> None:
    _tab(page, "Orders")
    _select(page, "out:orders:base_diff")
    page.screenshot(path=path)


def _layout(page: Any, tab: str, key: str) -> Any:
    _tab(page, tab)
    page.locator(f'[data-el="tab:{key}"]').click()
    page.get_by_role("button", name="Sheet layout", exact=False).first.click()
    return _dialog(page, f"{tab} · sheet layout")


def sheet_layout(page: Any, path: Path) -> None:
    _layout(page, "Orders", "orders").screenshot(path=path)


def erp_layout(page: Any, path: Path) -> None:
    _layout(page, "B2C Invoices ERP", "b2c_invoices").screenshot(path=path)


def dashboard(page: Any, path: Path) -> None:
    _tab(page, "Dashboard")
    _select(page, "bdname:dashboard")
    _place(page, "block:dashboard:0", 40, 40)
    page.screenshot(path=path)


def lookup_columns(page: Any, path: Path) -> None:
    _tab(page, "Orders")
    page.locator('[data-el="tab:orders"]').click()
    page.wait_for_timeout(400)
    page.get_by_text("Look-up columns", exact=True).first.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    page.screenshot(path=path)


def copy_settings(page: Any, path: Path) -> None:
    _tab(page, "Portal")
    _select(page, "tab:portal_copy", left=900, top=120)
    page.screenshot(path=path)


_SHOTS: dict[str, Callable[[Any, Path], None]] = {
    "upload_column": upload_column,
    "add_upload": add_upload,
    "vendor_sheet": vendor_sheet,
    "lists": lists,
    "members": members,
    "status": status,
    "exception_list": exception_list,
    "add_tab": add_tab,
    "orders_column": orders_column,
    "sheet_layout": sheet_layout,
    "erp_layout": erp_layout,
    "dashboard": dashboard,
    "copy_settings": copy_settings,
    "lookup_columns": lookup_columns,
}


# Every screenshot the walkthrough shows, by its key.
KEYS = tuple(_SHOTS)
