# Copyright (c) 2021, erpcloud.systems and Contributors
# See license.txt
"""
Unit tests for the multi-currency audit layer in general_ledger.py.

These tests verify the Phase 1–7 enhancements:
  * Phase 1 – new column definitions present in _PAYMENT_CURRENCY_COLUMNS
  * Phase 2 – source_debit_jod / source_credit_jod use payment currency
               (JOD) for BOTH GL sides of a Payment Entry
  * Phase 3 – transaction_currency and debit/credit_in_account_currency
               overridden to payment currency for all PE rows
  * Phase 4 – jod_to_usd_rate and jod_to_ils_rate populated
  * Phase 5 – rate_mismatch_warning populated when rates differ;
               missing rate handled gracefully
  * Phase 6 – exchange-rate cache is shared across rows of the same PE
  * Phase 7 – multiple_cheque_reference populated from Cheque Table Receive

Tests run with Python's built-in unittest and use unittest.mock to replace
frappe DB calls – no live Frappe/ERPNext instance is required.
"""

from __future__ import unicode_literals
import sys
import types
import unittest
from unittest.mock import MagicMock, call, patch


# ---------------------------------------------------------------------------
# Bootstrap a minimal frappe stub so the module under test can be imported
# without a running Frappe instance.
# ---------------------------------------------------------------------------

def _make_frappe_stub():
    frappe_mod = types.ModuleType("frappe")
    frappe_mod.db = MagicMock()
    frappe_mod._ = lambda s, *a: s
    frappe_mod.whitelist = lambda fn=None, **kw: (fn if fn else lambda f: f)
    frappe_mod.log_error = MagicMock()
    frappe_mod.get_all = MagicMock(return_value=[])
    frappe_mod.get_cached_value = MagicMock()

    class _ValidationError(Exception):
        pass

    frappe_mod.ValidationError = _ValidationError
    return frappe_mod


_frappe_stub = _make_frappe_stub()
sys.modules.setdefault("frappe", _frappe_stub)

_utils_mod = types.ModuleType("frappe.utils")


def _flt(val, precision=None):
    """Minimal float helper mimicking frappe.utils.flt."""
    try:
        v = float(val or 0)
    except (TypeError, ValueError):
        v = 0.0
    if precision is not None:
        v = round(v, precision)
    return v


_utils_mod.flt = _flt
_utils_mod.getdate = MagicMock(side_effect=lambda d: d)
_utils_mod.nowdate = MagicMock(return_value="2024-01-01")
sys.modules["frappe.utils"] = _utils_mod

# Now we can import the module under test.
from ecs_cheques.ecs_cheques.overrides.general_ledger.general_ledger import (  # noqa: E402
    _add_payment_currency_data,
    _fix_account_currency_per_row,
    _fetch_multiple_cheque_references,
    _validate_exchange_rate,
    _inject_payment_currency_columns,
    _PAYMENT_CURRENCY_COLUMNS,
    _AUDIT_COLUMNS,
)

import frappe  # the stub we just registered


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Dict(dict):
    """dict subclass with attribute access (mirrors frappe._dict)."""
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            return None

    def __setattr__(self, key, value):
        self[key] = value


def _make_pe(**kwargs):
    """Return a minimal Payment Entry _Dict for use in frappe.get_all stubs."""
    return _Dict(
        name=kwargs.get("name", "PE-001"),
        paid_from=kwargs.get("paid_from", "Customer AR - ILS"),
        paid_to=kwargs.get("paid_to", "Bank - JOD"),
        paid_from_account_currency=kwargs.get("paid_from_account_currency", "ILS"),
        paid_to_account_currency=kwargs.get("paid_to_account_currency", "JOD"),
        paid_amount=kwargs.get("paid_amount", 500.0),       # ILS
        received_amount=kwargs.get("received_amount", 100.0),  # JOD
        source_exchange_rate=kwargs.get("source_exchange_rate", 1.0),
        target_exchange_rate=kwargs.get("target_exchange_rate", 0.7092),  # JOD→USD≈1.41
        posting_date=kwargs.get("posting_date", "2024-01-15"),
    )


