# Copyright (c) 2026, erpcloud.systems and contributors
# For license information, please see license.txt

"""
Shared helper utilities for Advanced Customer Statement and
Advanced Supplier Statement Script Reports.

Provides:
  * Column definitions for both party types
  * GL Entry query helpers with correct parameterisation
  * Opening-balance aggregation
  * Running-balance calculation
  * Row-label constants for summary rows
"""

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.utils import flt, getdate

# Labels used to identify summary rows in the formatter.
LABEL_OPENING = "Opening Balance"
LABEL_TOTAL = "Total"
LABEL_CLOSING = "Closing Balance"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_filters(filters, party_type):
    """Raise an informative error when mandatory filters are missing."""
    if not filters.get("company"):
        frappe.throw(_("Please select a Company"))
    if not filters.get("from_date") or not filters.get("to_date"):
        frappe.throw(_("Please select a Date Range (From Date and To Date)"))
    if getdate(filters.get("from_date")) > getdate(filters.get("to_date")):
        frappe.throw(_("From Date must be before To Date"))


# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

def get_columns(filters, party_type):
    """Return the column list for a party statement report.

    Args:
        filters (dict): Report filters (used to check show_in_company_currency).
        party_type (str): ``"Customer"`` or ``"Supplier"``.

    Returns:
        list[dict]: Column definitions ready for ERPNext Script Report.
    """
    company_currency = _company_currency(filters.get("company"))
    show_company_currency = filters.get("show_in_company_currency")

    party_label = _("Customer") if party_type == "Customer" else _("Supplier")
    party_doctype = party_type

    columns = [
        {
            "fieldname": "posting_date",
            "label": _("Posting Date"),
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
            "width": 160,
        },
        {
            "fieldname": "party",
            "label": party_label,
            "fieldtype": "Link",
            "options": party_doctype,
            "width": 150,
        },
        {
            "fieldname": "account",
            "label": _("Account Name"),
            "fieldtype": "Link",
            "options": "Account",
            "width": 200,
        },
        {
            "fieldname": "remarks",
            "label": _("Remarks"),
            "fieldtype": "Data",
            "width": 220,
        },
        {
            "fieldname": "debit_in_account_currency",
            "label": _("Debit (Account Currency)"),
            "fieldtype": "Currency",
            "options": "account_currency",
            "width": 160,
        },
        {
            "fieldname": "credit_in_account_currency",
            "label": _("Credit (Account Currency)"),
            "fieldtype": "Currency",
            "options": "account_currency",
            "width": 160,
        },
        {
            "fieldname": "balance_in_account_currency",
            "label": _("Balance (Account Currency)"),
            "fieldtype": "Currency",
            "options": "account_currency",
            "width": 165,
        },
        # Hidden helper column used by the JS formatter to render the correct
        # currency symbol for per-row currency columns.
        {
            "fieldname": "account_currency",
            "label": _("Account Currency"),
            "fieldtype": "Link",
            "options": "Currency",
            "width": 80,
            "hidden": 1,
        },
    ]

    if show_company_currency:
        columns += [
            {
                "fieldname": "debit",
                "label": _("Debit ({0})").format(company_currency),
                "fieldtype": "Currency",
                "options": "company_currency",
                "width": 140,
            },
            {
                "fieldname": "credit",
                "label": _("Credit ({0})").format(company_currency),
                "fieldtype": "Currency",
                "options": "company_currency",
                "width": 140,
            },
            {
                "fieldname": "balance_in_company_currency",
                "label": _("Balance ({0})").format(company_currency),
                "fieldtype": "Currency",
                "options": "company_currency",
                "width": 150,
            },
            # Hidden helper – resolved by the JS formatter.
            {
                "fieldname": "company_currency",
                "label": _("Company Currency"),
                "fieldtype": "Link",
                "options": "Currency",
                "width": 80,
                "hidden": 1,
            },
        ]

    return columns


# ---------------------------------------------------------------------------
# Data fetching helpers
# ---------------------------------------------------------------------------

def _company_currency(company):
    """Return the default currency code for *company* (e.g. ``"USD"``).

    Falls back to ``"USD"`` when the company is not found so callers can
    always safely use the return value as a currency label.
    """
    if not company:
        return "USD"
    return frappe.db.get_value("Company", company, "default_currency") or "USD"


