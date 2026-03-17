# Copyright (c) 2021, erpcloud.systems and contributors
# For license information, please see license.txt

"""
General Ledger report override.

Fixes the "Add Columns in Transaction Currency" feature so that each GL row
uses its own ``account_currency`` (sourced directly from the GL Entry / Account
master) rather than a shared filter/presentation currency.

Also adds two extra data columns when the transaction-currency option is enabled:

* ``debit_in_payment_currency``  – Debit amount in the payment method (cheque)
  currency (e.g. JOD when the MOP account is a JOD bank account).
* ``credit_in_payment_currency`` – Credit amount in the same payment currency.

These columns allow the report to show the "raw" cheque value on BOTH the
party-account row (ILS debit) and the bank/wallet row (JOD credit) so that
the user can see all three values: USD (company), JOD (payment), ILS (party).

This module is monkey-patched onto the ERPNext GL report at app boot time via
the ``boot_session`` hook in hooks.py.
"""

import frappe
from frappe.utils import flt, getdate, nowdate


# Column definitions for the extra payment-currency columns injected into the
# GL report.  They are appended after the standard transaction-currency columns.
_PAYMENT_CURRENCY_COLUMNS = [
    {
        "fieldname": "debit_in_payment_currency",
        "label": "Debit (Payment Currency)",
        "fieldtype": "Currency",
        "options": "payment_currency",
        "width": 130,
    },
    {
        "fieldname": "credit_in_payment_currency",
        "label": "Credit (Payment Currency)",
        "fieldtype": "Currency",
        "options": "payment_currency",
        "width": 130,
    },
    {
        "fieldname": "debit_in_party_currency",
        "label": "Debit (Party Currency)",
        "fieldtype": "Currency",
        "options": "party_currency",
        "width": 130,
    },
    {
        "fieldname": "credit_in_party_currency",
        "label": "Credit (Party Currency)",
        "fieldtype": "Currency",
        "options": "party_currency",
        "width": 130,
    },
]


def patch_general_ledger_report(*args, **kwargs):
	"""Monkey-patch the ERPNext General Ledger report's execute function.

	The original execute function may set a single presentation currency for all
	rows when "Add Columns in Transaction Currency" is enabled, which causes every
	row to display the same (wrong) currency symbol.  This wrapper ensures each
	row's ``account_currency`` is populated from the GL Entry's account master so
	the column formatter can render per-row currencies correctly.

	Accepts ``*args, **kwargs`` so Frappe can call this as a ``boot_session``
	hook (which passes the boot data as a positional argument) without raising
	a ``TypeError``.
	"""
	try:
		import erpnext.accounts.report.general_ledger.general_ledger as gl_module
	except ImportError:
		return  # ERPNext not installed – nothing to patch

	if getattr(gl_module, "_ecs_patched", False):
		return  # already patched in this process

	_original_execute = gl_module.execute

	def _patched_execute(filters=None):
		result = _original_execute(filters)

		# execute() may return (columns, data) or a dict – handle both
		if isinstance(result, (list, tuple)) and len(result) >= 2:
			columns, data = result[0], result[1]
			_fix_account_currency_per_row(data)

			# Enrich data with payment-currency cross-reference values.
			_add_payment_currency_data(data)

			# Inject the extra columns if the transaction-currency option is on.
			show_tx_currency = (
				filters
				and isinstance(filters, dict)
				and filters.get("add_values_in_transaction_currency")
			)
			if show_tx_currency:
				columns = _inject_payment_currency_columns(columns)

			return (columns, data) + tuple(result[2:])

		return result

	gl_module.execute = _patched_execute
	gl_module._ecs_patched = True


def _inject_payment_currency_columns(columns):
	"""Return a new columns list with the payment-currency columns appended.

	Avoids duplicating them if the report is executed multiple times in the
	same process (e.g. during exports).
	"""
	existing_fieldnames = {
		(c.get("fieldname") if isinstance(c, dict) else c)
		for c in columns
	}
	extra = [
		col for col in _PAYMENT_CURRENCY_COLUMNS
		if col["fieldname"] not in existing_fieldnames
	]
	return list(columns) + extra


def _fetch_exchange_rate(from_currency, to_currency, posting_date=None):
	"""Fetch the exchange rate from *from_currency* to *to_currency* using the
	Currency Exchange DocType.

	Returns 1.0 when currencies are equal.  Tries the direct pair first, then
	the inverse pair.  Returns None when no matching record is found.
	"""
	if not from_currency or not to_currency:
		return None
	if from_currency == to_currency:
		return 1.0

	date_filter = getdate(posting_date) if posting_date else getdate(nowdate())

	# Direct pair
	rate = frappe.db.get_value(
		"Currency Exchange",
		{"from_currency": from_currency, "to_currency": to_currency,
		 "date": ["<=", date_filter]},
		"exchange_rate",
		order_by="date desc",
	)
	if rate:
		return flt(rate)

	# Inverse pair
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


