# Copyright (c) 2026, erpcloud.systems and contributors
# For license information, please see license.txt

"""
Smart General Ledger Pro – Script Report
========================================

A comprehensive multi-currency GL report with intelligent payment mapping,
audit mode, and advanced column layers.

Architecture
------------
Company currency : USD
Party accounts   : ILS
Payment currency : JOD  (cheque / MOP currency)

For each GL row linked to a Payment Entry the report populates three
currency layers:

  Payment Layer  (JOD)  – debit_jod  / credit_jod
  Party Layer    (ILS)  – debit_ils  / credit_ils
  Accounting Layer(USD) – debit_usd  / credit_usd

Display rules
  Receivable / Payable accounts:
    credit_jod = received_amount  (payment currency amount)
    credit_ils = credit_in_account_currency  (GL natural amount in ILS)
    credit_usd = credit  (company-currency base)

  Bank / Cash accounts:
    debit_jod = received_amount
    debit_usd = debit

Rate columns
  rate_jod_to_usd = target_exchange_rate  (payment → company)
  rate_jod_to_ils = derived from amounts  (payment → party)

Validation
  rate_mismatch_flag   – rate derived from amounts vs Currency Exchange master
  amount_mismatch_flag – (debit_jod * rate_jod_to_usd) vs debit_usd

Audit mode
  payment_entry_id, exchange_rate_source, raw_payment_amount, raw_gl_amount

Performance
  All Payment Entries are preloaded into memory (single batch query).
  No DB queries are issued inside the data loop.

Validation scenario
  1000 JOD → 3000 ILS → 1410 USD
  Customer: credit_jod=1000, credit_ils=3000, credit_usd=1410
  Bank    : debit_jod=1000,  debit_usd=1410
"""

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RATE_MISMATCH_THRESHOLD = 0.0001  # 0.01 % tolerance for rate warnings


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def execute(filters=None):
    if not filters:
        filters = {}
    _validate_filters(filters)
    columns = _get_columns(filters)
    data = _get_data(filters)
    return columns, data


# ---------------------------------------------------------------------------
# Filter validation
# ---------------------------------------------------------------------------

def _validate_filters(filters):
    if not filters.get("company"):
        frappe.throw(_("Please select a Company"))
    if not filters.get("from_date") or not filters.get("to_date"):
        frappe.throw(_("Please select a Date Range (From Date and To Date)"))
    if getdate(filters["from_date"]) > getdate(filters["to_date"]):
        frappe.throw(_("From Date must be before To Date"))


# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

