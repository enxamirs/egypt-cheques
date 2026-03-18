# Copyright (c) 2021, erpcloud.systems and contributors
# For license information, please see license.txt

"""
General Ledger report override – multi-currency audit layer.

Fixes the "Add Columns in Transaction Currency" feature so that each GL row
uses its own ``account_currency`` (sourced directly from the GL Entry / Account
master) rather than a shared filter/presentation currency.

For Payment Entries involving three currencies (e.g. Payment Currency = JOD,
Party Currency = ILS, Company Currency = USD), adds a smart multi-currency
audit layer with the following extra columns:

Primary payment-currency columns (JOD shown on BOTH sides):
  * ``source_debit_jod``   – Debit in payment currency for EVERY GL row of the PE.
  * ``source_credit_jod``  – Credit in payment currency for EVERY GL row of the PE.

Party-currency columns:
  * ``party_debit_ils``    – Debit in party account currency (e.g. ILS).
  * ``party_credit_ils``   – Credit in party account currency.

Cross-currency rate columns:
  * ``jod_to_usd_rate``    – Payment currency → company currency rate.
  * ``jod_to_ils_rate``    – Payment currency → party currency rate.

Validation column:
  * ``rate_mismatch_warning`` – Non-empty when the derived rate differs from
                                the Currency Exchange master by more than 0.01%.

Traceability columns:
  * ``payment_entry_reference``    – Link to the Payment Entry.
  * ``multiple_cheque_reference``  – Link to the originating Multiple Cheque Entry
                                     (when the PE was created from one).

Legacy backward-compatible columns (kept for existing integrations):
  * ``debit_in_payment_currency``  – Debit in bank/MOP account currency.
  * ``credit_in_payment_currency`` – Credit in bank/MOP account currency.
  * ``debit_in_party_currency``    – Debit in party account currency (ILS equiv.).
  * ``credit_in_party_currency``   – Credit in party account currency.

Phase 3 – "Add Columns in Transaction Currency" override:
  When this filter is enabled the standard ERPNext columns
  (``debit_in_account_currency`` / ``credit_in_account_currency``) are
  overridden to display the payment currency (JOD) for ALL GL rows of the PE,
  ensuring a uniform currency view rather than mixing ILS and JOD per side.

This module is monkey-patched onto the ERPNext General Ledger report at app
boot time via the ``boot_session`` hook in hooks.py.
"""

import frappe
from frappe.utils import flt, getdate, nowdate

# Tolerance for exchange-rate mismatch warnings (0.01 % of the reference rate).
_RATE_MISMATCH_THRESHOLD = 0.0001


# ---------------------------------------------------------------------------
# Column definitions injected into the GL report
# ---------------------------------------------------------------------------