def _add_payment_currency_data(data):
	"""Populate payment-currency and party-currency columns for GL rows that
	originate from Payment Entries.

	For each Payment Entry referenced in the data, we fetch:
	- ``paid_to_account_currency``  – the bank/wallet account currency (e.g. JOD)
	- ``received_amount``           – the amount in that currency
	- ``paid_from_account_currency`` – the party/source account currency (e.g. ILS)
	- ``paid_amount``               – the amount on the party side

	The following fields are populated on every matching GL row:

	* ``payment_currency``          – e.g. "JOD"
	* ``debit_in_payment_currency`` – debit in JOD
	* ``credit_in_payment_currency`` – credit in JOD
	* ``party_currency``            – e.g. "ILS"
	* ``debit_in_party_currency``   – debit in ILS
	* ``credit_in_party_currency``  – credit in ILS

	Exchange rates are cached per (from, to, date) key to avoid redundant DB
	queries when many GL rows reference the same Payment Entry.
	"""
	if not data:
		return

	pe_names = {
		row.get("voucher_no")
		for row in data
		if isinstance(row, dict)
		and row.get("voucher_type") == "Payment Entry"
		and row.get("voucher_no")
	}
	if not pe_names:
		return

	pe_rows = frappe.get_all(
		"Payment Entry",
		filters={"name": ["in", list(pe_names)]},
		fields=[
			"name",
			"paid_from", "paid_to",
			"paid_from_account_currency", "paid_to_account_currency",
			"paid_amount", "received_amount",
			"source_exchange_rate", "target_exchange_rate",
			"posting_date",
		],
	)

	# Build map: pe_name → PE data
	pe_map = {pe.name: pe for pe in pe_rows}

	# Exchange-rate cache: (from_currency, to_currency, date_str) → rate
	_rate_cache = {}

	def _get_rate(from_currency, to_currency, posting_date=None):
		"""Cached exchange-rate lookup."""
		if not from_currency or not to_currency:
			return None
		if from_currency == to_currency:
			return 1.0
		date_str = str(posting_date) if posting_date else ""
		key = (from_currency, to_currency, date_str)
		if key not in _rate_cache:
			_rate_cache[key] = _fetch_exchange_rate(from_currency, to_currency, posting_date)
		return _rate_cache[key]

	for row in data:
		if not isinstance(row, dict):
			continue
		if row.get("voucher_type") != "Payment Entry":
			continue
		pe_name = row.get("voucher_no")
		if not pe_name or pe_name not in pe_map:
			continue

		pe = pe_map[pe_name]
		account = row.get("account")
		posting_date = pe.get("posting_date")

		# Payment currency = MOP/bank account currency (paid_to for Receive; paid_from for Pay)
		payment_currency = pe.paid_to_account_currency or pe.paid_from_account_currency or ""
		# Party currency = the counterpart account currency
		party_currency = pe.paid_from_account_currency or pe.paid_to_account_currency or ""
		# If both sides have a value, paid_from is the party and paid_to is the bank (Receive)
		if pe.paid_from_account_currency and pe.paid_to_account_currency:
			if pe.paid_from_account_currency != pe.paid_to_account_currency:
				payment_currency = pe.paid_to_account_currency   # bank side
				party_currency = pe.paid_from_account_currency   # party side

		row["payment_currency"] = payment_currency
		row["party_currency"] = party_currency

		debit_company = flt(row.get("debit") or 0)
		credit_company = flt(row.get("credit") or 0)
		base_company = debit_company or credit_company

		# ── Payment-currency amounts ────────────────────────────────────────
		if account == pe.paid_to:
			# Bank/wallet credit row: use received_amount directly (in payment currency)
			pay_debit = 0.0
			pay_credit = flt(pe.received_amount or 0)
			if not pay_credit and base_company:
				rate = flt(pe.target_exchange_rate)
				pay_credit = flt(base_company / rate, 9) if rate and rate != 0 else flt(base_company, 9)
		elif account == pe.paid_from:
			# Party debit row: use paid_amount directly (in party/paid_from currency)
			pay_debit = flt(pe.paid_amount or 0)
			pay_credit = 0.0
			if not pay_debit and base_company:
				rate = flt(pe.source_exchange_rate)
				pay_debit = flt(base_company / rate, 9) if rate and rate != 0 else flt(base_company, 9)
		else:
			# Other rows – derive from base company amount using target rate
			rate = flt(pe.target_exchange_rate)
			derived = flt(base_company / rate, 9) if rate and rate != 0 else flt(base_company, 9)
			pay_debit = derived if debit_company else 0.0
			pay_credit = derived if credit_company else 0.0

		row["debit_in_payment_currency"] = pay_debit
		row["credit_in_payment_currency"] = pay_credit

		# ── Party-currency amounts ──────────────────────────────────────────
		# Convert the payment-currency amounts → party currency using Currency Exchange.
		if party_currency and payment_currency and party_currency != payment_currency:
			rate_to_party = _get_rate(payment_currency, party_currency, posting_date)
			if rate_to_party is None:
				# Warn and fall back to zero so the column is visibly blank rather
				# than showing a silently wrong value.
				frappe.log_error(
					f"ECS GL: No exchange rate found for {payment_currency} → {party_currency} "
					f"on {posting_date}. Party currency columns left blank for {pe_name}.",
					"ECS GL Missing Exchange Rate",
				)
				row["debit_in_party_currency"] = 0.0
				row["credit_in_party_currency"] = 0.0
			else:
				row["debit_in_party_currency"] = flt(pay_debit * rate_to_party, 9)
				row["credit_in_party_currency"] = flt(pay_credit * rate_to_party, 9)
		else:
			# Same currency or no conversion needed – use payment-currency values directly.
			row["debit_in_party_currency"] = pay_debit
			row["credit_in_party_currency"] = pay_credit