def _make_row(**kwargs):
    """Return a minimal GL data row dict."""
    return {
        "voucher_type": kwargs.get("voucher_type", "Payment Entry"),
        "voucher_no": kwargs.get("voucher_no", "PE-001"),
        "account": kwargs.get("account", "Bank - JOD"),
        "debit": kwargs.get("debit", 0.0),
        "credit": kwargs.get("credit", 0.0),
        "company": kwargs.get("company", "Test Company"),
    }


def _setup_frappe_get_all(pe_list=None, account_list=None, mce_list=None):
    """Configure frappe.get_all to return appropriate data per doctype."""
    pe_list = pe_list or []
    account_list = account_list or []
    mce_list = mce_list or []

    def _get_all(doctype, filters=None, fields=None, **kw):
        if doctype == "Payment Entry":
            return pe_list
        if doctype == "Account":
            return account_list
        if doctype == "Cheque Table Receive":
            return mce_list
        return []

    frappe.get_all = MagicMock(side_effect=_get_all)


# ---------------------------------------------------------------------------
# Phase 1 – Column definitions
# ---------------------------------------------------------------------------

class TestColumnDefinitions(unittest.TestCase):
    """Phase 1: verify all required columns are present in _PAYMENT_CURRENCY_COLUMNS."""

    required_fieldnames = {
        # Legacy
        "debit_in_payment_currency",
        "credit_in_payment_currency",
        "debit_in_party_currency",
        "credit_in_party_currency",
        # New primary payment columns
        "source_debit_jod",
        "source_credit_jod",
        # New party columns
        "party_debit_ils",
        "party_credit_ils",
        # Rate columns
        "jod_to_usd_rate",
        "jod_to_ils_rate",
        # Validation
        "rate_mismatch_warning",
        # Traceability
        "payment_entry_reference",
        "multiple_cheque_reference",
    }

    def test_all_required_columns_present(self):
        fieldnames = {col["fieldname"] for col in _PAYMENT_CURRENCY_COLUMNS}
        for name in self.required_fieldnames:
            self.assertIn(name, fieldnames, f"Missing column: {name}")

    def test_source_debit_jod_uses_payment_currency_option(self):
        col = next(c for c in _AUDIT_COLUMNS if c["fieldname"] == "source_debit_jod")
        self.assertEqual(col.get("options"), "payment_currency")
        self.assertEqual(col["fieldtype"], "Currency")

    def test_jod_to_usd_rate_is_float(self):
        col = next(c for c in _AUDIT_COLUMNS if c["fieldname"] == "jod_to_usd_rate")
        self.assertEqual(col["fieldtype"], "Float")

    def test_payment_entry_reference_is_link(self):
        col = next(c for c in _AUDIT_COLUMNS if c["fieldname"] == "payment_entry_reference")
        self.assertEqual(col["fieldtype"], "Link")
        self.assertEqual(col.get("options"), "Payment Entry")

    def test_multiple_cheque_reference_is_link(self):
        col = next(c for c in _AUDIT_COLUMNS if c["fieldname"] == "multiple_cheque_reference")
        self.assertEqual(col["fieldtype"], "Link")
        self.assertEqual(col.get("options"), "Multiple Cheque Entry")

    def test_inject_does_not_duplicate_columns(self):
        columns = list(_PAYMENT_CURRENCY_COLUMNS)
        # Inject once.
        result1 = _inject_payment_currency_columns(columns)
        # Inject again (simulates two calls in same process).
        result2 = _inject_payment_currency_columns(result1)
        fieldnames1 = [c["fieldname"] for c in result1]
        fieldnames2 = [c["fieldname"] for c in result2]
        self.assertEqual(len(fieldnames1), len(set(fieldnames1)), "Duplicate columns after first inject")
        self.assertEqual(len(fieldnames2), len(set(fieldnames2)), "Duplicate columns after second inject")


# ---------------------------------------------------------------------------
# Phase 2 – source_debit_jod / source_credit_jod on BOTH GL sides
# ---------------------------------------------------------------------------

