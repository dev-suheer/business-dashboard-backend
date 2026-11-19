"""The mini-map: how a report column is worked out, step by step.

From the setup alone: every column of every table, every figure and every
breakdown column of the approved setups is traced, down to the uploaded
columns, the status rules and the rates.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config_model.models import CategoryConfig
from app.config_model.seed import load_seed
from app.core.config import settings
from app.services import configs
from app.services.trace import Step, Target, TraceTargetError, trace

API = settings.API_V1_STR
ARROW = chr(0x203A)


def _steps(step: Step) -> Iterator[Step]:
    yield step
    for child in step.children:
        yield from _steps(child)


def _named(step: Step, name: str) -> Step:
    return next(s for s in _steps(step) if s.name == name)


@pytest.mark.parametrize("category", ["travel", "eshop", "food"])
def test_everything_in_the_report_can_be_traced(category: str) -> None:
    config = load_seed(category)
    traced = 0
    for tab in config.output.tabs:
        targets: list[Target] = [Target(tab.key, c.key) for c in tab.columns]
        for b, block in enumerate(tab.blocks):
            targets += [
                Target(tab.key, block=b, value=v) for v in range(len(block.values))
            ]
        if tab.breakdown is not None:
            targets.append(Target(tab.key, breakdown="name"))
            targets += [
                Target(tab.key, breakdown=i) for i in range(len(tab.breakdown.columns))
            ]
        for target in targets:
            found = trace(config, target)
            assert found.title.startswith(f"{tab.title} {ARROW} ")
            assert found.root.name
            traced += 1
    assert traced > 50


def test_a_difference_goes_down_to_the_uploads_and_the_rate() -> None:
    root = trace(load_seed("travel"), Target("orders", "verdict")).root
    assert root.kind == "column" and root.id == "out:orders:verdict"
    base = _named(root, "base_diff")
    assert base.kind == "order field" and base.id == "order:base_diff"
    total = _named(root, "portal.order_total")
    assert total.kind == "upload column"
    assert total.where == "Portal export · Order Total"
    assert total.id == "in:portal:portal:order_total"
    lines = next(
        s
        for s in _steps(root)
        if s.name == "lines" and any(c.name == "total_invoice" for c in s.children)
    )
    assert lines.kind == "rows"
    invoice = next(c for c in lines.children if c.name == "total_invoice")
    assert invoice.where == "Vendor claim · Total Invoice Amount"
    tolerance = _named(root, "param.base_tolerance")
    assert tolerance.kind == "rate" and tolerance.where == "Amount tolerance"


def test_the_verdict_shows_every_status_rule() -> None:
    config = load_seed("travel")
    assert config.model is not None
    root = trace(config, Target("orders", "verdict")).root
    verdict = _named(root, "verdict")
    assert verdict.kind == "status rules"
    assert [c.name for c in verdict.children] == [
        f"rule {n + 1}" for n in range(len(config.model.rules))
    ]
    assert verdict.children[0].formula == config.model.rules[0].when
    # A step shown once is marked the second time, not repeated.
    others = [s for s in _steps(root) if s.name == "verdict" and s is not verdict]
    assert all(s.repeat for s in others)


def test_a_list_shows_its_condition() -> None:
    root = trace(load_seed("travel"), Target("dashboard", block=0, value=0)).root
    matched = _named(root, "matched")
    assert matched.kind == "rows" and matched.formula == 'verdict = "matched"'


def test_an_exceptions_status_reads_its_order() -> None:
    root = trace(load_seed("travel"), Target("exceptions", "status")).root
    status = root.children[0]
    assert status.name == "order.portal_status" and status.kind == "order field"
    assert any(c.kind == "upload field" for c in status.children)


def test_an_erp_unit_price_shows_the_linked_column() -> None:
    root = trace(load_seed("travel"), Target("b2c_invoices", "unit_price")).root
    assert root.kind == "Excel formula"
    linked = root.children[0]
    assert linked.id == "out:orders:commission_charged"
    assert linked.children[0].name == "portal.commission_charged"


def test_an_upload_rows_table_and_a_copys_column_read_the_headers() -> None:
    config = load_seed("eshop")
    partner = trace(config, Target("b2c_invoices", "partner")).root
    leaf = partner.children[0]
    assert leaf.name == "merchantname" and leaf.kind == "upload column"
    assert leaf.where == "Portal export"

    data = config.model_dump(mode="json")
    copy = next(t for t in data["output"]["tabs"] if t["key"] == "portal_copy")
    copy["columns"] = [
        {
            "key": "double",
            "header": "Double",
            "width": 12,
            "group": "meta",
            "kind": "money",
            "value": "shippingcost * param.standard_rate",
            "excel": None,
            "color": None,
            "style": None,
        }
    ]
    edited = CategoryConfig.model_validate(data)
    added = trace(edited, Target("portal_copy", "double")).root
    assert [(c.name, c.kind) for c in added.children] == [
        ("shippingcost", "upload column"),
        ("param.standard_rate", "rate"),
    ]


def test_a_breakdown_column_shows_its_names_and_value() -> None:
    root = trace(load_seed("travel"), Target("dashboard", breakdown=7)).root
    assert root.kind == "breakdown" and root.id == "bd:dashboard:7"
    names = [c.name for c in root.children]
    assert names[0] == "Breakdown By Most Used" and "value" in names
    total = _named(root, "line_commission")
    assert total.kind == "name's total"


def test_a_dashboard_before_any_orders_is_traced() -> None:
    # A blank category with a dashboard added, as the Add tab dialog makes it.
    data = configs.blank("qa").model_dump(mode="json")
    data["output"]["tabs"].append(
        {
            "key": "dash",
            "title": "Dash",
            "kind": "dashboard",
            "blocks": [
                {
                    "label": "Figures",
                    "value_label": None,
                    "headers": ["New figure"],
                    "values": [{"metric": "", "kind": "money"}],
                    "tall": True,
                }
            ],
        }
    )
    config = CategoryConfig.model_validate(data)
    assert config.model is None
    found = trace(config, Target("dash", block=0, value=0))
    assert found.title == f"Dash {ARROW} New figure"
    assert found.root.kind == "figure"
    assert found.root.where is None and not found.root.children


def _excel(data: dict, tab: str, column: str, excel: str) -> None:
    table = next(t for t in data["output"]["tabs"] if t["key"] == tab)
    cell = next(c for c in table["columns"] if c["key"] == column)
    cell["excel"] = excel


def test_an_excel_link_ring_stops() -> None:
    # A column linked to itself: shown once, then marked.
    data = load_seed("travel").model_dump(mode="json")
    _excel(data, "orders", "commission_charged", "={cell:orders.commission_charged}")
    root = trace(
        CategoryConfig.model_validate(data), Target("orders", "commission_charged")
    ).root
    assert root.kind == "Excel formula" and not root.repeat
    inner = root.children[0]
    assert inner.id == "out:orders:commission_charged" and inner.repeat
    assert not inner.children

    # Two columns linked to each other, traced from either end.
    data = load_seed("travel").model_dump(mode="json")
    _excel(data, "orders", "commission_charged", "={cell:b2c_invoices.unit_price}")
    ring = CategoryConfig.model_validate(data)
    for target in (
        Target("orders", "commission_charged"),
        Target("b2c_invoices", "unit_price"),
    ):
        steps = list(_steps(trace(ring, target).root))
        assert len(steps) == 3 and steps[-1].repeat


def test_an_unknown_target_is_refused() -> None:
    config = load_seed("travel")
    with pytest.raises(TraceTargetError):
        trace(config, Target("nosuch", "x"))
    with pytest.raises(TraceTargetError):
        trace(config, Target("orders", "nosuch"))
    with pytest.raises(TraceTargetError):
        trace(config, Target("dashboard", breakdown=99))
    with pytest.raises(TraceTargetError):
        trace(config, Target("dashboard", block=-1, value=0))
    with pytest.raises(TraceTargetError):
        trace(config, Target("dashboard", block=0, value=-1))


def test_the_api_traces_without_any_file(runs_client: TestClient) -> None:
    body = {
        "config": load_seed("travel").model_dump(mode="json"),
        "target": {"tab": "orders", "column": "base_diff"},
    }
    response = runs_client.post(f"{API}/categories/travel/trace", json=body)
    assert response.status_code == 200, response.text
    found = response.json()
    assert found["title"] == f"Orders {ARROW} BASE DIFFERENCE"
    assert found["root"]["name"] == "BASE DIFFERENCE"
    assert found["root"]["children"]
    assert set(found["root"]) == {
        "name",
        "kind",
        "where",
        "formula",
        "id",
        "repeat",
        "children",
    }
    for target in (
        {"tab": "orders", "column": "nosuch"},
        {"tab": "dashboard", "block": -1, "value": 0},
    ):
        refused = runs_client.post(
            f"{API}/categories/travel/trace", json={**body, "target": target}
        )
        assert refused.status_code == 422, target
