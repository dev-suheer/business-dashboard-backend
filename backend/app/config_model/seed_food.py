"""Food on the generic runner: how version 1 reads the uploads and reconciles.

Food used to be a pipeline in code. Each rule below is that code, in the same
order, written as a formula; ``tests/test_parity.py`` proves the reports and
the figures identical to the ones the client approved.

* Only ``Vertical = Food`` portal rows are read.
* Portal ``Partner Order ID`` pairs with vendor ``order_id``. Only Delivered
  rows pair; dates are carried, not compared.
* An order settles when both sides are Delivered, its id is unique, the amount
  and the fee agree within the tolerance, the delivery split meets the
  vendor's charge, and the portal shows no refund. Anything else is held, and
  stays on the sheet but out of the settled payable.
"""

from app.config_model.models import (
    Check,
    History,
    InputColumn,
    InputFile,
    InputSheet,
    Model,
    Named,
    Parameter,
    Raise,
    RowRule,
    SetDef,
    Side,
    StatusRule,
    WalletCheck,
)


def _col(
    field: str,
    *headers: str,
    type: str = "text",
    required: bool = False,
    skip: bool = False,
) -> InputColumn:
    return InputColumn.model_validate(
        {
            "field": field,
            "headers": list(headers),
            "type": type,
            "required": required,
            "skip_if_blank": skip,
        }
    )


PORTAL = InputFile(
    role="portal",
    title="Portal export",
    caption="Orders from the portal",
    required=True,
    empty_message="The portal export contained no food orders",
    sheets=[
        InputSheet(
            key="portal",
            name="food portal export",
            fingerprint=[
                "SG Order ID",
                "Partner Order ID",
                "Vertical",
                "Order Total",
                "SG Delivery Expense",
                "Commission Charged",
                "Net Payable Amount to Partner",
            ],
            # The worked ANALYSIS sheet bolts the vendor columns onto the portal
            # ones and satisfies the fingerprint; these headers disqualify it.
            exclude=[
                "order_id",
                "shoppinggate_fees",
                "shoppinggate_percentage",
                "MATCH ID'S",
                "Match ID's",
            ],
            filter='vertical = "food"',
            columns=[
                _col("sg_order_id", "SG Order ID", type="id"),
                _col(
                    "order_id", "Partner Order ID", type="id", required=True, skip=True
                ),
                _col("vertical", "Vertical", required=True),
                _col("order_date", "Order Date", type="date"),
                _col("order_time", "Order Time"),
                _col("provider", "Provider Name"),
                _col("food_order_type", "Food Order Type"),
                _col("restaurant_id", "Restaurant ID/Seller ID", type="id"),
                _col("restaurant_name", "Restaurant Name"),
                _col("transaction_id", "Transaction ID"),
                _col("order_status", "Order Status", required=True, skip=True),
                _col("payment_status", "Payment Status"),
                _col("payment_method", "Payment Method"),
                _col("sub_total", "Sub Total", type="money"),
                _col("promocode", "Promocode"),
                _col("discount", "SG Discount/Promo Amount", type="money"),
                _col("sg_delivery", "SG Delivery Expense", type="money", required=True),
                _col("vat", "VAT", type="money"),
                _col("order_total", "Order Total", type="money"),
                _col("total_amount", "Total Amount", type="money", required=True),
                _col("stated_rate", "SG Comission %", "SG Commission %", type="rate"),
                _col("commission_amount", "Commission Amount", type="money"),
                _col("partner_payout", "Partner Payout", type="money"),
                _col(
                    "commission_charged",
                    "Commission Charged",
                    type="money",
                    required=True,
                ),
                _col("wallet_amount", "Wallet Amount", type="money"),
                _col("card_amount", "Applepay/Card Amount", type="money"),
                _col("refund_amount", "Refund Amount", type="money"),
                _col("compensation_amount", "Compensation Amount", type="money"),
                _col("net_payable", "Net Payable Amount to Partner", type="money"),
            ],
        )
    ],
)