class TestSourceJodColumns(unittest.TestCase):
    """Phase 2: JOD shown as primary currency on BOTH bank and party rows."""

    def _run(self, bank_row, party_row, pe):
        _setup_frappe_get_all(
            pe_list=[pe],
            account_list=[
                _Dict(name="Bank - JOD", account_currency="JOD"),
                _Dict(name="Customer AR - ILS", account_currency="ILS"),
            ],
        )
        # No MCE links for these tests.
        data = [bank_row, party_row]
        # Patch exchange-rate fetch to return known rates.
        with patch(
            "ecs_cheques.ecs_cheques.overrides.general_ledger.general_ledger._fetch_exchange_rate",
            return_value=5.0,  # 1 JOD = 5 ILS
        ):
            _add_payment_currency_data(data)
        return bank_row, party_row

    def test_bank_row_source_debit_jod_equals_received_amount(self):
        pe = _make_pe(received_amount=100.0, paid_amount=500.0)
        bank_row = _make_row(account="Bank - JOD", debit=141.0, credit=0.0)
        party_row = _make_row(account="Customer AR - ILS", debit=0.0, credit=141.0)
        bank_row, party_row = self._run(bank_row, party_row, pe)

        self.assertEqual(bank_row["source_debit_jod"], 100.0)
        self.assertEqual(bank_row["source_credit_jod"], 0.0)

    def test_party_row_source_credit_jod_equals_received_amount(self):
        """Phase 2 key requirement: party row must also show JOD (not ILS)."""
        pe = _make_pe(received_amount=100.0, paid_amount=500.0)
        bank_row = _make_row(account="Bank - JOD", debit=141.0, credit=0.0)
        party_row = _make_row(account="Customer AR - ILS", debit=0.0, credit=141.0)
        bank_row, party_row = self._run(bank_row, party_row, pe)

        # Both sides show the SAME JOD amount.
        self.assertEqual(party_row["source_credit_jod"], 100.0)
        self.assertEqual(party_row["source_debit_jod"], 0.0)

    def test_both_sides_same_jod_amount(self):
        """source_debit_jod on bank == source_credit_jod on party."""
        pe = _make_pe(received_amount=250.0)
        bank_row = _make_row(account="Bank - JOD", debit=353.0, credit=0.0)
        party_row = _make_row(account="Customer AR - ILS", debit=0.0, credit=353.0)
        bank_row, party_row = self._run(bank_row, party_row, pe)

        jod_bank = bank_row["source_debit_jod"]
        jod_party = party_row["source_credit_jod"]
        self.assertEqual(jod_bank, jod_party, "JOD amount must be identical on both sides")

    def test_jod_fallback_from_company_amount_when_received_amount_zero(self):
        """When received_amount is 0, JOD is derived from company currency / target rate."""
        pe = _make_pe(
            received_amount=0.0,
            paid_amount=0.0,
            target_exchange_rate=1.41,  # 1 JOD = 1.41 USD
        )
        bank_row = _make_row(account="Bank - JOD", debit=141.0, credit=0.0)
        party_row = _make_row(account="Customer AR - ILS", debit=0.0, credit=141.0)
        bank_row, party_row = self._run(bank_row, party_row, pe)

        # 141 USD / 1.41 ≈ 100 JOD
        self.assertAlmostEqual(bank_row["source_debit_jod"], 100.0, places=3)
        self.assertAlmostEqual(party_row["source_credit_jod"], 100.0, places=3)


# ---------------------------------------------------------------------------
# Phase 1 – party_debit_ils / party_credit_ils
# ---------------------------------------------------------------------------