def _fix_account_currency_per_row(data):
	"""Ensure every data row contains the correct ``account_currency`` value.

	ERPNext's GL report may omit ``account_currency`` or set it to the filter
	presentation currency for all rows.  This function fills in the correct
	per-row currency using two sources:

	1. **Payment Entry rows** – the ``paid_from_account_currency`` /
	   ``paid_to_account_currency`` fields from the linked Payment Entry are
	   used so the "Transaction Currency" columns reflect the actual currency
	   of ``paid_amount`` / ``received_amount`` rather than a global fallback.

	2. **All other rows** – the ``account_currency`` is read from the
	   ``Account`` master (batch-fetched for performance).
	"""
	if not data:
		return

	# Collect all unique account names first, then batch-fetch currencies.
	accounts = {row.get("account") for row in data if isinstance(row, dict) and row.get("account")}
	if not accounts:
		return

	account_rows = frappe.get_all(
		"Account",
		filters={"name": ["in", list(accounts)]},
		fields=["name", "account_currency"],
	)
	account_currency_map = {r.name: r.account_currency for r in account_rows if r.account_currency}

	# Batch-fetch Payment Entry currencies for GL rows that come from Payment Entries.
	# Map: (pe_name, account_name) → account_currency
	pe_account_currency_map = {}
	pe_names = {
		row.get("voucher_no")
		for row in data
		if isinstance(row, dict)
		and row.get("voucher_type") == "Payment Entry"
		and row.get("voucher_no")
	}
	if pe_names:
		pe_rows = frappe.get_all(
			"Payment Entry",
			filters={"name": ["in", list(pe_names)]},
			fields=["name", "paid_from", "paid_to", "paid_from_account_currency", "paid_to_account_currency"],
		)
		for pe in pe_rows:
			if pe.paid_from and pe.paid_from_account_currency:
				pe_account_currency_map[(pe.name, pe.paid_from)] = pe.paid_from_account_currency
			if pe.paid_to and pe.paid_to_account_currency:
				pe_account_currency_map[(pe.name, pe.paid_to)] = pe.paid_to_account_currency

	for row in data:
		if not isinstance(row, dict):
			continue
		account = row.get("account")
		if not account:
			continue

		# For Payment Entry rows, prefer the currency stored on the PE document.
		if row.get("voucher_type") == "Payment Entry" and row.get("voucher_no"):
			pe_currency = pe_account_currency_map.get((row["voucher_no"], account))
			if pe_currency:
				row["account_currency"] = pe_currency
				# Keep transaction_currency in sync so the "Add Columns in
				# Transaction Currency" columns display the correct symbol.
				row["transaction_currency"] = pe_currency
				continue

		# Fall back to Account master currency for all other rows.
		if account_currency_map.get(account):
			currency = account_currency_map[account]
			row["account_currency"] = currency
			# Always keep transaction_currency in sync with account_currency so
			# the "Add Columns in Transaction Currency" columns display the
			# correct symbol even when the GL report pre-populated the field
			# with a different (e.g. company) currency.
			row["transaction_currency"] = currency