VENDOR = InputFile(
    role="vendor",
    title="Vendor statement",
    caption="Account statement from vendor",
    required=True,
    empty_message="The vendor report contained no orders",
    sheets=[
        InputSheet(
            key="orders",
            name="food vendor report",
            fingerprint=[
                "order_id",
                "requestedDeliveryDate",
                "shoppinggate_percentage",
                "shoppinggate_fees",
                "original_deliveryFee",
                "promotion_shoppinggate_charge_amount",
                "promotion_chef_charge_amount",
            ],
            # The same ANALYSIS sheet also satisfies this fingerprint.
            exclude=["Partner Order ID", "MATCH ID'S", "Match ID's"],
            columns=[
                _col("requested_delivery_date", "requestedDeliveryDate", type="date"),
                _col("brand_name", "brand_name"),
                # A totals row has no order id, and is not an order.
                _col("order_id", "order_id", type="id", required=True, skip=True),
                _col("order_no", "order_no", type="id"),
                _col("customer_name", "customer_name"),
                _col("total", "total", type="money", required=True),
                _col("order_chef_total", "order_chef_total", type="money"),
                _col("shoppinggate_percentage", "shoppinggate_percentage", type="rate"),
                _col(
                    "shoppinggate_fees",
                    "shoppinggate_fees",
                    type="money",
                    required=True,
                ),
                _col(
                    "original_delivery_fee",
                    "original_deliveryFee",
                    type="money",
                    required=True,
                ),
                _col(
                    "sg_delivery_charge",
                    "promotion_shoppinggate_charge_amount",
                    type="money",
                    required=True,
                ),
                _col("delivery_fee", "deliveryFee", type="money"),
                _col("order_status", "order_status", required=True, skip=True),
                _col("order_items", "order_items"),
                _col("promotion_discount", "promotion_discount", type="money"),
                _col(
                    "chef_delivery_charge",
                    "promotion_chef_charge_amount",
                    type="money",
                    required=True,
                ),
            ],
        )
    ],
)

PARAMETERS = [
    Parameter(
        key="tolerance", label="Amount tolerance", kind="tolerance", value="0.01"
    ),
]

_BOTH = "AND(NOT(ISBLANK(portal)), NOT(ISBLANK(vendor)))"
_OUT = "ABS({}) > param.tolerance"