class TestPartyILSColumns(unittest.TestCase):
    """Phase 1: ILS amounts computed from JOD × jod_to_ils_rate."""

    def _run(self, data, pe, ils_rate=5.0):
        _setup_frappe_get_all(pe_list=[pe])
        with patch(
            "ecs_cheques.ecs_cheques.overrides.general_ledger.general_ledger._fetch_exchange_rate",
            return_value=ils_rate,
        ):
            _add_payment_currency_data(data)

    def test_party_credit_ils_equals_jod_times_rate(self):
        pe = _make_pe(received_amount=100.0, paid_amount=500.0)
        party_row = _make_row(account="Customer AR - ILS", debit=0.0, credit=141.0)
        self._run([party_row], pe, ils_rate=5.0)
        # source_credit_jod = 100, rate = 5 → ILS credit = 500
        self.assertAlmostEqual(party_row["party_credit_ils"], 500.0, places=3)
        self.assertEqual(party_row["party_debit_ils"], 0.0)

    def test_party_debit_ils_on_bank_row(self):
        pe = _make_pe(received_amount=100.0)
        bank_row = _make_row(account="Bank - JOD", debit=141.0, credit=0.0)
        self._run([bank_row], pe, ils_rate=5.0)
        # source_debit_jod = 100, rate = 5 → ILS debit = 500
        self.assertAlmostEqual(bank_row["party_debit_ils"], 500.0, places=3)
        self.assertEqual(bank_row["party_credit_ils"], 0.0)

    def test_same_currency_no_conversion(self):
        pe = _make_pe(
            paid_from_account_currency="JOD",
            paid_to_account_currency="JOD",
            received_amount=100.0,
        )
        row = _make_row(account="Bank - JOD", debit=141.0, credit=0.0)
        _setup_frappe_get_all(pe_list=[pe])
        with patch(
            "ecs_cheques.ecs_cheques.overrides.general_ledger.general_ledger._fetch_exchange_rate",
        ) as mock_rate:
            _add_payment_currency_data([row])
        # When payment_currency == party_currency, rate = 1 (no DB lookup needed).
        self.assertEqual(row["party_debit_ils"], row["source_debit_jod"])

    def test_missing_ils_rate_logs_error_and_blanks_columns(self):
        pe = _make_pe(received_amount=100.0)
        party_row = _make_row(account="Customer AR - ILS", debit=0.0, credit=141.0)
        _setup_frappe_get_all(pe_list=[pe])
        frappe.log_error = MagicMock()
        with patch(
            "ecs_cheques.ecs_cheques.overrides.general_ledger.general_ledger._fetch_exchange_rate",
            return_value=None,  # simulate missing rate
        ):
            _add_payment_currency_data([party_row])
        frappe.log_error.assert_called_once()
        self.assertEqual(party_row["party_debit_ils"], 0.0)
        self.assertEqual(party_row["party_credit_ils"], 0.0)


# ---------------------------------------------------------------------------
# Phase 4 – jod_to_usd_rate and jod_to_ils_rate
# ---------------------------------------------------------------------------

class TestRateColumns(unittest.TestCase):
    """Phase 4: cross-currency rate columns."""

    def _run_with_rate(self, row, pe, fetch_rate=5.0):
        _setup_frappe_get_all(pe_list=[pe])
        with patch(
            "ecs_cheques.ecs_cheques.overrides.general_ledger.general_ledger._fetch_exchange_rate",
            return_value=fetch_rate,
        ):
            _add_payment_currency_data([row])

    def test_jod_to_ils_rate_populated(self):
        pe = _make_pe(received_amount=100.0)
        row = _make_row(account="Customer AR - ILS", debit=0.0, credit=141.0)
        self._run_with_rate(row, pe, fetch_rate=5.0)
        self.assertAlmostEqual(row["jod_to_ils_rate"], 5.0, places=3)

    def test_jod_to_usd_rate_from_target_exchange_rate(self):
        pe = _make_pe(received_amount=100.0, target_exchange_rate=1.41)
        row = _make_row(account="Bank - JOD", debit=141.0, credit=0.0)
        self._run_with_rate(row, pe, fetch_rate=5.0)
        self.assertAlmostEqual(row["jod_to_usd_rate"], 1.41, places=3)

    def test_same_currency_ils_rate_is_one(self):
        pe = _make_pe(
            paid_from_account_currency="JOD",
            paid_to_account_currency="JOD",
            received_amount=100.0,
        )
        row = _make_row(account="Bank - JOD", debit=100.0, credit=0.0)
        _setup_frappe_get_all(pe_list=[pe])
        with patch(
            "ecs_cheques.ecs_cheques.overrides.general_ledger.general_ledger._fetch_exchange_rate",
        ):
            _add_payment_currency_data([row])
        # Same currency → rate_jod_to_ils = 1.0
        self.assertEqual(row["jod_to_ils_rate"], 1.0)


# ---------------------------------------------------------------------------
# Phase 5 – rate_mismatch_warning
# ---------------------------------------------------------------------------

