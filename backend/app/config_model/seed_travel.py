"""Travel on the generic runner: how version 1 reads the uploads and reconciles.

Travel used to be a pipeline in code. Each rule below is that code, in the same
order, written as a formula; ``tests/test_parity.py`` proves the reports and
the figures identical to the ones the client approved.

* A booking is everything under one booking id: its claim lines (a claim's
  ``A260726150195500_7889547`` and its credit ``…_R0`` both belong to
  ``A260726150195500``), its portal row (the earliest, when the portal repeats
  it), and the claim's free-text adjustments that name it.
* Local or international is decided by the claim's VAT, never by its DOM/INT
  label. What we charged is our local rate; what the vendor may bill is that
  rate, plus VAT when the booking is international.
* A booking matches when both sides carry it, nothing reversed it, and the
  portal says Processed. Anything else is listed with the reason, and kept
  out of the totals. A booking the claim does not bill is a break only inside
  the claim's own week.
"""

from app.config_model.models import (
    Finding,
    History,
    InputColumn,
    InputFile,
    InputSheet,
    Member,
    Model,
    Named,
    Raise,
    RowRule,
    RunRule,
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


def _enum(field: str, values: tuple[str, ...]) -> str:
    """The value its text names, spaces and case aside; "unknown" otherwise."""
    pairs = ", ".join(
        f'SUBSTITUTE({field}, " ", "") = "{v.replace(" ", "")}", "{v}"' for v in values
    )
    return f'IFS({pairs}, TRUE, "unknown")'


def _vertical(field: str) -> str:
    """The vertical a portal Vertical or a claim Service Type names."""
    return (
        f'IFS(IN({field}, "hotel", "hotels", "h"), "hotel", '
        f'IN({field}, "flight", "flights", "a"), "flight")'
    )


# The vertical a booking id names by its first letter.
_ID_VERTICAL = (
    'IFS(REGEX({id}, "^H") = "H", "hotel", REGEX({id}, "^A") = "A", "flight")'
)
# The date a booking id carries in its own digits: A260726150195500 is 2026-07-26.
_ID_DATE = 'DATEVALUE(REGEX({id}, "^[AH](\\d{{6}})", 1), "%y%m%d")'


def _shown(field: str) -> str:
    """A text as v1's messages wrote it, "None" when it is blank."""
    return f'COALESCE({field}, "None")'


# What the report calls each reason a booking is out of the totals.
REASON_LABELS = {
    "refunded": "Refunded",
    "cancelled": "Cancelled",
    "missing_in_vendor": "Missing from vendor claim",
    "missing_in_portal": "Missing from portal",
    "other": "Other",
}

PORTAL = InputFile(
    role="portal",
    title="Portal export",
    caption="Hotel and flight tabs",
    required=True,
    empty_message=(
        "{filename} holds no Hotel or Flights rows. A portal export for another "
        "category cannot be reconciled as travel."
    ),
    sheets=[
        InputSheet(
            key="portal",
            name="travel portal export",
            fingerprint=[
                "SG Order ID",
                "Partner Order ID",
                "Vertical",
                "Order Total",
                "Wallet Amount",
                "Applepay/Card Amount",
                "Commission Charged",
                "Net Payable Amount to Partner",
            ],
            # The portal header is shared with the Food export, and both the
            # client's own analysis tab and ours bolt the vendor columns onto
            # it. None of those are an input, and each carries one of these.
            exclude=[
                "Match ID's",
                "Booking ID",
                "Total Invoice Amount",
                "shoppinggate_percentage",
                "MATCH SKU's",
            ],
            columns=[
                _col("sg_order_id", "SG Order ID", required=True),
                _col("booking_id", "Partner Order ID", required=True),
                _col("vertical", "Vertical", required=True),
                _col("order_date", "Order Date", type="date", required=True),
                _col("order_time", "Order Time"),
                # The export repeats "Customer" three times; the first is the id.
                _col("customer_ref", "Customer"),
                _col("provider", "Provider Name"),
                _col("transaction_id", "Transaction ID"),
                _col("status", "Order Status", required=True),
                _col("payment_status", "Payment Status"),
                _col("payment_method", "Payment Method"),
                _col("sub_total", "Sub Total", type="money"),
                _col("promocode", "Promocode"),
                _col("discount", "SG Discount/Promo Amount", type="money"),
                _col("vat", "VAT", type="money", required=True),
                _col("order_total", "Order Total", type="money", required=True),
                _col("total_amount", "Total Amount", type="decimal"),
                _col(
                    "stated_rate", "SG Comission %", "SG Commission %", type="decimal"
                ),
                _col("commission_amount", "Commission Amount", type="money"),
                _col("partner_payout", "Partner Payout", type="money"),
                _col(
                    "commission_charged",
                    "Commission Charged",
                    type="money",
                    required=True,
                ),
                _col("wallet_amount", "Wallet Amount", type="money", required=True),
                _col(
                    "card_amount", "Applepay/Card Amount", type="money", required=True
                ),
                _col("refund_amount", "Refund Amount", type="money"),
                _col("compensation_amount", "Compensation Amount", type="money"),
                _col(
                    "net_payable",
                    "Net Payable Amount to Partner",
                    type="money",
                    required=True,
                ),
            ],
            fields=[
                # The booking id, without the "-1" the portal adds when one
                # booking arrives as two rows. "-" is no booking at all.
                Named(
                    key="booking_id",
                    formula=(
                        "IF(ISBLANK(booking_id), BLANK(), COALESCE(REGEX(UPPER(booking_id), "
                        '"^([A-Za-z]\\d+)-\\d+$", 1), UPPER(booking_id)))'
                    ),
                ),
                Named(key="vertical", formula=_vertical("vertical")),
                Named(
                    key="status",
                    formula=_enum(
                        "status", ("Processed", "Cancelled", "Expired", "Pending")
                    ),
                ),
                Named(
                    key="payment_status",
                    formula=_enum(
                        "payment_status",
                        ("Paid", "Completed", "Pending", "Failed", "Refunded"),
                    ),
                ),
                Named(
                    key="total_amount",
                    formula="IF(ISBLANK(total_amount), order_total, total_amount)",
                ),
                Named(key="locality", formula='IF(vat > 0, "local", "international")'),
                # How a row with no booking id is named in an exception.
                Named(
                    key="reference",
                    formula=(
                        'COALESCE(booking_id, vertical & " order " & '
                        f"{_shown('sg_order_id')})"
                    ),
                ),
            ],
            # Rows of another vertical (the same export carries Food), and rows
            # with no date, are not travel rows.
            filter="AND(NOT(ISBLANK(vertical)), NOT(ISBLANK(order_date)))",
        )
    ],
)

VENDOR = InputFile(
    role="vendor",
    title="Vendor claim",
    caption="The weekly claim workbook",
    required=True,
    sheets=[
        InputSheet(
            key="claim",
            name="vendor claim report",
            # The intersection of all four sample claim files: one adds five
            # hotel columns, one drops Supply Date, one drops Entitlement. The
            # booking id is left out so a claim heading it "Booking Number" is
            # still found.
            fingerprint=[
                "Invoice Number",
                "line Item",
                "Billing Type",
                "Service Type",
                "Total Price W.O VAT",
                "VAT %",
                "Airline Can Fee",
                "Total Invoice Amount",
                "commision amount",
                "net payable amount",
            ],
            # The client's analysis tab satisfies the fingerprint too.
            exclude=["SG Order ID", "Order Total", "Match ID's"],
            set="claim",
            columns=[
                _col(
                    "booking_ref",
                    "Booking ID",
                    "Booking Number",
                    "Booking No",
                    required=True,
                ),
                _col("customer_id", "Customer ID"),
                _col("dom_int_label", "DOM/INT"),
                _col("customer_name", "Customer Name"),
                _col("invoice_no", "Invoice Number", required=True),
                _col("line_item", "line Item"),
                _col("billing_type", "Billing Type"),
                _col("issue_date", "Issue Date", type="date"),
                _col("service_type", "Service Type"),
                _col("service_provider", "Service Provider"),
                _col("description", "Description of Service"),
                _col("supplier", "Supplier Name"),
                _col("pnr", "PNR No"),
                _col("vendor_booking_date", "Booking Date", type="date"),
                # Kept as text: the claim writes MM/DD/YYYY.
                _col("check_in", "Check In Date"),
                _col("check_out", "Check Out Date"),
                _col("to_number", "T/O Number"),
                _col("client_name", "Client Name"),
                _col("route", "Route"),
                _col("destination_city", "Destination City"),
                _col("class_type", "Class/Type"),
                _col("base_ex_vat", "Total Price W.O VAT", type="money", required=True),
                _col("vat_percent", "VAT %", type="money", required=True),
                _col("vat", "VAT", type="money"),
                _col("airline_cancel_fee", "Airline Can Fee", type="money"),
                _col(
                    "total_invoice", "Total Invoice Amount", type="money", required=True
                ),
                _col(
                    "commission", "commision amount", "commission amount", type="money"
                ),
                _col("net_payable", "net payable amount", type="money"),
                _col("currency", "Currency"),
            ],
            fields=[
                Named(key="booking_ref", formula="UPPER(booking_ref)"),
                # A99_7889547 and its credit A99_257964_R0 are booking A99.
                Named(
                    key="booking_id",
                    formula=(
                        'IF(ISBLANK(booking_ref), BLANK(), TEXTBEFORE(booking_ref & "_", "_"))'
                    ),
                ),
                Named(
                    key="reversal",
                    formula='NOT(ISBLANK(REGEX(booking_ref, "_R\\d+$")))',
                ),
                # Three shapes share the sheet: invoice lines, the free-text
                # adjustment rows the vendor uses to correct an earlier claim,
                # and totals rows. An invoiced row with no booking id is reported.
                Named(
                    key="kind",
                    formula=(
                        'IFS(NOT(ISBLANK(booking_ref)), "line", '
                        "AND(NOT(ISBLANK(customer_id)), "
                        'ISBLANK(REGEX(customer_id, "\\A\\d+\\Z"))), "adjustment", '
                        'NOT(ISBLANK(invoice_no)), "skipped", TRUE, "spacer")'
                    ),
                ),
                Named(
                    key="adjusts",
                    formula=(
                        'IF(kind = "adjustment", '
                        'REGEX(UPPER(customer_id), "[AH]\\d{12,18}"), BLANK())'
                    ),
                ),
                Named(
                    key="billing_type",
                    formula=(
                        'IFS(billing_type = "Invoice", "Invoice", '
                        'billing_type = "Credit for Returns", "Credit for Returns", '
                        'CONTAINS(billing_type, "credit"), "Credit for Returns", '
                        'TRUE, "unknown")'
                    ),
                ),
                Named(
                    key="credit",
                    formula='OR(billing_type = "Credit for Returns", reversal)',
                ),
                Named(
                    key="line_vertical",
                    formula=(
                        f"COALESCE({_ID_VERTICAL.format(id='booking_id')}, "
                        f'{_vertical("service_type")}, "flight")'
                    ),
                ),
                # VAT decides, never the claim's own DOM/INT label.
                Named(
                    key="line_locality",
                    formula='IF(OR(vat <> 0, vat_percent <> 0), "local", "international")',
                ),
                # What the vendor may bill on the line: our rate, plus VAT
                # when the line is international.
                Named(
                    key="line_rate",
                    formula=(
                        'IF(line_locality = "international", '
                        'IF(line_vertical = "hotel", param.hotel_rate, param.flight_rate)'
                        " * (1 + param.vat_rate), "
                        'IF(line_vertical = "hotel", param.hotel_rate, param.flight_rate))'
                    ),
                ),
                Named(key="expected", formula="total_invoice * line_rate"),
                Named(
                    key="change_fee",
                    formula='IF(CONTAINS(description, "change fee"), total_invoice, 0)',
                ),
                Named(key="id_date", formula=_ID_DATE.format(id="booking_id")),
                # The claim writes VAT % as a whole number: 15, not 15.00.
                Named(
                    key="vat_percent_text",
                    formula=(
                        "IF(vat_percent = ROUND(vat_percent, 0), "
                        "TEXT(ROUND(vat_percent, 0)), TEXT(vat_percent))"
                    ),
                ),
            ],
            # A claim uploaded twice must not double what we owe: a line is the
            # same line when its invoice identity and its amounts agree.
            unique=Unique(
                when='kind = "line"',
                keys=[
                    "TEXT(invoice_no)",
                    "TEXT(line_item)",
                    "booking_ref",
                    "TEXT(MONEY(total_invoice))",
                    "TEXT(MONEY(commission))",
                ],
                exception=Raise(
                    code="DUPLICATE_VENDOR_LINE",
                    order_no="booking_id",
                    amount="total_invoice",
                    detail=(
                        f'"invoice " & {_shown("invoice_no")} & " line " & '
                        f'{_shown("line_item")} & " appears twice in the upload"'
                    ),
                ),
            ),
            counted='kind = "line"',
        ),
        InputSheet(
            key="claim_periods",
            name="claim period ledger",
            fingerprint=["Claim period", "total Payable amount", "DIFF in payment"],
            set="claim_periods",
            optional=True,
            first_only=True,
            # Its own role, so the sheets read cannot say the claim was found twice.
            manifest_role="vendor-ledger",
            columns=[
                # The block ends with an unlabelled totals row.
                _col("claim_period", "Claim period", required=True, skip=True),
                _col("total_claim", "Total Claim", "total Claim", type="decimal"),
                _col(
                    "commission_amount",
                    "Commision Amount",
                    "Commision invoices Amount",
                    "Commission Amount",
                    type="decimal",
                ),
                _col(
                    "total_payable",
                    "total Payable amount",
                    type="decimal",
                    required=True,
                ),
                _col("paid_amount", "Paid Amount", type="decimal"),
                _col("difference", "DIFF in payment", type="decimal"),
                _col(
                    "unprovided_commission",
                    "unprovided Commision invoices",
                    "unprovided Commission invoices",
                    type="decimal",
                ),
            ],
        ),
    ],
)

_BOTH = "AND(NOT(ISBLANK(portal)), n_lines > 0)"
_RATE = 'IF(vertical = "hotel", param.hotel_rate, param.flight_rate)'


def _placed(start: str, end: str, name: str, outside: str = "") -> str:
    """Where the booking date falls against a span of dates, as a trailing clause."""
    span = f'"{name} (" & {start} & " to " & {end} & ")"'
    return (
        'IF(ISBLANK(booking_date), "; booking date unknown", '
        '"; booked " & booking_date & IFS('
        f'booking_date < {start}, ", before " & {span} & "{outside}", '
        f'booking_date > {end}, ", after " & {span} & "{outside}", '
        f'TRUE, ", inside " & {span}))'
    )


def _pct(rate: str) -> str:
    return f'COALESCE(PERCENT({rate}, 4), "—")'


_SHOWN_STATUS = 'COALESCE(portal_status, "unknown")'
_ON_CLAIM = (
    "OR(NOT(has_claim), AND(NOT(ISBLANK(booking_date)), "
    "booking_date >= claim_start, booking_date <= claim_end))"
)

MODEL = Model(
    builder="group",
    members=[
        Member(set="lines", key="booking_id", name="lines"),
        # The earliest row is the booking; later ones are repeats, never added
        # to the totals: the vendor bills the booking once.
        Member(
            set="booked",
            key="booking_id",
            name="portal",
            primary=["order_date", "N(INTEGER(sg_order_id))", "row"],
            repeats="repeats",
        ),
        Member(set="adjustments", key="adjusts", name="adjustments"),
    ],
    context=[
        # The portal export's span.
        Named(key="export_start", formula='MINX("portal", order_date)'),
        Named(key="export_end", formula='MAXX("portal", order_date)'),
        # The week this claim covers, from the booking ids it bills. Credit
        # notes reverse bookings of any age, so they are left out.
        Named(key="claim_start", formula='MINX("billed_lines", id_date)'),
        Named(key="claim_end", formula='MAXX("billed_lines", id_date)'),
        Named(key="has_claim", formula="NOT(ISBLANK(claim_start))"),
    ],
    stops=[
        Stop(
            when="OR(ISBLANK(export_start), ISBLANK(export_end))",
            message='"The portal export carried no dated travel rows"',
        ),
    ],
    row_rules=[
        RowRule(
            over="skipped",
            code="UNPARSEABLE_VENDOR_ROW",
            detail=(
                'QUOTE(sheet) & " row " & row & ": invoiced row carries no booking id"'
                ' & " (" & invoice_no & ")"'
            ),
        ),
        RowRule(
            over="portal",
            when="ABS(wallet_amount + card_amount - order_total) > param.base_tolerance",
            code="PAYMENT_SPLIT_MISMATCH",
            order_no="reference",
            amount="MONEY(order_total - (wallet_amount + card_amount))",
            detail=(
                'COALESCE(payment_method, "no method") & ": wallet " & MONEY(wallet_amount)'
                ' & " + card " & MONEY(card_amount) & " does not make the order total "'
                " & MONEY(order_total)"
            ),
        ),
        # The portal computes a notional commission on every row, so the status
        # alone says nothing. It is only a finding where money actually moved.
        RowRule(
            over="portal",
            when=(
                'AND(IN(status, "Expired", "Pending"), '
                'IN(payment_status, "Paid", "Completed", "Refunded"), '
                "commission_charged > param.commission_tolerance)"
            ),
            code="COMMISSION_ON_UNCOMPLETED_ORDER",
            order_no="reference",
            amount="MONEY(commission_charged)",
            detail=(
                '"paid for (" & payment_status & ") but " & status'
                ' & ", and commission was still taken"'
            ),
        ),
        RowRule(
            over="adjustments",
            when="ISBLANK(adjusts)",
            code="ADJUSTMENT_UNLINKED",
            amount="commission",
            detail='"no booking id in " & QUOTE(customer_id)',
        ),
        RowRule(
            over="adjustments",
            when="NOT(ISBLANK(adjusts))",
            code="ADJUSTMENT_APPLIED",
            order_no="adjusts",
            amount="commission",
            detail="customer_id",
        ),
    ],
    fields=[
        # Both sides, added up unrounded.
        Named(key="n_lines", formula='COUNTX("lines")'),
        Named(key="n_repeats", formula='COUNTX("repeats")'),
        Named(key="v_base", formula='SUMX("lines", total_invoice)'),
        Named(key="v_commission", formula='SUMX("lines", commission)'),
        Named(key="v_expected", formula='SUMX("lines", expected)'),
        Named(key="v_net", formula='SUMX("lines", net_payable)'),
        Named(key="v_cancel", formula='SUMX("lines", airline_cancel_fee)'),
        Named(key="v_change", formula='SUMX("lines", change_fee)'),
        Named(
            key="local_base",
            formula='SUMX("lines", IF(line_locality = "local", total_invoice, 0))',
        ),
        Named(
            key="intl_base",
            formula='SUMX("lines", IF(line_locality = "international", total_invoice, 0))',
        ),
        Named(key="has_invoice", formula='COUNTX("lines", NOT(credit)) > 0'),
        Named(key="has_credit", formula='COUNTX("lines", credit) > 0'),
        Named(key="first_issue", formula='MINX("lines", issue_date)'),
        Named(key="adjustment", formula='SUMX("adjustments", commission)'),
        Named(key="id_vertical", formula=_ID_VERTICAL.format(id="key")),
        Named(
            key="vertical",
            formula=(
                'COALESCE(portal.vertical, id_vertical, FIRSTX("lines", line_vertical), '
                '"flight")'
            ),
        ),
        # The locality that governs most of the booking's value.
        Named(key="n_localities", formula='DISTINCTX("lines", line_locality)'),
        Named(
            key="locality",
            formula=(
                'IF(n_lines = 0, IF(ISBLANK(portal), "international", portal.locality), '
                'IF(n_localities = 1, FIRSTX("lines", line_locality), '
                'IF(ABS(local_base) >= ABS(intl_base), "local", "international")))'
            ),
        ),
        Named(
            key="match_state",
            formula=(
                f'IFS({_BOTH}, "matched", NOT(ISBLANK(portal)), "portal_only", '
                'TRUE, "vendor_only")'
            ),
        ),
        # Decided on the vendor's side: a retained fee makes a refund partial,
        # and so does an invoice the credit has not cancelled out.
        Named(
            key="refund",
            formula=(
                'IFS(NOT(has_credit), "none", '
                'v_cancel + v_change > param.base_tolerance, "partial", '
                'AND(has_invoice, v_base > param.base_tolerance), "partial", '
                'TRUE, "full")'
            ),
        ),
        # From the id; the claim's Booking Date holds the date of a credit.
        Named(
            key="booking_date",
            formula=(
                f"COALESCE({_ID_DATE.format(id='key')}, "
                "IF(ISBLANK(portal), first_issue, portal.order_date))"
            ),
        ),
        Named(key="order_date", formula="portal.order_date"),
        Named(key="portal_rows", formula="IF(ISBLANK(portal), 0, 1) + n_repeats"),
        Named(key="portal_status", formula="portal.status"),
        Named(key="portal_payment_status", formula="portal.payment_status"),
        Named(key="payment_method", formula="portal.payment_method"),
        Named(key="p_base", formula="N(portal.order_total)"),
        Named(key="p_commission", formula="N(portal.commission_charged)"),
        Named(key="p_net", formula="N(portal.net_payable)"),
        Named(key="base_rate", formula=_RATE),
        Named(
            key="contract_rate",
            formula='IF(locality = "international", base_rate * (1 + param.vat_rate), base_rate)',
        ),
        Named(key="sg_expected", formula="p_base * base_rate"),
        Named(key="as_vendor", formula="p_base * contract_rate"),
        # Every reported figure is quantized once, here.
        Named(key="portal_base", formula="MONEY(p_base)"),
        Named(key="portal_commission", formula="MONEY(p_commission)"),
        Named(key="portal_net_payable", formula="MONEY(p_net)"),
        Named(key="wallet_amount", formula="MONEY(N(portal.wallet_amount))"),
        Named(key="card_amount", formula="MONEY(N(portal.card_amount))"),
        Named(key="vendor_base", formula="MONEY(v_base)"),
        Named(key="vendor_commission", formula="MONEY(v_commission)"),
        Named(key="vendor_net_payable", formula="MONEY(v_net)"),
        Named(key="vendor_cancel_fee", formula="MONEY(v_cancel)"),
        Named(key="vendor_change_fee", formula="MONEY(v_change)"),
        Named(key="vendor_adjustment", formula="MONEY(adjustment)"),
        # Rates are ratios, not money: never quantized.
        Named(key="sg_rate", formula="IF(p_base = 0, BLANK(), p_commission / p_base)"),
        Named(
            key="vendor_rate", formula="IF(v_base = 0, BLANK(), v_commission / v_base)"
        ),
        Named(key="sg_expected_commission", formula="MONEY(sg_expected)"),
        Named(key="vendor_expected_commission", formula="MONEY(v_expected)"),
        Named(key="base_diff", formula="MONEY(p_base - v_base)"),
        Named(key="commission_diff", formula="MONEY(p_commission - v_commission)"),
        Named(key="net_payable_diff", formula="MONEY(p_net - v_net)"),
        # What we settle on: our base at the vendor's contract rate.
        Named(key="commission_as_vendor", formula="MONEY(as_vendor)"),
        Named(key="net_payable_as_vendor", formula="MONEY(p_base - as_vendor)"),
        # Only where both sides exist: against a claim that never billed the
        # booking, the difference is just the whole commission again.
        Named(
            key="commission_diff_as_vendor",
            formula="MONEY(IF(n_lines > 0, as_vendor - v_commission, 0))",
        ),
        Named(
            key="net_payable_diff_as_vendor",
            formula="MONEY(IF(n_lines > 0, (p_base - as_vendor) - v_net, 0))",
        ),
        # The commission we never collected from the customer.
        Named(key="vat_uplift_shortfall", formula="MONEY(as_vendor - sg_expected)"),
        Named(key="both", formula=_BOTH),
    ],
    # Why a booking is out of the matched output, taken in order of what
    # decides it: a refund or a cancellation leaves the two sides apart on
    # purpose, so it explains a booking before a missing side does.
    rules=[
        StatusRule(
            when='refund <> "none"',
            verdict="refunded",
            note=(
                'refund & " refund credited on the claim" & '
                'IF(vendor_cancel_fee + vendor_change_fee <> 0, "; " & '
                '(vendor_cancel_fee + vendor_change_fee) & " in fees retained", "") & '
                'IF(vendor_commission <> 0, "; vendor commission " & vendor_commission'
                ' & " still billed", "") & '
                f'IF(portal_rows > 0, "; the portal shows " & {_SHOWN_STATUS}, '
                f'"; no portal row" & {_placed("export_start", "export_end", "the portal export")})'
            ),
        ),
        StatusRule(
            when='portal_status = "Cancelled"',
            verdict="cancelled",
            note=(
                'IF(match_state = "matched", '
                '"cancelled on the portal, but the claim bills it with no credit note", '
                '"cancelled on the portal; not on the claim")'
            ),
        ),
        StatusRule(
            when='AND(match_state = "portal_only", portal_status <> "Processed")',
            verdict="other",
            note=f'{_SHOWN_STATUS} & " on the portal; not on the claim"',
        ),
        StatusRule(
            when='AND(match_state = "portal_only", NOT(has_claim))',
            verdict="missing_in_vendor",
            note='"not billed on the claim"',
        ),
        StatusRule(
            when='match_state = "portal_only"',
            verdict="missing_in_vendor",
            note=(
                '"not billed on the claim" & '
                + _placed(
                    "claim_start",
                    "claim_end",
                    "this claim's week",
                    outside=", so expected on another claim",
                )
            ),
        ),
        StatusRule(
            when='match_state = "vendor_only"',
            verdict="missing_in_portal",
            note=(
                'IF(n_lines > 0, "billed on the claim", '
                '"adjusted by " & vendor_adjustment & " on the claim") & '
                '", no portal row" & '
                + _placed("export_start", "export_end", "the portal export")
            ),
        ),
        StatusRule(
            when='portal_status <> "Processed"',
            verdict="other",
            note=f'"billed on the claim while the portal shows " & {_SHOWN_STATUS}',
        ),
        StatusRule(when="TRUE", verdict="matched", note='""'),
    ],
    findings=[
        # A booking this claim does not bill, placed against the claim's week:
        # outside it, it is billed on another claim, which is expected.
        *(
            Finding(
                code=code,
                when=f'AND(match_state = "portal_only", has_claim, {test})',
                amount="portal_base",
                detail=(
                    'COALESCE(portal_status, "booked") & " on the portal, not billed on '
                    'this claim (booked " & booking_date & ", outside its week)"'
                ),
            )
            for code, test in (
                ("PRIOR_PERIOD", "booking_date < claim_start"),
                ("LATER_PERIOD", "booking_date > claim_end"),
            )
        ),
        Finding(
            code="BOOKING_MISSING_IN_VENDOR",
            when=(
                'AND(match_state = "portal_only", NOT(AND(has_claim, '
                "OR(booking_date < claim_start, booking_date > claim_end))))"
            ),
            amount="portal_base",
            detail=(
                'COALESCE(portal_status, "booked") & " on the portal, not billed on this claim"'
            ),
        ),
        # A billed booking with no portal row: a credit note for a booking
        # older than the export is the common case and is not a break.
        *(
            Finding(
                code=code,
                when=f'AND(match_state = "vendor_only", {test})',
                amount="vendor_base",
                detail=(
                    '"billed on the claim, no portal row (booked " & '
                    'COALESCE(booking_date, "unknown") & ")"'
                ),
            )
            for code, test in (
                ("PRIOR_PERIOD", "booking_date < export_start"),
                ("LATER_PERIOD", "booking_date > export_end"),
                (
                    "BOOKING_MISSING_IN_PORTAL",
                    "NOT(OR(booking_date < export_start, booking_date > export_end))",
                ),
            )
        ),
        Finding(
            code="BOOKING_BASE_MISMATCH",
            when=(
                'AND(match_state = "matched", refund = "none", '
                "ABS(base_diff) > param.base_tolerance)"
            ),
            amount="base_diff",
            detail='"we booked " & portal_base & ", they billed " & vendor_base',
        ),
        Finding(
            code="PARTIAL_REFUND",
            when='refund = "partial"',
            amount="MONEY(v_cancel + v_change)",
            detail=(
                '"refunded with " & MONEY(v_cancel) & " cancellation fee and " & '
                'MONEY(v_change) & " change fee retained"'
            ),
        ),
        Finding(
            code="REFUND_WITHOUT_PORTAL_CANCELLATION",
            when='AND(refund <> "none", portal_status = "Processed")',
            amount="vendor_base",
            detail='"credited on the claim while the portal still says Processed"',
        ),
        # The vendor's own netting: only checkable when both sides of the
        # reversal are on this claim.
        Finding(
            code="COMMISSION_NOT_REVERSED_ON_REFUND",
            when=(
                'AND(refund <> "none", has_invoice, has_credit, '
                "ABS(v_commission) > param.commission_tolerance)"
            ),
            amount="vendor_commission",
            detail='"the credit note leaves the vendor\'s commission standing"',
        ),
        Finding(
            code="COMMISSION_NOT_REVERSED_ON_REFUND",
            when='AND(refund <> "none", portal_commission > param.commission_tolerance)',
            amount="portal_commission",
            detail='"the portal still retains its commission on a refunded booking"',
        ),
        Finding(
            code="CANCELLATION_WITHOUT_VENDOR_CREDIT",
            when=f'AND(refund = "none", portal_status = "Cancelled", {_ON_CLAIM})',
            amount="portal_base",
            detail='"cancelled on the portal with no credit note on the claim"',
        ),
        Finding(
            code="VENDOR_COMMISSION_OVERCHARGE",
            when=(
                'AND(refund = "none", n_lines > 0, '
                "v_commission - v_expected > param.commission_tolerance)"
            ),
            amount="MONEY(v_commission - v_expected)",
            detail=(
                '"billed " & vendor_commission & " where " & vendor_expected_commission'
                f' & " is contractual (" & {_pct("vendor_rate")} & " against " & '
                f'{_pct("contract_rate")} & ")"'
            ),
        ),
        Finding(
            code="VENDOR_COMMISSION_UNDERCHARGE",
            when=(
                'AND(refund = "none", n_lines > 0, '
                "v_commission - v_expected < -param.commission_tolerance)"
            ),
            amount="MONEY(v_commission - v_expected)",
            detail=(
                '"billed " & vendor_commission & " where " & vendor_expected_commission'
                ' & " is contractual"'
            ),
        ),
        Finding(
            code="SG_COMMISSION_RATE_UNEXPECTED",
            when=(
                'AND(refund = "none", portal_base <> 0, '
                "ABS(portal_commission - sg_expected_commission) > param.commission_tolerance)"
            ),
            amount="MONEY(portal_commission - sg_expected_commission)",
            detail=(
                '"we charged " & portal_commission & " where our own " & '
                f'{_pct("base_rate")} & " rate makes " & sg_expected_commission'
            ),
        ),
        Finding(
            code="MULTI_LEG_BOOKING",
            when="n_lines > 1",
            amount="vendor_base",
            detail='n_lines & " invoice lines compared as one booking"',
        ),
        Finding(
            code="REPEAT_BOOKING_ROW",
            each="repeats",
            when="TRUE",
            amount="MONEY(order_total)",
            detail=(
                '"portal row " & row & " of " & QUOTE(sheet) & '
                '" repeats this booking; excluded from the totals"'
            ),
        ),
        # The booking id, the portal and the claim lines each name a vertical.
        Finding(
            code="VERTICAL_CONFLICT",
            when=(
                'AND(OR(COUNTX("lines", line_vertical = "hotel") > 0, '
                'portal.vertical = "hotel", id_vertical = "hotel"), '
                'OR(COUNTX("lines", line_vertical = "flight") > 0, '
                'portal.vertical = "flight", id_vertical = "flight"))'
            ),
            detail='"the booking id, the portal and the claim disagree: flight, hotel"',
        ),
        Finding(
            code="VAT_BASIS_CONFLICT",
            when=f"AND({_BOTH}, n_localities > 1)",
            detail='"the claim\'s own lines disagree about whether VAT applies"',
        ),
        Finding(
            code="VAT_BASIS_CONFLICT",
            when=f"AND({_BOTH}, n_localities <= 1, portal.locality <> locality)",
            detail='"the portal reads " & portal.locality & " and the claim " & locality',
        ),
    ],
    # The VAT uplift, raised once for the run: one finding about pricing, not
    # a list of broken bookings.
    run_rules=[
        RunRule(
            code="SG_COMMISSION_SHORT_OF_VENDOR_RATE",
            when='COUNTX("short") > 0',
            amount='MONEY(SUMX("short", vat_uplift_shortfall))',
            detail=(
                'COUNTX("short") & " international booking(s) were charged our local '
                "rate while the vendor bills that rate plus VAT; the difference is "
                'commission we never collected from the customer"'
            ),
        ),
    ],
    sort=["booking_date", "key"],
    wallet=WalletCheck(
        rows="booked",
        excluded="unbooked",
        vertical="vertical",
        wallet_types={
            "hotel": "hotel",
            "hotels": "hotel",
            "h": "hotel",
            "flight": "flight",
            "flights": "flight",
            "a": "flight",
        },
        sg_order_id="sg_order_id",
        reference="booking_id",
        order_date="order_date",
        amount="wallet_amount",
        tolerance="param.commission_tolerance",
    ),
    sets=[
        SetDef(key="lines", over="claim", where='kind = "line"'),
        SetDef(key="adjustments", over="claim", where='kind = "adjustment"'),
        SetDef(key="skipped", over="claim", where='kind = "skipped"'),
        SetDef(
            key="billed_lines",
            over="lines",
            where="AND(NOT(credit), NOT(ISBLANK(id_date)))",
        ),
        SetDef(key="booked", over="portal", where="NOT(ISBLANK(booking_id))"),
        SetDef(key="unbooked", over="portal", where="ISBLANK(booking_id)"),
        SetDef(key="matched", over="entities", where='verdict = "matched"'),
        SetDef(key="unmatched", over="entities", where='verdict <> "matched"'),
        SetDef(
            key="short",
            over="matched",
            where='AND(locality = "international", vat_uplift_shortfall > 0)',
        ),
    ],
    metrics=[
        # The run's summary. Money is over the matched bookings; the claim_*
        # figures are the whole claim, to tie out against the vendor's ledger.
        Named(key="portal_rows", formula='COUNTX("portal")'),
        Named(key="portal_bookings", formula='COUNTX("entities", portal_rows > 0)'),
        Named(key="vendor_lines", formula='COUNTX("lines")'),
        Named(key="vendor_bookings", formula='COUNTX("entities", n_lines > 0)'),
        Named(key="bookings_matched", formula='COUNTX("matched")'),
        Named(key="bookings_unmatched", formula='COUNTX("unmatched")'),
        Named(
            key="bookings_portal_only",
            formula='COUNTX("entities", match_state = "portal_only")',
        ),
        Named(
            key="bookings_vendor_only",
            formula='COUNTX("entities", match_state = "vendor_only")',
        ),
        Named(key="repeat_rows", formula='SUMX("entities", n_repeats)'),
        Named(key="portal_rows_without_booking_id", formula='COUNTX("unbooked")'),
        Named(key="vendor_rows_without_booking_id", formula='COUNTX("skipped")'),
        Named(key="claim_base", formula='MONEY(SUMX("entities", v_base))'),
        Named(key="claim_commission", formula='MONEY(SUMX("entities", v_commission))'),
        Named(key="claim_net_payable", formula='MONEY(SUMX("entities", v_net))'),
        Named(key="portal_base", formula='MONEY(SUMX("matched", p_base))'),
        Named(key="vendor_base", formula='MONEY(SUMX("matched", v_base))'),
        Named(key="base_difference", formula='MONEY(SUMX("matched", base_diff))'),
        Named(key="portal_commission", formula='MONEY(SUMX("matched", p_commission))'),
        Named(key="vendor_commission", formula='MONEY(SUMX("matched", v_commission))'),
        Named(
            key="commission_difference",
            formula='MONEY(SUMX("matched", commission_diff))',
        ),
        Named(key="portal_net_payable", formula='MONEY(SUMX("matched", p_net))'),
        Named(key="vendor_net_payable", formula='MONEY(SUMX("matched", v_net))'),
        Named(
            key="net_payable_difference",
            formula='MONEY(SUMX("matched", net_payable_diff))',
        ),
        Named(
            key="commission_as_vendor",
            formula='MONEY(SUMX("matched", commission_as_vendor))',
        ),
        Named(
            key="net_payable_as_vendor",
            formula='MONEY(SUMX("matched", net_payable_as_vendor))',
        ),
        # Both sides at the contract rate, from the unrounded order totals.
        Named(
            key="commission_difference_at_contract",
            formula='MONEY(SUMX("matched", as_vendor - v_commission))',
        ),
        Named(
            key="net_payable_difference_at_contract",
            formula='MONEY(SUMX("matched", (p_base - as_vendor) - v_net))',
        ),
        Named(
            key="vat_uplift_shortfall",
            formula='MONEY(SUMX("matched", vat_uplift_shortfall))',
        ),
        Named(
            key="vendor_overcharge",
            formula=(
                'MONEY(SUMX("entities", IF(AND(refund = "none", '
                "v_commission - v_expected > 0), v_commission - v_expected, 0)))"
            ),
        ),
        Named(key="vendor_adjustments", formula='MONEY(SUMX("entities", adjustment))'),
        Named(
            key="retained_fees", formula='MONEY(SUMX("entities", v_cancel + v_change))'
        ),
        # What the vendor keeps after reversing a booking it has also credited.
        Named(
            key="commission_retained_on_refunds",
            formula=(
                'MONEY(SUMX("entities", IF(AND(refund <> "none", has_invoice), '
                "v_commission, 0)))"
            ),
        ),
        *(
            Named(
                key=f"{vertical}_total",
                formula=(
                    f'SUMX("matched", IF(vertical = "{vertical}", '
                    "commission_as_vendor, 0))"
                ),
            )
            for vertical in ("hotel", "flight")
        ),
        Named(key="hotel_commission", formula="MONEY(hotel_total)"),
        Named(key="flight_commission", formula="MONEY(flight_total)"),
        Named(
            key="hotel_commission_ex_vat",
            formula="MONEY(hotel_total / (1 + param.vat_rate))",
        ),
        Named(
            key="flight_commission_ex_vat",
            formula="MONEY(flight_total / (1 + param.vat_rate))",
        ),
        Named(key="wallet_rows_checked", formula="N(wallet_check.rows_checked)"),
        Named(key="wallet_matched", formula="N(wallet_check.matched)"),
        Named(key="vendor_name", formula='COALESCEX("portal", provider)'),
        # The client dashboard, over the matched bookings as the totals are.
        Named(key="bookings", formula='COUNTX("matched")'),
        Named(key="round_off", formula="vendor_commission - portal_commission"),
        Named(key="net_payable", formula="net_payable_as_vendor"),
        Named(key="wallet", formula='MONEY(SUMX("matched", wallet_amount))'),
        Named(key="card", formula='MONEY(SUMX("matched", card_amount))'),
        Named(
            key="portal_total", formula='MONEY(SUMX("matched", portal.total_amount))'
        ),
        Named(key="promo", formula='MONEY(SUMX("matched", portal.discount))'),
        Named(key="collection", formula="wallet + card"),
        Named(key="collection_gap", formula="collection - portal_total"),
        Named(key="remaining", formula="promo + collection - portal_total"),
        Named(key="order_collection", formula="portal_total - collection"),
        Named(key="net_difference", formula="portal_total - collection - promo"),
        Named(key="commission", formula="hotel_commission + flight_commission"),
        Named(
            key="commission_ex_vat",
            formula="hotel_commission_ex_vat + flight_commission_ex_vat",
        ),
        Named(
            key="average_commission",
            formula="IF(bookings = 0, 0, commission / bookings)",
        ),
        *(
            Named(
                key=f"{vertical}_share",
                formula=f"IF(commission = 0, 0, {vertical}_commission / commission)",
            )
            for vertical in ("hotel", "flight")
        ),
    ],
    history=History(
        counted="bookings_matched",
        total='COUNTX("entities")',
        unit='"bookings"',
        net_payable="net_payable_as_vendor",
        vendor="vendor_name",
        period_start="export_start",
        period_end="export_end",
        report_name='"reconciliation-travel-" & export_start & "-to-" & export_end & ".xlsx"',
    ),
)