MODEL = Model(
    sides=[
        Side(role="portal", key="order_id", active='order_status = "delivered"'),
        Side(role="vendor", key="order_id", active='order_status = "delivered"'),
    ],
    fields=[
        Named(
            key="ids_match",
            formula=f"AND({_BOTH}, EXACT(portal.order_id, vendor.order_id))",
        ),
        Named(key="match", formula='IF(ids_match, "TRUE", "FALSE")'),
        Named(key="amount_diff", formula="portal.total_amount - vendor.total"),
        Named(
            key="fee_diff",
            formula="portal.commission_charged - vendor.shoppinggate_fees",
        ),
        # SG's share of the delivery: the gross fee less what the customer and
        # the restaurant paid.
        Named(
            key="sg_bear",
            formula="vendor.original_delivery_fee - portal.sg_delivery - vendor.chef_delivery_charge",
        ),
        Named(key="chefz_bear", formula="vendor.chef_delivery_charge"),
        Named(key="cust_del", formula="portal.sg_delivery"),
        # A zero share is written as a plain 0.
        Named(key="split_share", formula="IF(sg_bear = 0, 0, sg_bear)"),
        Named(
            key="due",
            formula="vendor.total + vendor.sg_delivery_charge - vendor.shoppinggate_fees",
        ),
        Named(
            key="cancelled",
            formula=(
                'OR(IN(portal.order_status, "cancelled", "canceled"), '
                'IN(vendor.order_status, "cancelled", "canceled"))'
            ),
        ),
        # The order status on each side we have; one value when they agree.
        Named(
            key="status_text",
            formula=(
                "IF(AND(NOT(ISBLANK(portal.order_status)), NOT(ISBLANK(vendor.order_status)), "
                "portal.order_status <> vendor.order_status), "
                '"Portal: " & portal.order_status & " / Vendor: " & vendor.order_status, '
                "TEXT(COALESCE(portal.order_status, vendor.order_status)))"
            ),
        ),
        # What the client dashboard counts for the order. A vendor-only order
        # counts the vendor's own food, fees and delivery split.
        Named(
            key="client_food",
            formula="IF(ISBLANK(portal), vendor.order_chef_total, portal.sub_total)",
        ),
        Named(
            key="client_customer",
            formula=(
                "IF(ISBLANK(portal), vendor.original_delivery_fee - vendor.sg_delivery_charge"
                " - vendor.chef_delivery_charge, portal.sg_delivery)"
            ),
        ),
        Named(
            key="client_sg_share",
            formula="IF(ISBLANK(portal), vendor.sg_delivery_charge, N(sg_bear))",
        ),
        Named(
            key="client_commission",
            formula="IF(ISBLANK(portal), vendor.shoppinggate_fees, portal.commission_charged)",
        ),
    ],
    row_rules=[
        RowRule(
            over="inactive",
            code="STATUS_NOT_DELIVERED",
            order_no="key",
            detail=(
                'IF(role = "portal", "Portal", "Vendor") & " status " & row.order_status'
                ' & " is not Delivered."'
            ),
        ),
        RowRule(
            over="duplicates",
            code="DUPLICATE_ORDER_ID",
            order_no="key",
            detail=(
                'IF(role = "portal", "Partner Order ID ", "order_id ") & key & " appears " '
                '& copies & " times " & IF(role = "portal", "on the portal.", '
                '"on the vendor report.")'
            ),
        ),
    ],
    rules=[
        # Delivered on one side, another status on the other.
        *(
            StatusRule(
                when=f"AND({this}_active, NOT({that}_active), NOT(ISBLANK({that})))",
                verdict="held",
                exception=Raise(
                    code="STATUS_MISMATCH",
                    detail=(
                        '"Portal status " & portal.order_status & ", vendor status " '
                        '& vendor.order_status & "."'
                    ),
                ),
            )
            for this, that in (("portal", "vendor"), ("vendor", "portal"))
        ),
        StatusRule(
            when="ISBLANK(vendor)",
            verdict="portal_only",
            exception=Raise(
                code="ORDER_MISSING_IN_VENDOR",
                amount="portal.total_amount",
                detail='"Portal order " & key & " has no vendor row."',
            ),
        ),
        StatusRule(
            when="ISBLANK(portal)",
            verdict="vendor_only",
            exception=Raise(
                code="MISSING_FROM_PORTAL",
                amount="due",
                detail='"Vendor order " & key & " has no portal row."',
            ),
        ),
        StatusRule(when="OR(portal_copies > 1, vendor_copies > 1)", verdict="held"),
        StatusRule(when="TRUE", checks=True, clean="settled", broken="held"),
    ],
    checks=[
        # The fee gap usually follows the amount gap, so it is told on the
        # same exception rather than raised again.
        Check(
            code="FOOD_AMOUNT_MISMATCH",
            when=_OUT.format("amount_diff"),
            amount="amount_diff",
            detail=(
                '"Portal total " & portal.total_amount & " against vendor total " '
                '& vendor.total & " (difference " & amount_diff & ")." & '
                f"IF({_OUT.format('fee_diff')}, "
                '" Fee difference " & fee_diff & " follows the same gap (portal " '
                '& portal.commission_charged & ", vendor " & vendor.shoppinggate_fees '
                '& ")."'
                ', "")'
            ),
        ),
        Check(
            code="FEE_MISMATCH",
            when=f"AND(NOT({_OUT.format('amount_diff')}), {_OUT.format('fee_diff')})",
            amount="fee_diff",
            detail=(
                '"Portal commission " & portal.commission_charged & " against vendor fees " '
                '& vendor.shoppinggate_fees & " (difference " & fee_diff & ")."'
            ),
        ),
        Check(
            code="DELIVERY_SPLIT_MISMATCH",
            when=_OUT.format("split_share - vendor.sg_delivery_charge"),
            amount="split_share - vendor.sg_delivery_charge",
            detail=(
                '"SG delivery share " & split_share & " against vendor charge " '
                '& vendor.sg_delivery_charge & "."'
            ),
        ),
        Check(
            code="REFUND_ON_DELIVERED",
            when="portal.refund_amount <> 0",
            amount="portal.refund_amount",
            detail=(
                '"Delivered order " & portal.order_id & " carries a refund of " '
                '& portal.refund_amount & "."'
            ),
        ),
    ],
    sort=["COALESCE(portal.order_date, vendor.requested_delivery_date)", "key"],
    wallet=WalletCheck(
        rows="portal",
        vertical='"food"',
        wallet_types={"Food": "food"},
        sg_order_id="sg_order_id",
        reference="order_id",
        order_date="order_date",
        amount="wallet_amount",
        tolerance="param.tolerance",
    ),
    sets=[
        SetDef(key="settled", over="entities", where='verdict = "settled"'),
        SetDef(key="held", over="entities", where='verdict = "held"'),
        SetDef(key="portal_only", over="entities", where='verdict = "portal_only"'),
        SetDef(key="vendor_only", over="entities", where='verdict = "vendor_only"'),
        SetDef(key="paired", over="entities", where="ids_match"),
        # The client dashboard: paired orders and vendor-only ones.
        SetDef(
            key="client",
            over="entities",
            where='OR(ids_match, verdict = "vendor_only")',
        ),
        # The Orders sheet: paired orders that are not cancelled.
        SetDef(key="orders", over="entities", where="AND(ids_match, NOT(cancelled))"),
        SetDef(
            key="delivered_portal", over="portal", where='order_status = "delivered"'
        ),
        SetDef(
            key="compensated", over="delivered_portal", where="compensation_amount <> 0"
        ),
    ],
    metrics=[
        # The run's summary.
        Named(key="portal_rows", formula='COUNTX("portal")'),
        Named(key="vendor_rows", formula='COUNTX("vendor")'),
        Named(key="paired", formula=f'COUNTX("entities", {_BOTH})'),
        Named(key="settled_orders", formula='COUNTX("settled")'),
        Named(key="settled_due", formula='MONEY(SUMX("settled", N(due)))'),
        Named(key="held_orders", formula='COUNTX("held")'),
        Named(key="held_due", formula='MONEY(SUMX("held", N(due)))'),
        Named(key="portal_only", formula='COUNTX("portal_only")'),
        Named(key="vendor_only", formula='COUNTX("vendor_only")'),
        Named(key="vendor_only_due", formula='MONEY(SUMX("vendor_only", N(due)))'),
        Named(
            key="amount_breaks",
            formula='COUNTX("exceptions", code = "FOOD_AMOUNT_MISMATCH")',
        ),
        Named(key="fee_breaks", formula='COUNTX("exceptions", code = "FEE_MISMATCH")'),
        Named(
            key="delivery_breaks",
            formula='COUNTX("exceptions", code = "DELIVERY_SPLIT_MISMATCH")',
        ),
        Named(
            key="not_delivered",
            formula='COUNTX("exceptions", code = "STATUS_NOT_DELIVERED")',
        ),
        Named(key="compensation_orders", formula='COUNTX("compensated")'),
        Named(
            key="compensation_total",
            formula='MONEY(SUMX("compensated", compensation_amount))',
        ),
        Named(
            key="settled_subtotal", formula='MONEY(SUMX("settled", portal.sub_total))'
        ),
        Named(
            key="settled_customer_delivery",
            formula='MONEY(SUMX("settled", portal.sg_delivery))',
        ),
        Named(
            key="settled_sg_delivery",
            formula='MONEY(SUMX("settled", vendor.sg_delivery_charge))',
        ),
        Named(
            key="settled_vendor_fees",
            formula='MONEY(SUMX("settled", vendor.shoppinggate_fees))',
        ),
        Named(
            key="settled_food_gap",
            formula=(
                'MONEY(SUMX("settled", vendor.order_chef_total) '
                '- SUMX("settled", portal.sub_total))'
            ),
        ),
        Named(key="portal_date_start", formula='MINX("delivered_portal", order_date)'),
        Named(key="portal_date_end", formula='MAXX("delivered_portal", order_date)'),
        Named(key="vendor_name", formula='ONLYX("delivered_portal", provider)'),
        # The client dashboard.
        Named(key="orders", formula='COUNTX("client")'),
        Named(key="food", formula='MONEY(SUMX("client", client_food))'),
        Named(
            key="customer_delivery", formula='MONEY(SUMX("client", client_customer))'
        ),
        Named(key="sg_delivery", formula='MONEY(SUMX("client", client_sg_share))'),
        Named(
            key="merchant_delivery",
            formula='MONEY(SUMX("client", vendor.chef_delivery_charge))',
        ),
        Named(key="sg_commission", formula='MONEY(SUMX("client", client_commission))'),
        Named(
            key="vendor_fees", formula='MONEY(SUMX("client", vendor.shoppinggate_fees))'
        ),
        Named(key="wallet", formula='MONEY(SUMX("client", portal.wallet_amount))'),
        Named(key="card", formula='MONEY(SUMX("client", portal.card_amount))'),
        Named(key="portal_total", formula='MONEY(SUMX("client", portal.total_amount))'),
        Named(key="promo", formula='MONEY(SUMX("client", portal.discount))'),
        Named(key="road_off", formula="vendor_fees - sg_commission"),
        Named(
            key="net_payable",
            formula="food + customer_delivery + sg_delivery - vendor_fees",
        ),
        Named(key="collection", formula="wallet + card"),
        Named(key="collection_gap", formula="collection - portal_total"),
        Named(key="remaining", formula="promo + collection - portal_total"),
        Named(key="order_collection", formula="portal_total - collection"),
        Named(key="net_difference", formula="order_collection - promo"),
        Named(
            key="delivery_total",
            formula="customer_delivery + sg_delivery + merchant_delivery",
        ),
        Named(
            key="average_delivery",
            formula="IF(orders = 0, 0, delivery_total / orders)",
        ),
        *(
            Named(
                key=f"{share}_share",
                formula=f"IF(delivery_total = 0, 0, {part} / delivery_total)",
            )
            for share, part in (
                ("customer", "customer_delivery"),
                ("sg", "sg_delivery"),
                ("merchant", "merchant_delivery"),
            )
        ),
    ],
    history=History(
        counted="settled_orders",
        total='COUNTX("entities")',
        unit='"orders"',
        net_payable="settled_due",
        vendor="vendor_name",
        period_start="portal_date_start",
        period_end="portal_date_end",
        report_name=(
            '"reconciliation-food" & IF(AND(NOT(ISBLANK(portal_date_start)), '
            'NOT(ISBLANK(portal_date_end))), "-" & portal_date_start & "-to-" '
            '& portal_date_end, "") & ".xlsx"'
        ),
    ),
)