def _build_conditions(filters, party_type, date_field="gle.posting_date"):
    """Return a (where_clause_str, values_dict) tuple for a GL Entry query.

    The *date_field* parameter lets callers use different date comparisons
    (e.g. ``<`` for opening balance vs ``BETWEEN`` for period rows).

    Args:
        filters (dict): Report filters.
        party_type (str): ``"Customer"`` or ``"Supplier"``.
        date_field (str): SQL expression inserted verbatim for date filtering.
            Pass ``None`` to omit date filtering (caller handles it separately).

    Returns:
        tuple[str, dict]: (WHERE clause fragment without the ``WHERE`` keyword,
                           parameter dict for ``frappe.db.sql``).
    """
    conds = [
        "gle.company = %(company)s",
        "gle.party_type = %(party_type)s",
        "gle.is_cancelled = 0",
    ]
    values = {
        "company": filters.get("company"),
        "party_type": party_type,
        "from_date": filters.get("from_date"),
        "to_date": filters.get("to_date"),
    }

    # Multi-select party filter
    parties = _to_list(filters.get("parties") or filters.get("customers") or filters.get("suppliers"))
    if parties:
        conds.append("gle.party IN %(parties)s")
        values["parties"] = tuple(parties)

    # Multi-select account filter
    accounts = _to_list(filters.get("accounts"))
    if accounts:
        conds.append("gle.account IN %(accounts)s")
        values["accounts"] = tuple(accounts)

    return " AND ".join(conds), values


def _to_list(value):
    """Normalise a filter value to a Python list.

    Handles:
    * ``None`` / empty string → ``[]``
    * ``list`` / ``tuple`` → returned as a plain list
    * Comma-separated ``str`` → split and stripped
    """
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [v for v in value if v]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def get_opening_balance(filters, party_type):
    """Fetch aggregated opening balance rows (before *from_date*).

    Returns one row per ``(party, account, account_currency)`` combination so
    that multi-account / multi-currency scenarios are handled correctly.

    Args:
        filters (dict): Report filters.
        party_type (str): ``"Customer"`` or ``"Supplier"``.

    Returns:
        list[dict]: Each dict has keys:
            ``party``, ``account``, ``account_currency``,
            ``opening_debit``, ``opening_credit``,
            ``opening_debit_company``, ``opening_credit_company``.
    """
    conds, values = _build_conditions(filters, party_type)
    query = """
        SELECT
            gle.party,
            gle.account,
            gle.account_currency,
            SUM(gle.debit_in_account_currency)  AS opening_debit,
            SUM(gle.credit_in_account_currency) AS opening_credit,
            SUM(gle.debit)                       AS opening_debit_company,
            SUM(gle.credit)                      AS opening_credit_company
        FROM `tabGL Entry` gle
        WHERE {conds}
          AND gle.posting_date < %(from_date)s
        GROUP BY gle.party, gle.account, gle.account_currency
        ORDER BY gle.party, gle.account
    """.format(conds=conds)
    return frappe.db.sql(query, values, as_dict=True)


def get_gl_entries(filters, party_type):
    """Fetch individual GL entries for the selected period.

    Args:
        filters (dict): Report filters.
        party_type (str): ``"Customer"`` or ``"Supplier"``.

    Returns:
        list[dict]: One dict per GL Entry with all fields needed by the report.
    """
    conds, values = _build_conditions(filters, party_type)
    query = """
        SELECT
            gle.posting_date,
            gle.voucher_type,
            gle.voucher_no,
            gle.party,
            gle.account,
            gle.remarks,
            gle.debit_in_account_currency,
            gle.credit_in_account_currency,
            gle.debit,
            gle.credit,
            gle.account_currency
        FROM `tabGL Entry` gle
        WHERE {conds}
          AND gle.posting_date BETWEEN %(from_date)s AND %(to_date)s
        ORDER BY gle.posting_date, gle.voucher_no, gle.name
    """.format(conds=conds)
    return frappe.db.sql(query, values, as_dict=True)


# ---------------------------------------------------------------------------
# Data assembly
# ---------------------------------------------------------------------------