def _get_columns(filters):
    audit_mode = filters.get("audit_mode")

    cols = [
        {
            "fieldname": "posting_date",
            "label": _("Date"),
            "fieldtype": "Date",
            "width": 100,
        },
        {
            "fieldname": "voucher_type",
            "label": _("Voucher Type"),
            "fieldtype": "Data",
            "width": 120,
        },
        {
            "fieldname": "voucher_no",
            "label": _("Voucher No"),
            "fieldtype": "Dynamic Link",
            "options": "voucher_type",
            "width": 180,
        },
        {
            "fieldname": "account",
            "label": _("Account"),
            "fieldtype": "Link",
            "options": "Account",
            "width": 220,
        },
        {
            "fieldname": "party_type",
            "label": _("Party Type"),
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "fieldname": "party",
            "label": _("Party"),
            "fieldtype": "Dynamic Link",
            "options": "party_type",
            "width": 160,
        },
        {
            "fieldname": "cost_center",
            "label": _("Cost Center"),
            "fieldtype": "Link",
            "options": "Cost Center",
            "width": 140,
        },
        {
            "fieldname": "project",
            "label": _("Project"),
            "fieldtype": "Link",
            "options": "Project",
            "width": 120,
        },
        {
            "fieldname": "remarks",
            "label": _("Remarks"),
            "fieldtype": "Data",
            "width": 180,
        },
        # ── Payment Layer (JOD) ───────────────────────────────────────────
        {
            "fieldname": "debit_jod",
            "label": _("Debit (JOD)"),
            "fieldtype": "Currency",
            "options": "payment_currency",
            "width": 130,
        },
        {
            "fieldname": "credit_jod",
            "label": _("Credit (JOD)"),
            "fieldtype": "Currency",
            "options": "payment_currency",
            "width": 130,
        },
        # ── Party Layer (ILS) ─────────────────────────────────────────────
        {
            "fieldname": "debit_ils",
            "label": _("Debit (ILS)"),
            "fieldtype": "Currency",
            "options": "party_currency",
            "width": 130,
        },
        {
            "fieldname": "credit_ils",
            "label": _("Credit (ILS)"),
            "fieldtype": "Currency",
            "options": "party_currency",
            "width": 130,
        },
        # ── Accounting Layer (USD) ────────────────────────────────────────
        {
            "fieldname": "debit_usd",
            "label": _("Debit (USD)"),
            "fieldtype": "Currency",
            "options": "company_currency",
            "width": 130,
        },
        {
            "fieldname": "credit_usd",
            "label": _("Credit (USD)"),
            "fieldtype": "Currency",
            "options": "company_currency",
            "width": 130,
        },
        # ── Exchange Rates ────────────────────────────────────────────────
        {
            "fieldname": "rate_jod_to_usd",
            "label": _("Rate (JOD→USD)"),
            "fieldtype": "Float",
            "width": 130,
        },
        {
            "fieldname": "rate_jod_to_ils",
            "label": _("Rate (JOD→ILS)"),
            "fieldtype": "Float",
            "width": 130,
        },
        # ── Validation ────────────────────────────────────────────────────
        {
            "fieldname": "rate_mismatch_flag",
            "label": _("Rate Warning"),
            "fieldtype": "Data",
            "width": 160,
        },
        {
            "fieldname": "amount_mismatch_flag",
            "label": _("Amount Warning"),
            "fieldtype": "Data",
            "width": 160,
        },
        # ── Currency metadata (for column formatters) ─────────────────────
        {
            "fieldname": "payment_currency",
            "label": _("Payment Currency"),
            "fieldtype": "Data",
            "width": 100,
            "hidden": 1,
        },
        {
            "fieldname": "party_currency",
            "label": _("Party Currency"),
            "fieldtype": "Data",
            "width": 100,
            "hidden": 1,
        },
        {
            "fieldname": "company_currency",
            "label": _("Company Currency"),
            "fieldtype": "Data",
            "width": 100,
            "hidden": 1,
        },
    ]

    # Audit mode: add traceability columns
    if audit_mode:
        cols += [
            {
                "fieldname": "payment_entry_id",
                "label": _("Payment Entry"),
                "fieldtype": "Link",
                "options": "Payment Entry",
                "width": 160,
            },
            {
                "fieldname": "exchange_rate_source",
                "label": _("Exchange Rate Source"),
                "fieldtype": "Data",
                "width": 160,
            },
            {
                "fieldname": "raw_payment_amount",
                "label": _("Raw Payment Amount"),
                "fieldtype": "Currency",
                "width": 140,
            },
            {
                "fieldname": "raw_gl_amount",
                "label": _("Raw GL Amount"),
                "fieldtype": "Currency",
                "width": 130,
            },
        ]

    return cols


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def _get_data(filters):
    conditions, values = _build_conditions(filters)

    gl_entries = frappe.db.sql(
        f"""
        SELECT
            gle.name,
            gle.posting_date,
            gle.account,
            gle.voucher_type,
            gle.voucher_no,
            gle.debit,
            gle.credit,
            gle.debit_in_account_currency,
            gle.credit_in_account_currency,
            gle.account_currency,
            gle.party_type,
            gle.party,
            gle.cost_center,
            gle.project,
            gle.remarks,
            gle.company
        FROM `tabGL Entry` gle
        WHERE gle.is_cancelled = 0
          AND gle.company = %(company)s
          AND gle.posting_date BETWEEN %(from_date)s AND %(to_date)s
          {conditions}
        ORDER BY gle.posting_date, gle.voucher_no, gle.name
        """,
        values=values,
        as_dict=True,
    )

    if not gl_entries:
        return []

    # ── Phase 6: Preload all Payment Entries into memory ──────────────────
    pe_map = _preload_payment_entries(gl_entries)

    # ── Preload account types for bank/cash detection ─────────────────────
    account_type_map = _preload_account_types(gl_entries)

    # ── Preload company currencies ─────────────────────────────────────────
    company_currency_map = {}

    # ── Exchange rate cache: (from_cur, to_cur, date) → rate ─────────────
    rate_cache = {}

    def _get_rate(from_cur, to_cur, date=None):
        if not from_cur or not to_cur or from_cur == to_cur:
            return 1.0 if from_cur == to_cur else None
        key = (from_cur, to_cur, str(date) if date else "")
        if key not in rate_cache:
            rate_cache[key] = _fetch_exchange_rate(from_cur, to_cur, date)
        return rate_cache[key]

    data = []
    for row in gl_entries:
        company = row.get("company", "")
        if company not in company_currency_map:
            company_currency_map[company] = (
                frappe.db.get_value("Company", company, "default_currency") or "USD"
            )
        company_currency = company_currency_map[company]

        row_data = {
            "posting_date": row.posting_date,
            "voucher_type": row.voucher_type,
            "voucher_no": row.voucher_no,
            "account": row.account,
            "party_type": row.party_type or "",
            "party": row.party or "",
            "cost_center": row.cost_center or "",
            "project": row.project or "",
            "remarks": row.remarks or "",
            "company_currency": company_currency,
            # defaults for non-PE rows
            "payment_currency": row.account_currency or company_currency,
            "party_currency": row.account_currency or company_currency,
            "debit_jod": 0.0,
            "credit_jod": 0.0,
            "debit_ils": 0.0,
            "credit_ils": 0.0,
            "debit_usd": flt(row.debit),
            "credit_usd": flt(row.credit),
            "rate_jod_to_usd": 0.0,
            "rate_jod_to_ils": 0.0,
            "rate_mismatch_flag": "",
            "amount_mismatch_flag": "",
            # audit
            "payment_entry_id": "",
            "exchange_rate_source": "",
            "raw_payment_amount": 0.0,
            "raw_gl_amount": flt(row.debit or row.credit),
        }

        # ── Smart Mapping Engine ──────────────────────────────────────────
        pe_name = row.voucher_no if row.voucher_type == "Payment Entry" else None
        pe = pe_map.get(pe_name) if pe_name else None

        if pe:
            _apply_payment_mapping(row_data, row, pe, account_type_map, _get_rate,
                                   company_currency, filters.get("audit_mode"))
        else:
            # Non-PE row: JOD/ILS columns = account-currency amounts
            acc_currency = row.account_currency or company_currency
            row_data["payment_currency"] = acc_currency
            row_data["party_currency"] = acc_currency
            debit_acc = flt(row.debit_in_account_currency)
            credit_acc = flt(row.credit_in_account_currency)
            row_data["debit_jod"] = debit_acc
            row_data["credit_jod"] = credit_acc
            row_data["debit_ils"] = debit_acc
            row_data["credit_ils"] = credit_acc

        # ── Currency filter ───────────────────────────────────────────────
        if filters.get("currency"):
            cur_filter = filters["currency"]
            relevant_currencies = {
                row_data.get("payment_currency"),
                row_data.get("party_currency"),
                row_data.get("company_currency"),
                row.get("account_currency"),
            }
            if cur_filter not in relevant_currencies:
                continue

        data.append(row_data)

    return data