class TestRateMismatchWarning(unittest.TestCase):
    """Phase 5: warning column when derived rate differs from Currency Exchange."""

    def test_no_warning_when_rates_match(self):
        cache = {}
        with patch(
            "ecs_cheques.ecs_cheques.overrides.general_ledger.general_ledger._fetch_exchange_rate",
            return_value=5.0,
        ):
            warning = _validate_exchange_rate("JOD", "ILS", 5.0, "2024-01-15", cache)
        self.assertEqual(warning, "")

    def test_warning_when_rates_differ_significantly(self):
        cache = {}
        with patch(
            "ecs_cheques.ecs_cheques.overrides.general_ledger.general_ledger._fetch_exchange_rate",
            return_value=5.0,
        ):
            # Derived rate = 4.0, reference = 5.0 → 20% mismatch
            warning = _validate_exchange_rate("JOD", "ILS", 4.0, "2024-01-15", cache)
        self.assertIn("RATE_MISMATCH", warning)

    def test_no_warning_same_currency(self):
        cache = {}
        warning = _validate_exchange_rate("JOD", "JOD", 1.0, "2024-01-15", cache)
        self.assertEqual(warning, "")

    def test_missing_derived_rate_returns_warning(self):
        cache = {}
        warning = _validate_exchange_rate("JOD", "ILS", None, "2024-01-15", cache)
        self.assertIn("MISSING_RATE", warning)

    def test_zero_derived_rate_returns_warning(self):
        cache = {}
        with patch(
            "ecs_cheques.ecs_cheques.overrides.general_ledger.general_ledger._fetch_exchange_rate",
            return_value=5.0,
        ):
            warning = _validate_exchange_rate("JOD", "ILS", 0.0, "2024-01-15", cache)
        self.assertIn("MISSING_RATE", warning)

    def test_zero_reference_rate_returns_warning(self):
        cache = {}
        with patch(
            "ecs_cheques.ecs_cheques.overrides.general_ledger.general_ledger._fetch_exchange_rate",
            return_value=0.0,
        ):
            warning = _validate_exchange_rate("JOD", "ILS", 5.0, "2024-01-15", cache)
        self.assertIn("ZERO_REF_RATE", warning)

    def test_missing_reference_returns_empty(self):
        """When no Currency Exchange record exists we cannot validate – no warning."""
        cache = {}
        with patch(
            "ecs_cheques.ecs_cheques.overrides.general_ledger.general_ledger._fetch_exchange_rate",
            return_value=None,
        ):
            warning = _validate_exchange_rate("JOD", "ILS", 5.0, "2024-01-15", cache)
        self.assertEqual(warning, "")

    def test_rate_mismatch_warning_set_on_gl_row(self):
        """End-to-end: rate_mismatch_warning is populated on the data row."""
        pe = _make_pe(received_amount=100.0)
        row = _make_row(account="Bank - JOD", debit=141.0, credit=0.0)
        _setup_frappe_get_all(pe_list=[pe])
        # derived rate will be 5.0 (from _fetch_exchange_rate mock),
        # but PE target_exchange_rate = 0.7092 so jod_to_usd comes from PE.
        with patch(
            "ecs_cheques.ecs_cheques.overrides.general_ledger.general_ledger._fetch_exchange_rate",
            return_value=5.0,
        ):
            _add_payment_currency_data([row])
        # Warning field must exist (value depends on rates).
        self.assertIn("rate_mismatch_warning", row)


# ---------------------------------------------------------------------------
# Phase 6 – Exchange-rate caching
# ---------------------------------------------------------------------------

class TestExchangeRateCaching(unittest.TestCase):
    """Phase 6: verify that _fetch_exchange_rate is not called per row."""

    def test_rate_fetched_once_for_multiple_rows_same_pe(self):
        pe = _make_pe(received_amount=100.0, name="PE-CACHE")
        rows = [
            _make_row(account="Bank - JOD", debit=141.0, credit=0.0, voucher_no="PE-CACHE"),
            _make_row(account="Customer AR - ILS", debit=0.0, credit=141.0, voucher_no="PE-CACHE"),
        ]
        _setup_frappe_get_all(pe_list=[pe])
        with patch(
            "ecs_cheques.ecs_cheques.overrides.general_ledger.general_ledger._fetch_exchange_rate",
            return_value=5.0,
        ) as mock_fetch:
            _add_payment_currency_data(rows)
        # _fetch_exchange_rate should be called at most once per
        # (from, to, date) pair across all rows.
        call_args = [c.args[:2] for c in mock_fetch.call_args_list]
        unique_pairs = set(call_args)
        self.assertEqual(len(call_args), len(unique_pairs),
                         f"Duplicate rate fetch calls: {mock_fetch.call_args_list}")