def build_report_data(filters, party_type):
    """Assemble the full report data list including summary rows.

    Algorithm:
    1. Fetch opening balances (one row per party/account/currency combination).
    2. Emit an ``Opening Balance`` row for each group that has a non-zero
       pre-period balance.
    3. Stream through period GL entries, maintaining per-group running balances.
    4. Append a ``Closing Balance`` summary row at the end.

    Args:
        filters (dict): Report filters.
        party_type (str): ``"Customer"`` or ``"Supplier"``.

    Returns:
        list[dict]: Rows ready for the Script Report ``execute`` return value.
    """
    show_company_currency = filters.get("show_in_company_currency")
    company_currency = _company_currency(filters.get("company"))

    opening_rows = get_opening_balance(filters, party_type)
    gl_entries = get_gl_entries(filters, party_type)

    # ── State: running balances keyed by (party, account, account_currency) ──
    # balance_key → {"acc": float, "company": float}
    running = {}

    def _key(row):
        return (
            row.get("party") or "",
            row.get("account") or "",
            row.get("account_currency") or company_currency,
        )

    def _state(key, ac):
        if key not in running:
            running[key] = {"acc": 0.0, "company": 0.0, "account_currency": ac}
        return running[key]

    data = []

    # ── 1. Opening balance rows ──────────────────────────────────────────────
    for ob in opening_rows:
        ac = ob.account_currency or company_currency
        key = (ob.party or "", ob.account or "", ac)
        ob_acc = flt(ob.opening_debit) - flt(ob.opening_credit)
        ob_company = flt(ob.opening_debit_company) - flt(ob.opening_credit_company)

        state = _state(key, ac)
        state["acc"] = ob_acc
        state["company"] = ob_company

        row = {
            "posting_date": filters.get("from_date"),
            "voucher_type": "",
            "voucher_no": "",
            "party": ob.party or "",
            "account": ob.account or "",
            "remarks": _(LABEL_OPENING),
            "debit_in_account_currency": flt(ob.opening_debit),
            "credit_in_account_currency": flt(ob.opening_credit),
            "balance_in_account_currency": ob_acc,
            "account_currency": ac,
            "company_currency": company_currency,
            "row_type": "opening",
        }
        if show_company_currency:
            row["debit"] = flt(ob.opening_debit_company)
            row["credit"] = flt(ob.opening_credit_company)
            row["balance_in_company_currency"] = ob_company
        data.append(row)

    # ── 2. Period transaction rows ───────────────────────────────────────────
    period_totals = {"debit_acc": 0.0, "credit_acc": 0.0, "debit_co": 0.0, "credit_co": 0.0}

    for gle in gl_entries:
        ac = gle.account_currency or company_currency
        key = _key(gle)
        state = _state(key, ac)

        d_acc = flt(gle.debit_in_account_currency)
        c_acc = flt(gle.credit_in_account_currency)
        d_co = flt(gle.debit)
        c_co = flt(gle.credit)

        state["acc"] += d_acc - c_acc
        state["company"] += d_co - c_co
        state["account_currency"] = ac

        period_totals["debit_acc"] += d_acc
        period_totals["credit_acc"] += c_acc
        period_totals["debit_co"] += d_co
        period_totals["credit_co"] += c_co

        row = {
            "posting_date": gle.posting_date,
            "voucher_type": gle.voucher_type,
            "voucher_no": gle.voucher_no,
            "party": gle.party or "",
            "account": gle.account or "",
            "remarks": gle.remarks or "",
            "debit_in_account_currency": d_acc,
            "credit_in_account_currency": c_acc,
            "balance_in_account_currency": state["acc"],
            "account_currency": ac,
            "company_currency": company_currency,
            "row_type": "entry",
        }
        if show_company_currency:
            row["debit"] = d_co
            row["credit"] = c_co
            row["balance_in_company_currency"] = state["company"]
        data.append(row)

    # ── 3. Closing balance row ───────────────────────────────────────────────
    if data:
        # Sum closing balances across all groups (gives net balance for display).
        closing_acc = sum(s["acc"] for s in running.values())
        closing_company = sum(s["company"] for s in running.values())

        closing_row = {
            "posting_date": filters.get("to_date"),
            "voucher_type": "",
            "voucher_no": "",
            "party": "",
            "account": "",
            "remarks": _(LABEL_CLOSING),
            "debit_in_account_currency": period_totals["debit_acc"],
            "credit_in_account_currency": period_totals["credit_acc"],
            "balance_in_account_currency": closing_acc,
            "account_currency": company_currency,
            "company_currency": company_currency,
            "row_type": "closing",
        }
        if show_company_currency:
            closing_row["debit"] = period_totals["debit_co"]
            closing_row["credit"] = period_totals["credit_co"]
            closing_row["balance_in_company_currency"] = closing_company
        data.append(closing_row)

    return data