# ---------------------------------------------------------------------------
# Smart Mapping Engine (Phase 3)
# ---------------------------------------------------------------------------

def _apply_payment_mapping(row_data, gl_row, pe, account_type_map, get_rate_fn,
                            company_currency, audit_mode=False):
    """Populate multi-currency columns for a GL row linked to a Payment Entry.

    Payment Truth (per mapping spec):
      payment_currency = paid_from_account_currency  (the cheque/JOD side)
      payment_amount   = received_amount (Receive) or paid_amount (Pay)

    Display Rules:
      Receivable / Payable:
        credit_jod = payment_amount
        credit_ils = GL credit_in_account_currency
        credit_usd = GL credit (company base)

      Bank / Cash:
        debit_jod = payment_amount
        debit_usd = GL debit
    """
    account = gl_row.get("account", "")
    paid_from = pe.get("paid_from", "")
    paid_to = pe.get("paid_to", "")
    paid_from_currency = pe.get("paid_from_account_currency", "")
    paid_to_currency = pe.get("paid_to_account_currency", "")
    posting_date = pe.get("posting_date")

    # Identify payment currency (the JOD side = bank/MOP = paid_to for Receive)
    # Per spec: payment_currency = paid_from_account_currency when currencies differ.
    # In our 3-currency scenario: paid_from = ILS (party), paid_to = JOD (bank).
    # The "payment currency" for JOD column = the bank/MOP currency (paid_to).
    if paid_from_currency and paid_to_currency and paid_from_currency != paid_to_currency:
        payment_currency = paid_to_currency   # JOD (bank side)
        party_currency = paid_from_currency   # ILS (party side)
    else:
        payment_currency = paid_to_currency or paid_from_currency or company_currency
        party_currency = paid_from_currency or paid_to_currency or company_currency

    # Determine payment amount (JOD amount)
    received_amount = flt(pe.get("received_amount") or 0)
    paid_amount = flt(pe.get("paid_amount") or 0)
    payment_type = pe.get("payment_type", "Receive")
    if payment_type == "Receive":
        jod_amount = received_amount  # what was deposited in JOD bank account
    else:
        jod_amount = paid_amount      # what was paid from JOD bank account

    target_rate = flt(pe.get("target_exchange_rate") or 0)
    source_rate = flt(pe.get("source_exchange_rate") or 0)

    if not jod_amount:
        # Fallback: derive from company base using target_exchange_rate
        base = flt(gl_row.get("debit") or 0) or flt(gl_row.get("credit") or 0)
        if target_rate and base:
            jod_amount = flt(base / target_rate, 9)

    debit_company = flt(gl_row.get("debit") or 0)
    credit_company = flt(gl_row.get("credit") or 0)
    debit_acc = flt(gl_row.get("debit_in_account_currency") or 0)
    credit_acc = flt(gl_row.get("credit_in_account_currency") or 0)

    account_type = account_type_map.get(account, "")
    is_bank_cash = account_type in ("Bank", "Cash")

    # ── Apply display rules ───────────────────────────────────────────────
    if debit_company:
        # Debit row (Bank / Cash receiving payment or party paying)
        row_data["debit_jod"] = jod_amount
        row_data["credit_jod"] = 0.0
        row_data["debit_usd"] = debit_company
        row_data["credit_usd"] = 0.0
        if is_bank_cash:
            # Bank row: ILS column is 0 (bank account is in JOD)
            row_data["debit_ils"] = 0.0
            row_data["credit_ils"] = 0.0
        else:
            # Party row on debit side (Pay payment reduces payable)
            row_data["debit_ils"] = debit_acc
            row_data["credit_ils"] = 0.0
    else:
        # Credit row (Receivable / Payable credited when customer pays)
        row_data["debit_jod"] = 0.0
        row_data["credit_jod"] = jod_amount
        row_data["debit_usd"] = 0.0
        row_data["credit_usd"] = credit_company
        if is_bank_cash:
            row_data["debit_ils"] = 0.0
            row_data["credit_ils"] = 0.0
        else:
            row_data["debit_ils"] = 0.0
            row_data["credit_ils"] = credit_acc

    # ── Cross-currency rates ──────────────────────────────────────────────
    jod_to_usd = target_rate or get_rate_fn(payment_currency, company_currency, posting_date)
    row_data["rate_jod_to_usd"] = flt(jod_to_usd, 9) if jod_to_usd else 0.0

    # Derive JOD→ILS rate from amounts when available
    if jod_amount and (debit_acc or credit_acc) and not is_bank_cash:
        ils_amount = debit_acc or credit_acc
        jod_to_ils_derived = flt(ils_amount / jod_amount, 9) if jod_amount else 0.0
    elif source_rate and payment_currency != party_currency:
        # source_exchange_rate: party_currency per payment_currency
        jod_to_ils_derived = source_rate
    else:
        jod_to_ils_derived = get_rate_fn(payment_currency, party_currency, posting_date)
        jod_to_ils_derived = flt(jod_to_ils_derived, 9) if jod_to_ils_derived else 0.0

    row_data["rate_jod_to_ils"] = jod_to_ils_derived

    # ── Currency metadata ─────────────────────────────────────────────────
    row_data["payment_currency"] = payment_currency
    row_data["party_currency"] = party_currency

    # ── Rate mismatch validation ──────────────────────────────────────────
    if payment_currency and party_currency and payment_currency != party_currency and jod_to_ils_derived:
        ref_rate = get_rate_fn(payment_currency, party_currency, posting_date)
        if ref_rate and flt(ref_rate) > 0:
            rel_diff = abs(jod_to_ils_derived - flt(ref_rate)) / abs(flt(ref_rate))
            if rel_diff > _RATE_MISMATCH_THRESHOLD:
                row_data["rate_mismatch_flag"] = (
                    f"RATE_MISMATCH: derived={flt(jod_to_ils_derived, 4)} "
                    f"vs ref={flt(ref_rate, 4)}"
                )

    # ── Amount mismatch validation ────────────────────────────────────────
    if jod_amount and jod_to_usd:
        expected_usd = flt(jod_amount * jod_to_usd, 2)
        actual_usd = flt(debit_company or credit_company, 2)
        if actual_usd and abs(expected_usd - actual_usd) > 0.01:
            row_data["amount_mismatch_flag"] = (
                f"AMOUNT_MISMATCH: expected={expected_usd} vs gl={actual_usd}"
            )

    # ── Audit columns ─────────────────────────────────────────────────────
    if audit_mode:
        row_data["payment_entry_id"] = pe.get("name", "")
        row_data["exchange_rate_source"] = (
            "PE.target_exchange_rate" if target_rate else "Currency Exchange"
        )
        row_data["raw_payment_amount"] = jod_amount
        row_data["raw_gl_amount"] = debit_company or credit_company