# ---------------------------------------------------------------------------
# Phase 7 – multiple_cheque_reference
# ---------------------------------------------------------------------------

class TestMultipleChequeReference(unittest.TestCase):
    """Phase 7: multiple_cheque_reference populated from Cheque Table Receive."""

    def test_reference_populated_when_mce_exists(self):
        pe = _make_pe(received_amount=100.0, name="PE-MCE-001")
        row = _make_row(account="Bank - JOD", debit=141.0, credit=0.0, voucher_no="PE-MCE-001")
        mce_row = _Dict(payment_entry="PE-MCE-001", parent="MCE-001")

        def _get_all(doctype, filters=None, fields=None, **kw):
            if doctype == "Payment Entry":
                return [pe]
            if doctype == "Cheque Table Receive":
                return [mce_row]
            return []

        frappe.get_all = MagicMock(side_effect=_get_all)
        with patch(
            "ecs_cheques.ecs_cheques.overrides.general_ledger.general_ledger._fetch_exchange_rate",
            return_value=5.0,
        ):
            _add_payment_currency_data([row])
        self.assertEqual(row["multiple_cheque_reference"], "MCE-001")

    def test_reference_empty_when_no_mce(self):
        pe = _make_pe(received_amount=100.0, name="PE-DIRECT-001")
        row = _make_row(account="Bank - JOD", debit=141.0, credit=0.0, voucher_no="PE-DIRECT-001")

        def _get_all(doctype, filters=None, fields=None, **kw):
            if doctype == "Payment Entry":
                return [pe]
            return []

        frappe.get_all = MagicMock(side_effect=_get_all)
        with patch(
            "ecs_cheques.ecs_cheques.overrides.general_ledger.general_ledger._fetch_exchange_rate",
            return_value=5.0,
        ):
            _add_payment_currency_data([row])
        self.assertEqual(row["multiple_cheque_reference"], "")

    def test_payment_entry_reference_always_set(self):
        pe = _make_pe(received_amount=100.0, name="PE-REF-001")
        row = _make_row(account="Bank - JOD", debit=141.0, credit=0.0, voucher_no="PE-REF-001")
        _setup_frappe_get_all(pe_list=[pe])
        with patch(
            "ecs_cheques.ecs_cheques.overrides.general_ledger.general_ledger._fetch_exchange_rate",
            return_value=5.0,
        ):
            _add_payment_currency_data([row])
        self.assertEqual(row["payment_entry_reference"], "PE-REF-001")

    def test_fetch_multiple_cheque_references_returns_mapping(self):
        mce_row = _Dict(payment_entry="PE-001", parent="MCE-001")
        frappe.get_all = MagicMock(return_value=[mce_row])
        result = _fetch_multiple_cheque_references({"PE-001", "PE-002"})
        self.assertEqual(result, {"PE-001": "MCE-001"})

    def test_fetch_multiple_cheque_references_empty_input(self):
        frappe.get_all = MagicMock(return_value=[])
        result = _fetch_multiple_cheque_references(set())
        self.assertEqual(result, {})
        frappe.get_all.assert_not_called()


# ---------------------------------------------------------------------------
# _fix_account_currency_per_row – simplified behaviour (ERPNext default)
# ---------------------------------------------------------------------------