# Legacy columns kept for backward compatibility.
_LEGACY_PAYMENT_CURRENCY_COLUMNS = [
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

# New Phase 1 columns: explicit multi-currency audit layer.
_AUDIT_COLUMNS = [
    # ── Primary payment-currency columns (JOD for BOTH sides) ────────────
    {
        "fieldname": "source_debit_jod",
        "label": "Debit (Payment)",
        "fieldtype": "Currency",
        "options": "payment_currency",
        "width": 130,
    },
    {
        "fieldname": "source_credit_jod",
        "label": "Credit (Payment)",
        "fieldtype": "Currency",
        "options": "payment_currency",
        "width": 130,
    },
    # ── Party-currency columns ────────────────────────────────────────────
    {
        "fieldname": "party_debit_ils",
        "label": "Debit (Party)",
        "fieldtype": "Currency",
        "options": "party_currency",
        "width": 130,
    },
    {
        "fieldname": "party_credit_ils",
        "label": "Credit (Party)",
        "fieldtype": "Currency",
        "options": "party_currency",
        "width": 130,
    },
    # ── Cross-currency rate columns ───────────────────────────────────────
    {
        "fieldname": "jod_to_usd_rate",
        "label": "Rate (Payment→Company)",
        "fieldtype": "Float",
        "width": 150,
    },
    {
        "fieldname": "jod_to_ils_rate",
        "label": "Rate (Payment→Party)",
        "fieldtype": "Float",
        "width": 140,
    },
    # ── Validation column ─────────────────────────────────────────────────
    {
        "fieldname": "rate_mismatch_warning",
        "label": "Rate Warning",
        "fieldtype": "Data",
        "width": 160,
    },
    # ── Traceability columns ──────────────────────────────────────────────
    {
        "fieldname": "payment_entry_reference",
        "label": "Payment Entry",
        "fieldtype": "Link",
        "options": "Payment Entry",
        "width": 160,
    },
    {
        "fieldname": "multiple_cheque_reference",
        "label": "Multiple Cheque Entry",
        "fieldtype": "Link",
        "options": "Multiple Cheque Entry",
        "width": 190,
    },
]

# All payment-currency columns combined (legacy first, then new).
_PAYMENT_CURRENCY_COLUMNS = _LEGACY_PAYMENT_CURRENCY_COLUMNS + _AUDIT_COLUMNS


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


def _fetch_multiple_cheque_references(pe_names):
	"""Return a mapping of pe_name → Multiple Cheque Entry name.

	Looks up the Cheque Table Receive child rows whose ``payment_entry``
	field matches one of the supplied PE names.  The ``parent`` of each
	matching row is the Multiple Cheque Entry that originated the PE.

	Returns a dict: {pe_name: mce_name}.  PEs that were not created from
	a Multiple Cheque Entry are absent from the returned dict.
	"""
	if not pe_names:
		return {}
	rows = frappe.get_all(
		"Cheque Table Receive",
		filters={
			"payment_entry": ["in", list(pe_names)],
			"parenttype": "Multiple Cheque Entry",
		},
		fields=["payment_entry", "parent"],
	)
	return {r.payment_entry: r.parent for r in rows if r.payment_entry and r.parent}


def _add_payment_currency_data(data):
	"""Populate multi-currency audit columns for GL rows from Payment Entries.

	For each Payment Entry referenced in *data* the following fields are set
	on every matching GL row:

	Currency metadata
	  ``payment_currency``          – bank / MOP account currency (e.g. JOD)
	  ``party_currency``            – party account currency (e.g. ILS)

	Legacy backward-compatible columns
	  ``debit_in_payment_currency`` / ``credit_in_payment_currency``
	  ``debit_in_party_currency``   / ``credit_in_party_currency``

	Phase 1 – primary payment-currency columns (JOD shown on BOTH GL sides)
	  ``source_debit_jod``          – debit expressed in payment currency
	  ``source_credit_jod``         – credit expressed in payment currency

	Phase 1 – party-currency columns
	  ``party_debit_ils``           – debit expressed in party currency
	  ``party_credit_ils``          – credit expressed in party currency

	Phase 4 – cross-currency rate columns
	  ``jod_to_usd_rate``           – payment currency → company currency rate
	  ``jod_to_ils_rate``           – payment currency → party currency rate

	Phase 4/5 – validation
	  ``rate_mismatch_warning``     – non-empty string when the derived rate
	                                  differs from the Currency Exchange master
	                                  by more than _RATE_MISMATCH_THRESHOLD.

	Phase 7 – traceability
	  ``payment_entry_reference``   – name of the Payment Entry
	  ``multiple_cheque_reference`` – name of the originating Multiple Cheque
	                                  Entry (empty when not applicable)

	Exchange rates are cached per (from, to, date) key to avoid redundant DB
	queries when many GL rows reference the same Payment Entry (Phase 6).
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

	# Phase 7: pre-fetch Multiple Cheque Entry references (one batch query).
	mce_map = _fetch_multiple_cheque_references(pe_names)

	# Phase 6: exchange-rate cache – (from_currency, to_currency, date_str) → rate
	_rate_cache = {}

	def _get_rate(from_currency, to_currency, posting_date=None):
		"""Cached exchange-rate lookup (Phase 6 performance optimisation)."""
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

		# ── Phase 2 Step 2: identify currency roles ─────────────────────────
		# Payment currency = MOP/bank account currency (paid_to for Receive)
		payment_currency = pe.paid_to_account_currency or pe.paid_from_account_currency or ""
		# Party currency = counterpart account currency
		party_currency = pe.paid_from_account_currency or pe.paid_to_account_currency or ""
		# When both differ: paid_from = party side, paid_to = bank/payment side
		if pe.paid_from_account_currency and pe.paid_to_account_currency:
			if pe.paid_from_account_currency != pe.paid_to_account_currency:
				payment_currency = pe.paid_to_account_currency  # bank / MOP
				party_currency = pe.paid_from_account_currency  # party account

		row["payment_currency"] = payment_currency
		row["party_currency"] = party_currency

		# Phase 7: traceability columns
		row["payment_entry_reference"] = pe_name
		row["multiple_cheque_reference"] = mce_map.get(pe_name) or ""

		debit_company = flt(row.get("debit") or 0)
		credit_company = flt(row.get("credit") or 0)
		base_company = debit_company or credit_company

		# ── Phase 2 Step 4/5: determine canonical JOD amount ────────────────
		# received_amount is always in paid_to_account_currency (= payment_currency
		# = JOD for a Receive PE).  Use it as the source of truth for JOD.
		jod_amount = flt(pe.received_amount or 0)
		if not jod_amount:
			# Fallback: derive from company-currency base using target rate.
			target_rate = flt(pe.target_exchange_rate)
			jod_amount = flt(base_company / target_rate, 9) if target_rate else flt(base_company, 9)

		# ── Phase 2 Step 5: company-currency rate (JOD → USD) ───────────────
		# target_exchange_rate = paid_to_currency → company_currency
		target_rate = flt(pe.target_exchange_rate)
		jod_to_usd = target_rate if target_rate else None
		if jod_to_usd is None and jod_amount:
			jod_to_usd = _get_rate(payment_currency, _company_currency_of_row(row), posting_date)

		# ── Legacy payment-currency amounts (backward compat) ────────────────
		if account == pe.paid_to:
			# Bank/wallet row: received_amount is directly in payment currency.
			pay_debit = 0.0
			pay_credit = flt(pe.received_amount or 0)
			if not pay_credit and base_company:
				pay_credit = (
					flt(base_company / target_rate, 9) if target_rate else flt(base_company, 9)
				)
		elif account == pe.paid_from:
			# Party row (legacy): keep paid_amount (party currency) for this column.
			pay_debit = flt(pe.paid_amount or 0)
			pay_credit = 0.0
			if not pay_debit and base_company:
				source_rate = flt(pe.source_exchange_rate)
				pay_debit = (
					flt(base_company / source_rate, 9) if source_rate else flt(base_company, 9)
				)
		else:
			# Other rows – derive from company-currency amount.
			derived = (
				flt(base_company / target_rate, 9) if target_rate else flt(base_company, 9)
			)
			pay_debit = derived if debit_company else 0.0
			pay_credit = derived if credit_company else 0.0

		row["debit_in_payment_currency"] = pay_debit
		row["credit_in_payment_currency"] = pay_credit

		# ── Phase 1: source JOD columns – payment currency for BOTH sides ────
		# Per the requirement: source_debit/credit_jod must use the SAME JOD
		# amount regardless of whether the row is the bank or party side.
		if debit_company:
			row["source_debit_jod"] = jod_amount
			row["source_credit_jod"] = 0.0
		elif credit_company:
			row["source_debit_jod"] = 0.0
			row["source_credit_jod"] = jod_amount
		else:
			row["source_debit_jod"] = 0.0
			row["source_credit_jod"] = 0.0

		# ── Phase 2 Step 5: compute party-currency (ILS) amounts ─────────────
		rate_jod_to_ils = None
		if party_currency and payment_currency and party_currency != payment_currency:
			rate_jod_to_ils = _get_rate(payment_currency, party_currency, posting_date)

		if rate_jod_to_ils is None and party_currency == payment_currency:
			rate_jod_to_ils = 1.0

		if rate_jod_to_ils is None:
			# Missing rate – log and leave party columns blank (Phase 5 guard).
			frappe.log_error(
				f"ECS GL: No exchange rate found for {payment_currency} → {party_currency} "
				f"on {posting_date}. Party currency columns left blank for {pe_name}.",
				"ECS GL Missing Exchange Rate",
			)
			ils_debit = 0.0
			ils_credit = 0.0
		else:
			ils_debit = flt(row["source_debit_jod"] * rate_jod_to_ils, 9)
			ils_credit = flt(row["source_credit_jod"] * rate_jod_to_ils, 9)

		# Legacy party-currency columns.
		if party_currency and payment_currency and party_currency != payment_currency:
			row["debit_in_party_currency"] = flt(pay_debit * (rate_jod_to_ils or 0), 9)
			row["credit_in_party_currency"] = flt(pay_credit * (rate_jod_to_ils or 0), 9)
		else:
			row["debit_in_party_currency"] = pay_debit
			row["credit_in_party_currency"] = pay_credit

		# Phase 1: explicit party-currency columns (ILS for both sides).
		row["party_debit_ils"] = ils_debit
		row["party_credit_ils"] = ils_credit

		# ── Phase 4: cross-currency rate columns ─────────────────────────────
		row["jod_to_usd_rate"] = flt(jod_to_usd, 9) if jod_to_usd else 0.0
		row["jod_to_ils_rate"] = flt(rate_jod_to_ils, 9) if rate_jod_to_ils else 0.0

		# ── Phase 4/5: cross-currency rate validation ─────────────────────────
		warning = _validate_exchange_rate(
			payment_currency, party_currency, rate_jod_to_ils, posting_date, _rate_cache
		)
		row["rate_mismatch_warning"] = warning or ""


def _company_currency_of_row(row):
	"""Return the company currency for a GL data row.

	Reads the company from the ``company`` column when available and looks up
	the default currency.  Returns ``""`` when the company is not known so
	the caller can handle it gracefully.
	"""
	company = row.get("company") if isinstance(row, dict) else None
	if company:
		return frappe.db.get_value("Company", company, "default_currency") or ""
	return ""


def _validate_exchange_rate(payment_currency, party_currency, derived_rate, posting_date, rate_cache):
	"""Phase 5: validate the derived exchange rate against the Currency Exchange master.

	Returns a non-empty warning string when the absolute relative difference
	exceeds ``_RATE_MISMATCH_THRESHOLD``, or ``""`` when everything is
	consistent.

	Guards against:
	  * Missing currencies (returns ``""`` – no comparison possible)
	  * Zero/None derived rate (returns explicit warning)
	  * Division by zero (returns explicit warning)
	"""
	if not payment_currency or not party_currency or payment_currency == party_currency:
		return ""

	# Phase 5: guard against zero/missing derived rate.
	if not derived_rate:
		return f"MISSING_RATE: {payment_currency}→{party_currency}"

	date_str = str(posting_date) if posting_date else ""
	cache_key = (payment_currency, party_currency, date_str)
	if cache_key not in rate_cache:
		rate_cache[cache_key] = _fetch_exchange_rate(payment_currency, party_currency, posting_date)
	reference_rate = rate_cache[cache_key]

	if reference_rate is None:
		# No Currency Exchange record – cannot validate.
		return ""

	# Phase 5: guard against division by zero.
	if flt(reference_rate) == 0:
		return f"ZERO_REF_RATE: {payment_currency}→{party_currency}"

	relative_diff = abs(flt(derived_rate) - flt(reference_rate)) / abs(flt(reference_rate))
	if relative_diff > _RATE_MISMATCH_THRESHOLD:
		return (
			f"RATE_MISMATCH: derived={flt(derived_rate, 6)} "
			f"vs ref={flt(reference_rate, 6)} "
			f"({payment_currency}→{party_currency})"
		)
	return ""


def _fix_account_currency_per_row(data):
	"""Ensure every data row contains the correct ``account_currency`` value.

	ERPNext's GL report may omit ``account_currency`` or set it to the filter
	presentation currency for all rows.  This function fills in the correct
	per-row currency by reading each account's currency from the Account master
	(batch-fetched for performance).

	GL Entry.transaction_currency is set to the account's own currency so that
	the standard ERPNext "Add Columns in Transaction Currency" columns display
	the correct per-account amount and symbol (e.g. 3000 ILS for the customer
	row and 1000 JOD for the bank row).  debit_in_account_currency and
	credit_in_account_currency are NOT overridden – ERPNext populates these
	correctly from the GL Entry at submission time and they match the account
	currency (ILS for party accounts, JOD for bank/cash accounts).
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

	for row in data:
		if not isinstance(row, dict):
			continue
		account = row.get("account")
		if not account:
			continue

		currency = account_currency_map.get(account)
		if currency:
			row["account_currency"] = currency
			# Keep transaction_currency in sync with account_currency so the
			# standard "Add Columns in Transaction Currency" columns display the
			# correct per-row symbol matching ERPNext default behaviour.
			row["transaction_currency"] = currency