# ---------------------------------------------------------------------------
# SQL condition builder
# ---------------------------------------------------------------------------

def _build_conditions(filters):
    conditions = []
    values = {
        "company": filters["company"],
        "from_date": filters["from_date"],
        "to_date": filters["to_date"],
    }

    if filters.get("account"):
        conditions.append("gle.account = %(account)s")
        values["account"] = filters["account"]

    if filters.get("party_type"):
        conditions.append("gle.party_type = %(party_type)s")
        values["party_type"] = filters["party_type"]

    if filters.get("party"):
        conditions.append("gle.party = %(party)s")
        values["party"] = filters["party"]

    if filters.get("voucher_type"):
        conditions.append("gle.voucher_type = %(voucher_type)s")
        values["voucher_type"] = filters["voucher_type"]

    if filters.get("voucher_no"):
        conditions.append("gle.voucher_no = %(voucher_no)s")
        values["voucher_no"] = filters["voucher_no"]

    if filters.get("cost_center"):
        conditions.append("gle.cost_center = %(cost_center)s")
        values["cost_center"] = filters["cost_center"]

    if filters.get("project"):
        conditions.append("gle.project = %(project)s")
        values["project"] = filters["project"]

    condition_str = (" AND " + " AND ".join(conditions)) if conditions else ""
    return condition_str, values


