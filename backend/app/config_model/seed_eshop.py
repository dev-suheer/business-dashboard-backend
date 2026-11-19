"""eShop on the generic runner: how version 1 reads the uploads and reconciles.

eShop used to be a pipeline in code. Each rule below is that code, in the same
order, written as a formula; ``tests/test_parity.py`` proves the reports and
the figures identical to the ones the client approved.

* The commission base is always portal GMV, never the promo catalogue price.
  The promo rate applies to a promotional SKU ordered on or before the cutoff.
* An order is its portal lines plus the statement rows that name it. The
  statement is read whole (the converter writes overlapping copies of the same
  transactions, which collapse into one), and a credit note that names its
  order only by a truncated reference takes it from that prefix and its
  amount, or is reported, never guessed.
* An RTO is out of the payable, and an earlier cycle's payment for it is
  clawed back; a Return is paid and then reversed, leaving our commission;
  Ready to Pickup is payable but flagged. Shipping is never deducted.
"""

from app.config_model.models import (
    Finding,
    Group,
    History,
    InputColumn,
    InputFile,
    InputSheet,
    Lookup,
    Member,
    Model,
    Named,
    Reject,
    RowFields,
    RowRule,
    SetDef,
    StatusRule,
    Stop,
    Unique,
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


_CHECK = " Check that the right file went into that field."

# What the report calls each place a statement row with no portal order falls.
MATCH_STATE_LABELS = {
    "prior_period": "Prior period",
    "later_period": "Later period",
    "unmatched": "Missing from portal",
    "ambiguous": "Ambiguous credit note",
    "matched": "Matched",
}

PORTAL = InputFile(
    role="portal",
    title="Portal export",
    caption="One row per order line",
    required=True,
    empty_message="The portal export contained no usable order lines",
    sheets=[
        InputSheet(
            key="portal",
            name="portal export",
            fingerprint=["Order Number", "GMV", "SG Commission", "Order Status"],
            # The legacy recon tab bolts the promo catalogue onto a subset of
            # the portal columns; this header appears only there.
            exclude=["MATCH SKU's"],
            first_only=True,
            missing_hint=_CHECK,
            columns=[
                # A row with no order number is not an order line: the sheet
                # leaves blank rows before a trailing summary block.
                _col("order_ref", "Order Number", required=True, skip=True),
                _col("order_date", "Date", type="date", required=True),
                _col("merchant_id", "Merchant Id"),
                _col("merchant_name", "Merchant Name"),
                _col("sku", "SG SKU"),
                _col("merchant_sku", "Merchant SKU"),
                _col("brand", "Brand"),
                _col("item_title", "Item Title"),
                _col("gmv", "GMV", type="decimal", required=True),
                _col("units", "Units", type="decimal"),
                _col("coupon_name", "Coupon Name"),
                _col("coupon_value", "Coupon Value", type="decimal"),
                _col("actual_commission", "SG Commission", type="money", required=True),
                _col("status", "Order Status", required=True),
                # Prior-cycle settlement: what makes a clawback computable
                # without a settlement history upload.
                _col("amount_paid", "Paid", type="decimal"),
                # Display only: the report's dashboard, and the wallet check.
                _col("payment_type", "Payment Type"),
                _col("wallet_amount", "Wallet Amount", type="decimal"),
                _col("non_wallet_amount", "Non-Wallet Amount", type="decimal"),
                _col("shipping_owner", "Shipping Ownership"),
                _col("shipping_cost", "Shipping Cost", type="decimal"),
            ],
            fields=[
                Named(key="order_no", formula="UPPER(order_ref)"),
                Named(
                    key="status",
                    formula=(
                        'IFS(SUBSTITUTE(status, " ", "") = "Delivered", "Delivered", '
                        'SUBSTITUTE(status, " ", "") = "ReadytoPickup", "Ready to Pickup", '
                        'SUBSTITUTE(status, " ", "") = "Return", "Return", '
                        'SUBSTITUTE(status, " ", "") = "RTO", "RTO")'
                    ),
                ),
            ],
            rejects=[
                Reject(
                    when="OR(ISBLANK(order_date), ISBLANK(gmv), ISBLANK(status))",
                    message=(
                        '"Portal row for order " & order_ref & '
                        '" is missing a usable date, GMV or status"'
                    ),
                )
            ],
        )
    ],
)

VENDOR = InputFile(
    role="vendor",
    title="Vendor statement",
    caption="Account statement from vendor",
    required=True,
    sheets=[
        InputSheet(
            key="ledger",
            name="vendor statement",
            fingerprint=["دائن", "مدين", "البيان", "نوع المستند"],
            set="statement",
            columns=[
                _col("balance", "الرصيد الحالي", type="decimal"),
                _col("credit", "دائن", type="decimal", required=True),
                _col("debit", "مدين", type="decimal", required=True),
                _col("raw_ref", "رقم المرجع"),
                _col("raw_desc", "البيان", required=True),
                # Only the vendor template carries this. The converter's
                # statement has no order-number column, so the description
                # stays the fallback.
                _col("order_no", "رقم الطلب", "Order Number"),
                _col("doc_no", "رقم المستند"),
                _col("doc_type", "نوع المستند", required=True),
                _col("doc_date", "التاريخ", type="date", required=True),
            ],
            fields=[
                # Matched on distinctive words: the return invoice type
                # contains the sales invoice type, so returns come first.
                # English for a template row whose type was typed in English.
                Named(
                    key="doc_type",
                    formula=(
                        'IFS(ISBLANK(doc_type), "unknown", '
                        'CONTAINS(doc_type, "مردود"), "sales_return", '
                        'CONTAINS(doc_type, "فاتورة"), "sales_invoice", '
                        'CONTAINS(doc_type, "قيد"), "journal_entry", '
                        'OR(CONTAINS(doc_type, "return"), CONTAINS(doc_type, "credit note")), '
                        '"sales_return", '
                        'CONTAINS(doc_type, "invoice"), "sales_invoice", '
                        'OR(CONTAINS(doc_type, "journal"), CONTAINS(doc_type, "commission"), '
                        'CONTAINS(doc_type, "payment")), "journal_entry", '
                        'TRUE, "unknown")'
                    ),
                ),
                # One sheet writes 82.00 as text and another the number 82;
                # both are the same amount everywhere.
                Named(key="debit", formula="MONEY(debit)"),
                Named(key="credit", formula="MONEY(credit)"),
                Named(key="balance", formula="MONEY(balance)"),
                # The template's own column wins. Never the reference column,
                # which the vendor's system truncates to ten characters.
                Named(
                    key="order_no",
                    formula=(
                        'COALESCE(REGEX(UPPER(order_no), "SG-SH-\\d+"), '
                        'REGEX(UPPER(raw_desc), "SG-SH-\\d+"))'
                    ),
                ),
            ],
            # A page header, a subtotal, or a blank spacer row.
            filter='NOT(AND(doc_type = "unknown", ISBLANK(doc_date)))',
            # The converter's overlapping copies collapse into one transaction.
            unique=Unique(
                keys=[
                    "TEXT(doc_no)",
                    "TEXT(order_no)",
                    "doc_type",
                    "TEXT(doc_date)",
                    "TEXT(debit)",
                    "TEXT(credit)",
                ]
            ),
        )
    ],
)

PROMO = InputFile(
    role="promo",
    title="Discount base sheet",
    caption="Promotional SKUs",
    required=True,
    sheets=[
        InputSheet(
            key="promo",
            name="discount base sheet",
            fingerprint=["رمز المنتج", "سعر العرض"],
            # Disqualifies the legacy recon tab, which repeats these promo
            # columns alongside the portal ones.
            exclude=["Order Number", "GMV"],
            first_only=True,
            missing_hint=_CHECK,
            columns=[
                _col("sku", "رمز المنتج", required=True, skip=True),
                # Carried for reference; never the commission base.
                _col("offer_price", "سعر العرض", type="decimal"),
                _col("merchant_sku", "رمز البائع"),
            ],
        )
    ],
)

SETTLEMENTS = InputFile(
    role="settlements",
    title="Settlement history",
    caption="Optional · prior settlements, for clawbacks",
    required=False,
    shown=False,
    sheets=[
        InputSheet(
            key="settlements",
            name="settlement history",
            fingerprint=["order_no"],
            first_only=True,
            missing_hint=_CHECK,
            columns=[
                _col(
                    "order_no",
                    "order_no",
                    "Order Number",
                    "order no",
                    required=True,
                    skip=True,
                ),
                _col(
                    "amount_paid",
                    "amount_paid",
                    "amount paid",
                    "Paid",
                    type="decimal",
                    required=True,
                    skip=True,
                ),
                _col("settled_on", "settled_on", "settled on", "Date", type="date"),
                _col("cycle_ref", "cycle_ref", "cycle ref"),
            ],
            fields=[Named(key="order_no", formula="UPPER(order_no)")],
        )
    ],
)

_TOL = "param.line_tolerance"


def _variance_class(base: str, actual: str, expected: str, rate: str) -> str:
    """Within tolerance is clean. Above, we overcharged. Below, it is a wrong
    rate when what we took matches the other configured rate, a coupon we
    absorbed otherwise."""
    return (
        f'IFS(ABS({actual} - {expected}) <= {_TOL}, "clean", '
        f'{actual} - {expected} > {_TOL}, "overcharge", '
        f"OR(AND({rate} <> param.standard_rate, "
        f"ABS(MONEY({base} * param.standard_rate) - {actual}) <= {_TOL}), "
        f"AND({rate} <> param.promo_rate, "
        f"ABS(MONEY({base} * param.promo_rate) - {actual}) <= {_TOL})), "
        '"wrong_rate", TRUE, "promo_absorbed")'
    )


_DIGITS = 'REGEX(key, "(\\d+)$", 1)'
_LINES = "n_lines > 0"

MODEL = Model(
    builder="group",
    lookups=[
        Lookup(key="promo", set="promo", by="sku"),
        # What an earlier cycle paid, from the settlement history; the last
        # row for an order stands.
        Lookup(
            key="settled",
            set="settlements",
            by="order_no",
            value="amount_paid",
            combine="last",
        ),
        Lookup(
            key="order_gmv",
            set="portal",
            by="order_no",
            value="gmv",
            combine="sum",
            then="MONEY(value)",
        ),
        # Delivered and on the statement: what B2C invoices bill.
        Lookup(key="billed", set="billed", by="key"),
    ],
    row_fields=[
        RowFields(
            set="portal",
            fields=[
                # The promo rate needs the SKU in the catalogue and the order
                # on or before the cutoff.
                Named(
                    key="rate",
                    formula=(
                        'IF(AND(NOT(ISBLANK(sku)), NOT(ISBLANK(LOOKUP("promo", sku))), '
                        "order_date <= param.promo_cutoff), param.promo_rate, "
                        "param.standard_rate)"
                    ),
                ),
                # Unrounded: rounding happens once, where a figure is reported.
                Named(key="expected", formula="gmv * rate"),
                Named(key="variance", formula="actual_commission - expected"),
                Named(
                    key="variance_class",
                    formula=_variance_class(
                        "gmv", "actual_commission", "expected", "rate"
                    ),
                ),
            ],
        ),
        RowFields(
            set="statement",
            fields=[
                Named(
                    key="amount",
                    formula="IFS(N(credit) <> 0, credit, N(debit) <> 0, debit, TRUE, 0)",
                ),
                # A return invoice carries only its truncated reference:
                # SG-SH-0611 prefixes SG-SH-06115 and SG-SH-06118, and only the
                # amount separates them.
                Named(
                    key="unresolved",
                    formula="AND(ISBLANK(order_no), NOT(ISBLANK(raw_ref)))",
                ),
                Named(
                    key="candidates",
                    formula='IF(unresolved, PREFIXCOUNT("order_gmv", UPPER(raw_ref)), 0)',
                ),
                Named(
                    key="resolved",
                    formula=(
                        'IF(unresolved, MATCHPREFIX("order_gmv", UPPER(raw_ref), amount, '
                        "param.order_tolerance), BLANK())"
                    ),
                ),
                Named(
                    key="ambiguous", formula="AND(candidates > 1, ISBLANK(resolved))"
                ),
                Named(key="order_no", formula="COALESCE(order_no, resolved)"),
            ],
        ),
    ],
    members=[
        Member(set="portal", key="order_no", name="lines"),
        # Statement rows with no portal order make orders of their own, which
        # are reported against the period.
        Member(set="statement", key="order_no", name="entries"),
    ],
    groups=[
        Group(
            name="brands",
            of="lines",
            key='COALESCE(brand, "Unknown")',
            fields=[
                Named(key="gmv", formula='MONEY(SUMX("lines", gmv))'),
                Named(key="coupon", formula='MONEY(SUMX("lines", N(coupon_value)))'),
                Named(key="expected", formula='SUMX("lines", expected)'),
            ],
        )
    ],
    fields=[
        Named(key="n_lines", formula='COUNTX("lines")'),
        Named(key="n_entries", formula='COUNTX("entries")'),
        # The order's date is the earliest of its lines'.
        Named(key="order_date", formula='MINX("lines", order_date)'),
        Named(key="merchant_id", formula='COALESCEX("lines", merchant_id)'),
        Named(key="gmv_raw", formula='SUMX("lines", gmv)'),
        Named(key="expected_raw", formula='SUMX("lines", expected)'),
        Named(key="actual_raw", formula='SUMX("lines", actual_commission)'),
        Named(
            key="promo_raw",
            formula='SUMX("lines", IF(rate = param.promo_rate, gmv, 0))',
        ),
        Named(
            key="standard_raw",
            formula='SUMX("lines", IF(rate = param.promo_rate, 0, gmv))',
        ),
        Named(
            key="absorbed_raw",
            formula='SUMX("lines", IF(variance_class = "promo_absorbed", -variance, 0))',
        ),
        Named(
            key="overcharged_raw",
            formula='SUMX("lines", IF(variance_class = "overcharge", variance, 0))',
        ),
        Named(key="statuses", formula='JOINX("lines", status, ", ", TRUE)'),
        Named(key="has_rto", formula='COUNTX("lines", status = "RTO") > 0'),
        Named(key="has_return", formula='COUNTX("lines", status = "Return") > 0'),
        Named(
            key="has_ready", formula='COUNTX("lines", status = "Ready to Pickup") > 0'
        ),
        # What an earlier cycle already paid: the export's Paid column, else
        # the settlement history.
        Named(
            key="paid",
            formula=(
                'IF(COUNTX("lines", N(amount_paid) <> 0) > 0, '
                'SUMX("lines", IF(N(amount_paid) <> 0, amount_paid, 0)), BLANK())'
            ),
        ),
        Named(
            key="settled",
            formula='IF(N(paid) <> 0, paid, LOOKUP("settled", key))',
        ),
        # The most reversing status governs an order whose lines disagree:
        # RTO, then Return, then Ready to Pickup.
        Named(
            key="treatment",
            formula=(
                'IFS(AND(has_rto, N(settled) <> 0), "CLAWBACK", has_rto, "RTO_EXCLUDED", '
                'has_return, "RETURN_ADJUSTED", has_ready, "PENDING_DELIVERY")'
            ),
        ),
        Named(key="included", formula="NOT(has_rto)"),
        # Every reported figure is quantized once, from the unrounded totals.
        Named(key="portal_gmv", formula="MONEY(gmv_raw)"),
        Named(key="expected_commission", formula="MONEY(expected_raw)"),
        Named(key="actual_commission", formula="MONEY(actual_raw)"),
        Named(
            key="payable",
            formula="IF(has_rto, MONEY(0), MONEY(gmv_raw - expected_raw))",
        ),
        # An RTO an earlier cycle paid is clawed back; a Return is paid, then
        # its whole base comes back off, so the commission stays with us.
        Named(
            key="adjustments",
            formula=(
                'IFS(treatment = "CLAWBACK", MONEY(-settled), has_rto, MONEY(0), '
                "has_return, MONEY(-portal_gmv), TRUE, MONEY(0))"
            ),
        ),
        Named(key="net_payable", formula="MONEY(payable + adjustments)"),
        Named(key="commission_variance", formula="MONEY(actual_raw - expected_raw)"),
        Named(
            key="dominant_rate",
            formula="IF(promo_raw > standard_raw, param.promo_rate, param.standard_rate)",
        ),
        Named(
            key="variance_class",
            formula=_variance_class(
                "gmv_raw", "actual_raw", "expected_raw", "dominant_rate"
            ),
        ),
        Named(key="promo_base", formula="MONEY(IF(included, promo_raw, 0))"),
        Named(key="standard_base", formula="MONEY(IF(included, standard_raw, 0))"),
        Named(key="absorbed_commission", formula="MONEY(absorbed_raw)"),
        # The payment columns repeat the order's figure on every line, so the
        # first line's stands; shipping and coupons are summed.
        Named(key="payment_type", formula='COALESCEX("lines", payment_type)'),
        Named(
            key="wallet_amount",
            formula='MONEY(N(COALESCEX("lines", wallet_amount)))',
        ),
        Named(
            key="non_wallet_amount",
            formula='MONEY(N(COALESCEX("lines", non_wallet_amount)))',
        ),
        Named(key="coupon_value", formula='MONEY(SUMX("lines", N(coupon_value)))'),
        Named(key="shipping_cost", formula='MONEY(SUMX("lines", N(shipping_cost)))'),
        Named(
            key="sg_shipping",
            formula='MONEY(SUMX("lines", IF(shipping_owner = "sg", N(shipping_cost), 0)))',
        ),
        # The statement's side.
        Named(key="matched", formula="n_entries > 0"),
        Named(key="e_debit", formula='SUMX("entries", N(debit))'),
        Named(key="e_credit", formula='SUMX("entries", N(credit))'),
        Named(key="first_date", formula='MINX("entries", doc_date)'),
        Named(
            key="has_credit_note",
            formula='COUNTX("entries", doc_type = "sales_return") > 0',
        ),
        Named(key="vendor_amount", formula="IF(matched, MONEY(e_debit), BLANK())"),
        Named(key="vendor_credit", formula="IF(matched, MONEY(e_credit), BLANK())"),
        Named(key="vendor_date", formula="IF(matched, first_date, BLANK())"),
        Named(
            key="amount_diff",
            formula="IF(matched, MONEY(portal_gmv - e_debit), BLANK())",
        ),
        Named(
            key="reversing",
            formula=(
                'IFS(AND(has_rto, has_return), "RTO/Return", has_rto, "RTO", '
                'has_return, "Return")'
            ),
        ),
        # The SG Order ID an order is filed under: SG-SH-08986 is 8986.
        Named(
            key="order_digits",
            formula=f"IF(ISBLANK({_DIGITS}), BLANK(), TEXT(INTEGER({_DIGITS})))",
        ),
        Named(
            key="statement_amount", formula="MONEY(IF(e_debit <> 0, e_debit, e_credit))"
        ),
    ],
    # The cycle, from the portal order dates. A clawback is left out: it was
    # settled in an earlier cycle, and its date would stretch the window back.
    context=[
        Named(
            key="period_start",
            formula=(
                'IF(COUNTX("current") > 0, '
                'MINX("current", order_date), MINX("orders", order_date))'
            ),
        ),
        Named(
            key="period_end",
            formula=(
                'IF(COUNTX("current") > 0, '
                'MAXX("current", order_date), MAXX("orders", order_date))'
            ),
        ),
    ],
    stops=[
        # The vendor statement is per vendor, so a run must be too.
        Stop(
            when='DISTINCTX("portal", merchant_id) > 1',
            message=(
                '"The portal export covers more than one merchant (" & '
                'JOINX("portal", merchant_id, ", ", TRUE) & "). Reconcile one vendor per run."'
            ),
        ),
    ],
    row_rules=[
        RowRule(
            over="statement",
            when="ambiguous",
            code="AMBIGUOUS_CREDIT_NOTE",
            amount="amount",
            detail=(
                '"Reference " & QUOTE(raw_ref) & " prefix-matches " & candidates & '
                '" orders (" & PREFIXKEYS("order_gmv", UPPER(raw_ref)) & ") and the '
                'amount " & amount & " did not separate them"'
            ),
        ),
    ],
    # The treatment that governs the order, most reversing first.
    rules=[
        StatusRule(when="n_lines = 0", verdict="statement_only"),
        StatusRule(when='treatment = "CLAWBACK"', verdict="Clawback"),
        StatusRule(when='treatment = "RTO_EXCLUDED"', verdict="RTO excluded"),
        StatusRule(when="NOT(matched)", verdict="Missing from vendor"),
        StatusRule(
            when="ABS(amount_diff) > param.order_tolerance",
            verdict="Amount mismatch",
        ),
        StatusRule(when='treatment = "RETURN_ADJUSTED"', verdict="Return adjusted"),
        StatusRule(when='treatment = "PENDING_DELIVERY"', verdict="Pending delivery"),
        StatusRule(when="TRUE", verdict="Matched"),
    ],
    findings=[
        Finding(
            code="ORDER_MISSING_IN_VENDOR",
            when=f"AND({_LINES}, NOT(matched))",
            amount="portal_gmv",
            detail='"Present in the portal export, absent from the vendor statement"',
        ),
        Finding(
            code="AMOUNT_MISMATCH",
            when=f"AND({_LINES}, matched, ABS(amount_diff) > param.order_tolerance)",
            amount="amount_diff",
            detail='"Portal " & portal_gmv & " vs statement " & MONEY(e_debit)',
        ),
        Finding(
            code="RTO_WITHOUT_CREDIT_NOTE",
            when=f"AND({_LINES}, matched, OR(has_rto, has_return), NOT(has_credit_note))",
            amount="portal_gmv",
            detail='"Status " & reversing & " with no reversing credit note in the statement"',
        ),
        Finding(
            code="CREDIT_NOTE_WITHOUT_RTO",
            when=f"AND({_LINES}, matched, has_credit_note, NOT(OR(has_rto, has_return)))",
            amount="MONEY(e_credit)",
            detail='"Credit note in the statement with no RTO or Return in the portal"',
        ),
        # A statement order with no portal counterpart, placed against the
        # period: the invoice date lags the order date, so it always straddles
        # the boundary, and only one inside the period is a break.
        Finding(
            code="MISSING_FROM_PORTAL",
            when="AND(n_lines = 0, ISBLANK(first_date))",
            amount="statement_amount",
            detail='"In the statement with no usable invoice date and no portal counterpart"',
        ),
        Finding(
            code="PRIOR_PERIOD",
            when="AND(n_lines = 0, first_date < period_start)",
            amount="statement_amount",
            detail='"Invoiced " & first_date & ", before the period opened on " & period_start',
        ),
        Finding(
            code="LATER_PERIOD",
            when="AND(n_lines = 0, first_date > period_end)",
            amount="statement_amount",
            detail='"Invoiced " & first_date & ", after the period closed on " & period_end',
        ),
        Finding(
            code="MISSING_FROM_PORTAL",
            when=(
                "AND(n_lines = 0, NOT(ISBLANK(first_date)), first_date >= period_start, "
                "first_date <= period_end)"
            ),
            amount="statement_amount",
            detail='"Invoiced " & first_date & ", inside the period, but absent from the portal"',
        ),
        Finding(
            code="COMMISSION_OVERCHARGE",
            when=f'AND({_LINES}, variance_class = "overcharge")',
            amount="commission_variance",
            detail=(
                '"Charged " & actual_commission & " against a contractual " & '
                'expected_commission & "; the excess is owed back to the vendor"'
            ),
        ),
        Finding(
            code="COMMISSION_WRONG_RATE",
            when=f'AND({_LINES}, variance_class = "wrong_rate")',
            amount="commission_variance",
            detail=(
                '"Charged " & actual_commission & ", which matches a different '
                'configured rate — investigate as a system error"'
            ),
        ),
        Finding(
            code="PROMO_ABSORBED",
            when=f'AND({_LINES}, variance_class = "promo_absorbed")',
            amount="absorbed_commission",
            detail=(
                '"A coupon reduced what we collected; we bear the shortfall and the '
                'vendor is still paid on the contractual rate"'
            ),
        ),
        Finding(
            code="PENDING_DELIVERY",
            when='treatment = "PENDING_DELIVERY"',
            amount="net_payable",
            detail='"Ready to Pickup — payable but not yet delivered"',
        ),
        # An RTO that predates the period was probably paid in an earlier
        # cycle, but with no settlement history that cannot be proved.
        Finding(
            code="CLAWBACK_UNRESOLVED",
            when='AND(treatment = "RTO_EXCLUDED", ISBLANK(LOOKUP("settled", key)))',
            amount="portal_gmv",
            detail=(
                '"RTO excluded from payable. No prior settlement is recorded, so any '
                'earlier overpayment cannot be clawed back automatically"'
            ),
        ),
    ],
    sort=["key"],
    # One check per order: the wallet share is an order-level figure.
    wallet=WalletCheck(
        rows="orders",
        vertical='"eshop"',
        wallet_types={"eshoporder": "eshop", "eshoporders": "eshop", "eshop": "eshop"},
        strip="-",
        sg_order_id="order_digits",
        reference="key",
        order_date="order_date",
        amount="wallet_amount",
        tolerance=_TOL,
    ),
    sets=[
        SetDef(key="orders", over="entities", where=_LINES),
        SetDef(key="included", over="orders", where="included"),
        SetDef(key="excluded", over="orders", where="NOT(included)"),
        SetDef(key="current", over="orders", where='treatment <> "CLAWBACK"'),
        SetDef(key="billed", over="orders", where="AND(included, matched)"),
        # Statement rows naming an order the portal does not have.
        SetDef(
            key="statement_only",
            over="statement",
            where=(
                'AND(NOT(ISBLANK(order_no)), ISBLANK(LOOKUP("order_gmv", order_no)), '
                "NOT(ISBLANK(doc_no)))"
            ),
        ),
        SetDef(
            key="balanced",
            over="statement",
            where="AND(NOT(ISBLANK(doc_date)), NOT(ISBLANK(balance)))",
        ),
        SetDef(key="closing_rows", over="balanced", where="doc_date = closing_date"),
    ],
    metrics=[
        # The run's summary, from the unrounded running totals, each
        # quantized once: the headline is the figure to pay on.
        Named(key="gross_sales", formula='MONEY(SUMX("orders", gmv_raw))'),
        Named(key="standard_base", formula='MONEY(SUMX("included", standard_raw))'),
        Named(key="promo_base", formula='MONEY(SUMX("included", promo_raw))'),
        Named(key="commission_earned", formula='MONEY(SUMX("included", expected_raw))'),
        Named(
            key="payable_before_adjustments",
            formula='MONEY(SUMX("included", gmv_raw) - SUMX("included", expected_raw))',
        ),
        Named(key="adjustments", formula='MONEY(SUMX("orders", adjustments))'),
        Named(
            key="net_payable", formula="MONEY(payable_before_adjustments + adjustments)"
        ),
        # The running balance after the statement's last transaction: the last
        # row, in document order, of the latest date.
        Named(key="closing_date", formula='MAXX("balanced", doc_date)'),
        Named(
            key="vendor_closing_balance",
            formula='MONEY(LASTX("closing_rows", balance))',
        ),
        Named(
            key="unexplained_difference",
            formula="MONEY(vendor_closing_balance - net_payable)",
        ),
        Named(
            key="promotional_absorption", formula='MONEY(SUMX("orders", absorbed_raw))'
        ),
        Named(
            key="overcharged_commission",
            formula='MONEY(SUMX("orders", overcharged_raw))',
        ),
        Named(key="portal_lines", formula='COUNTX("portal")'),
        Named(key="orders", formula='COUNTX("orders")'),
        Named(key="ledger_entries", formula='COUNTX("statement")'),
        Named(key="orders_matched", formula='COUNTX("orders", matched)'),
        Named(
            key="orders_exact",
            formula='COUNTX("orders", AND(matched, amount_diff = 0))',
        ),
        Named(key="merchant_id", formula='ONLYX("orders", merchant_id)'),
        # The client dashboard, over the orders in the payable.
        Named(key="payable_orders", formula='COUNTX("included")'),
        Named(key="gmv", formula='SUMX("included", portal_gmv)'),
        Named(key="vendor", formula='SUMX("included", N(vendor_amount))'),
        Named(key="amount_difference", formula='SUMX("included", N(amount_diff))'),
        Named(key="actual_commission", formula='SUMX("included", actual_commission)'),
        Named(key="round_off", formula="actual_commission - commission_earned"),
        Named(key="wallet", formula='SUMX("included", wallet_amount)'),
        Named(key="non_wallet", formula='SUMX("included", non_wallet_amount)'),
        Named(key="collection", formula="wallet + non_wallet"),
        Named(key="collection_gap", formula="collection - gmv"),
        Named(key="coupon", formula='SUMX("included", coupon_value)'),
        Named(key="remaining", formula="coupon + collection - gmv"),
        Named(key="order_collection", formula="gmv - collection"),
        Named(key="net_difference", formula="gmv - collection - coupon"),
        Named(key="sg_shipping", formula='SUMX("included", sg_shipping)'),
        Named(key="shipping", formula='SUMX("included", shipping_cost)'),
        Named(key="merchant_shipping", formula="shipping - sg_shipping"),
        Named(
            key="average_shipping",
            formula="IF(payable_orders = 0, 0, shipping / payable_orders)",
        ),
        Named(
            key="sg_shipping_share",
            formula="IF(shipping = 0, 0, sg_shipping / shipping)",
        ),
        Named(
            key="merchant_shipping_share",
            formula="IF(shipping = 0, 0, merchant_shipping / shipping)",
        ),
    ],
    history=History(
        counted='COUNTX("included")',
        total='COUNTX("orders")',
        unit='"orders"',
        net_payable="net_payable",
        vendor="merchant_id",
        period_start="period_start",
        period_end="period_end",
        report_name='"reconciliation-eshop-" & period_start & "-to-" & period_end & ".xlsx"',
    ),
)