class TestFixAccountCurrencyPhase3(unittest.TestCase):
    """_fix_account_currency_per_row sets account_currency and
    transaction_currency from the Account master for every GL row.

    debit_in_account_currency and credit_in_account_currency are NOT
    overridden so that ERPNext default behaviour is preserved:
      * ILS account rows show 3000 ILS (the natural GL value)
      * JOD account rows show 1000 JOD (the natural GL value)
    """

    def _setup_and_run(self, bank_row, party_row):
        def _get_all(doctype, filters=None, fields=None, **kw):
            if doctype == "Account":
                return [
                    _Dict(name="Bank - JOD", account_currency="JOD"),
                    _Dict(name="Customer AR - ILS", account_currency="ILS"),
                ]
            return []
        frappe.get_all = MagicMock(side_effect=_get_all)
        _fix_account_currency_per_row([bank_row, party_row])

    def test_bank_row_transaction_currency_is_jod(self):
        bank_row = _make_row(account="Bank - JOD", debit=141.0, credit=0.0)
        party_row = _make_row(account="Customer AR - ILS", debit=0.0, credit=141.0)
        self._setup_and_run(bank_row, party_row)
        self.assertEqual(bank_row.get("transaction_currency"), "JOD")

    def test_party_row_transaction_currency_is_party_currency(self):
        """Party row transaction_currency = ILS (account master currency)."""
        bank_row = _make_row(account="Bank - JOD", debit=141.0, credit=0.0)
        party_row = _make_row(account="Customer AR - ILS", debit=0.0, credit=141.0)
        self._setup_and_run(bank_row, party_row)
        self.assertEqual(party_row.get("transaction_currency"), "ILS")

    def test_party_row_account_currency_remains_ils(self):
        """account_currency must reflect the true Account master currency."""
        bank_row = _make_row(account="Bank - JOD", debit=141.0, credit=0.0)
        party_row = _make_row(account="Customer AR - ILS", debit=0.0, credit=141.0)
        self._setup_and_run(bank_row, party_row)
        self.assertEqual(party_row.get("account_currency"), "ILS")

    def test_debit_in_account_currency_not_modified(self):
        """debit_in_account_currency is NOT overridden – ERPNext provides it."""
        bank_row = _make_row(account="Bank - JOD", debit=141.0, credit=0.0)
        party_row = _make_row(account="Customer AR - ILS", debit=0.0, credit=141.0)
        self._setup_and_run(bank_row, party_row)
        # The function must not add these keys; ERPNext GL entry already
        # stores the correct account-currency amounts.
        self.assertNotIn("debit_in_account_currency", bank_row)
        self.assertNotIn("credit_in_account_currency", party_row)

    def test_non_pe_row_uses_account_master_currency(self):
        row = _make_row(
            voucher_type="Sales Invoice",
            voucher_no="SINV-001",
            account="Customer AR - ILS",
            credit=141.0,
        )

        def _get_all(doctype, filters=None, fields=None, **kw):
            if doctype == "Account":
                return [_Dict(name="Customer AR - ILS", account_currency="ILS")]
            return []

        frappe.get_all = MagicMock(side_effect=_get_all)
        _fix_account_currency_per_row([row])
        self.assertEqual(row.get("account_currency"), "ILS")
        self.assertEqual(row.get("transaction_currency"), "ILS")


# ---------------------------------------------------------------------------
# Edge cases and guards
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):
    """Misc. edge-case and guard tests."""

    def test_empty_data_is_harmless(self):
        frappe.get_all = MagicMock(return_value=[])
        _add_payment_currency_data([])  # must not raise
        _fix_account_currency_per_row([])  # must not raise

    def test_non_dict_rows_are_skipped(self):
        frappe.get_all = MagicMock(return_value=[])
        _add_payment_currency_data(["not a dict", 42, None])  # must not raise

    def test_non_pe_rows_not_modified_by_add_payment_currency_data(self):
        row = _make_row(voucher_type="Sales Invoice", voucher_no="SINV-001", credit=100.0)
        frappe.get_all = MagicMock(return_value=[])
        _add_payment_currency_data([row])
        # None of the new fields should be present since it's not a PE row.
        self.assertNotIn("source_debit_jod", row)
        self.assertNotIn("payment_entry_reference", row)

    def test_pe_row_with_zero_amounts(self):
        pe = _make_pe(received_amount=0.0, paid_amount=0.0, target_exchange_rate=0.0)
        row = _make_row(account="Bank - JOD", debit=0.0, credit=0.0)
        _setup_frappe_get_all(pe_list=[pe])
        with patch(
            "ecs_cheques.ecs_cheques.overrides.general_ledger.general_ledger._fetch_exchange_rate",
            return_value=5.0,
        ):
            _add_payment_currency_data([row])
        self.assertEqual(row["source_debit_jod"], 0.0)
        self.assertEqual(row["source_credit_jod"], 0.0)


if __name__ == "__main__":
    unittest.main()