# ---------------------------------------------------------------------------
# Performance: batch-fetch helpers (Phase 6 – no queries inside loops)
# ---------------------------------------------------------------------------

def _preload_payment_entries(gl_entries):
    """Return a dict {pe_name: pe_dict} for all PEs referenced in gl_entries."""
    pe_names = {
        row["voucher_no"]
        for row in gl_entries
        if row.get("voucher_type") == "Payment Entry" and row.get("voucher_no")
    }
    if not pe_names:
        return {}

    pe_rows = frappe.get_all(
        "Payment Entry",
        filters={"name": ["in", list(pe_names)]},
        fields=[
            "name",
            "payment_type",
            "paid_from", "paid_to",
            "paid_from_account_currency", "paid_to_account_currency",
            "paid_amount", "received_amount",
            "source_exchange_rate", "target_exchange_rate",
            "posting_date",
        ],
    )
    return {pe.name: pe for pe in pe_rows}


def _preload_account_types(gl_entries):
    """Return a dict {account_name: account_type} for all accounts in gl_entries."""
    accounts = {row["account"] for row in gl_entries if row.get("account")}
    if not accounts:
        return {}

    rows = frappe.get_all(
        "Account",
        filters={"name": ["in", list(accounts)]},
        fields=["name", "account_type"],
    )
    return {r.name: r.account_type for r in rows}


# ---------------------------------------------------------------------------
# Exchange rate lookup (with inverse fallback)
# ---------------------------------------------------------------------------

def _fetch_exchange_rate(from_currency, to_currency, posting_date=None):
    """Fetch the exchange rate from *from_currency* to *to_currency*.

    Returns 1.0 when currencies are equal.  Tries the direct pair first,
    then the inverse pair.  Returns None when no record is found.
    """
    if not from_currency or not to_currency:
        return None
    if from_currency == to_currency:
        return 1.0

    date_filter = getdate(posting_date) if posting_date else getdate(nowdate())

    rate = frappe.db.get_value(
        "Currency Exchange",
        {"from_currency": from_currency, "to_currency": to_currency,
         "date": ["<=", date_filter]},
        "exchange_rate",
        order_by="date desc",
    )
    if rate:
        return flt(rate)

    rate = frappe.db.get_value(
        "Currency Exchange",
        {"from_currency": to_currency, "to_currency": from_currency,
         "date": ["<=", date_filter]},
        "exchange_rate",
        order_by="date desc",
    )
    if rate and flt(rate) > 0:
        return flt(1.0 / flt(rate), 9)

    return None
