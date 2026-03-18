# Copyright (c) 2026, erpcloud.systems and contributors
# See license.txt
"""
Unit tests for party_statement_utils.py and the Advanced Customer/Supplier
Statement Script Reports.

Tests cover:
  * _to_list helper normalisation
  * validate_filters raises on missing/invalid inputs
  * get_columns returns correct structure (with/without company currency)
  * build_report_data:
    - single party with opening balance
    - period transactions with correct running balance
    - closing balance row
    - multi-currency scenario
    - empty dataset returns empty list

All frappe DB calls are replaced with unittest.mock so no live Frappe/ERPNext
instance is required.
"""

from __future__ import unicode_literals

import sys
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Bootstrap minimal frappe stub
# ---------------------------------------------------------------------------

def _make_frappe_stub():
    mod = types.ModuleType("frappe")
    mod.db = MagicMock()
    mod._ = lambda s, *a: s
    mod.whitelist = lambda fn=None, **kw: (fn if fn else lambda f: f)
    mod.log_error = MagicMock()
    mod.get_all = MagicMock(return_value=[])
    mod.get_cached_value = MagicMock(return_value=None)
    mod.throw = MagicMock(side_effect=Exception)

    class _ValidationError(Exception):
        pass
    mod.ValidationError = _ValidationError
    return mod


_frappe_stub = _make_frappe_stub()
sys.modules.setdefault("frappe", _frappe_stub)

_utils_mod = types.ModuleType("frappe.utils")

def _flt(val, precision=None):
    try:
        v = float(val or 0)
    except (TypeError, ValueError):
        v = 0.0
    if precision is not None:
        v = round(v, precision)
    return v

_utils_mod.flt = _flt
_utils_mod.getdate = MagicMock(side_effect=lambda d: d)
_utils_mod.nowdate = MagicMock(return_value="2026-01-01")
sys.modules["frappe.utils"] = _utils_mod

# Stub erpnext sub-modules to avoid ImportError
for _m in [
    "erpnext",
    "erpnext.accounts",
    "erpnext.accounts.utils",
]:
    sys.modules.setdefault(_m, types.ModuleType(_m))

# Import the module under test
from ecs_cheques.ecs_cheques.report.party_statement_utils import (  # noqa: E402
    LABEL_CLOSING,
    LABEL_OPENING,
    _to_list,
    validate_filters,
    get_columns,
    build_report_data,
)
from ecs_cheques.ecs_cheques.report.advanced_customer_statement.advanced_customer_statement import (  # noqa: E402
    execute as customer_execute,
)
from ecs_cheques.ecs_cheques.report.advanced_supplier_statement.advanced_supplier_statement import (  # noqa: E402
    execute as supplier_execute,
)

import frappe  # the stub registered above


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Dict(dict):
    """dict with attribute-style access (mirrors frappe._dict)."""
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            return None
    def __setattr__(self, k, v):
        self[k] = v


def _filters(**kw):
    base = {"company": "Test Co", "from_date": "2026-01-01", "to_date": "2026-01-31"}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Tests for _to_list
# ---------------------------------------------------------------------------

class TestToList(unittest.TestCase):

    def test_none_returns_empty(self):
        from ecs_cheques.ecs_cheques.report.party_statement_utils import _to_list
        self.assertEqual(_to_list(None), [])

    def test_empty_string_returns_empty(self):
        from ecs_cheques.ecs_cheques.report.party_statement_utils import _to_list
        self.assertEqual(_to_list(""), [])

    def test_list_passthrough(self):
        from ecs_cheques.ecs_cheques.report.party_statement_utils import _to_list
        self.assertEqual(_to_list(["a", "b"]), ["a", "b"])

    def test_list_drops_empty_strings(self):
        from ecs_cheques.ecs_cheques.report.party_statement_utils import _to_list
        self.assertEqual(_to_list(["a", "", "b"]), ["a", "b"])

    def test_comma_separated_string(self):
        from ecs_cheques.ecs_cheques.report.party_statement_utils import _to_list
        self.assertEqual(_to_list("ACME, Widgets Inc."), ["ACME", "Widgets Inc."])

    def test_tuple_passthrough(self):
        from ecs_cheques.ecs_cheques.report.party_statement_utils import _to_list
        self.assertEqual(_to_list(("a", "b")), ["a", "b"])


# ---------------------------------------------------------------------------
# Tests for validate_filters
# ---------------------------------------------------------------------------

class TestValidateFilters(unittest.TestCase):

    def test_missing_company_raises(self):
        with self.assertRaises(Exception):
            validate_filters({"from_date": "2026-01-01", "to_date": "2026-01-31"}, "Customer")

    def test_missing_from_date_raises(self):
        with self.assertRaises(Exception):
            validate_filters({"company": "X", "to_date": "2026-01-31"}, "Customer")

    def test_from_date_after_to_date_raises(self):
        # getdate is identity-stubbed so comparison uses the strings directly.
        with self.assertRaises(Exception):
            validate_filters(
                {"company": "X", "from_date": "2026-02-01", "to_date": "2026-01-01"},
                "Customer",
            )

    def test_valid_filters_does_not_raise(self):
        # Should not raise
        validate_filters(_filters(), "Customer")


# ---------------------------------------------------------------------------
# Tests for get_columns
# ---------------------------------------------------------------------------

class TestGetColumns(unittest.TestCase):

    def setUp(self):
        frappe.db.get_value = MagicMock(return_value="USD")

    def test_standard_columns_present(self):
        cols = get_columns(_filters(), "Customer")
        fieldnames = [c["fieldname"] for c in cols]
        for fn in [
            "posting_date", "voucher_type", "voucher_no",
            "party", "account", "remarks",
            "debit_in_account_currency", "credit_in_account_currency",
            "balance_in_account_currency",
        ]:
            self.assertIn(fn, fieldnames, f"Missing column: {fn}")

    def test_company_currency_columns_absent_by_default(self):
        cols = get_columns(_filters(), "Customer")
        fieldnames = [c["fieldname"] for c in cols]
        self.assertNotIn("debit", fieldnames)
        self.assertNotIn("credit", fieldnames)
        self.assertNotIn("balance_in_company_currency", fieldnames)

    def test_company_currency_columns_present_when_flag_set(self):
        cols = get_columns(_filters(show_in_company_currency=1), "Customer")
        fieldnames = [c["fieldname"] for c in cols]
        self.assertIn("debit", fieldnames)
        self.assertIn("credit", fieldnames)
        self.assertIn("balance_in_company_currency", fieldnames)

    def test_company_currency_label_includes_currency_code(self):
        frappe.db.get_value = MagicMock(return_value="JOD")
        cols = get_columns(_filters(show_in_company_currency=1, company="ACME"), "Customer")
        debit_col = next(c for c in cols if c["fieldname"] == "debit")
        self.assertIn("JOD", debit_col["label"])

    def test_supplier_party_column_links_to_supplier(self):
        cols = get_columns(_filters(), "Supplier")
        party_col = next(c for c in cols if c["fieldname"] == "party")
        self.assertEqual(party_col["options"], "Supplier")

    def test_customer_party_column_links_to_customer(self):
        cols = get_columns(_filters(), "Customer")
        party_col = next(c for c in cols if c["fieldname"] == "party")
        self.assertEqual(party_col["options"], "Customer")


# ---------------------------------------------------------------------------
# Tests for build_report_data
# ---------------------------------------------------------------------------

class TestBuildReportData(unittest.TestCase):

    def setUp(self):
        frappe.db.get_value = MagicMock(return_value="USD")
        # Default: empty opening balance and no transactions
        frappe.db.sql = MagicMock(return_value=[])

    def _ob_row(self, **kw):
        defaults = {
            "party": "CUST-001",
            "account": "Debtors - TC",
            "account_currency": "ILS",
            "opening_debit": 0.0,
            "opening_credit": 0.0,
            "opening_debit_company": 0.0,
            "opening_credit_company": 0.0,
        }
        defaults.update(kw)
        return _Dict(defaults)

    def _gl_row(self, **kw):
        defaults = {
            "posting_date": "2026-01-15",
            "voucher_type": "Payment Entry",
            "voucher_no": "PE-0001",
            "party": "CUST-001",
            "account": "Debtors - TC",
            "remarks": "Invoice payment",
            "debit_in_account_currency": 0.0,
            "credit_in_account_currency": 0.0,
            "debit": 0.0,
            "credit": 0.0,
            "account_currency": "ILS",
        }
        defaults.update(kw)
        return _Dict(defaults)

    # ── Empty dataset ────────────────────────────────────────────────────────

    def test_empty_data_returns_empty_list(self):
        result = build_report_data(_filters(), "Customer")
        self.assertEqual(result, [])

    # ── Opening balance rows ─────────────────────────────────────────────────

    def test_opening_balance_row_emitted(self):
        ob = self._ob_row(opening_debit=1000.0, opening_credit=200.0)
        frappe.db.sql = MagicMock(side_effect=[
            [ob],   # opening balance query
            [],     # transactions query
        ])
        data = build_report_data(_filters(), "Customer")
        self.assertTrue(any(r.get("remarks") == LABEL_OPENING for r in data))

    def test_opening_balance_calculated_correctly(self):
        ob = self._ob_row(opening_debit=1000.0, opening_credit=300.0)
        frappe.db.sql = MagicMock(side_effect=[[ob], []])
        data = build_report_data(_filters(), "Customer")
        ob_row = next(r for r in data if r.get("remarks") == LABEL_OPENING)
        self.assertAlmostEqual(ob_row["balance_in_account_currency"], 700.0)

    def test_opening_balance_row_type_is_opening(self):
        ob = self._ob_row(opening_debit=500.0, opening_credit=0.0)
        frappe.db.sql = MagicMock(side_effect=[[ob], []])
        data = build_report_data(_filters(), "Customer")
        ob_row = next(r for r in data if r.get("remarks") == LABEL_OPENING)
        self.assertEqual(ob_row["row_type"], "opening")

    # ── Transaction rows ─────────────────────────────────────────────────────

    def test_transaction_row_included_in_output(self):
        gle = self._gl_row(debit_in_account_currency=500.0, debit=250.0)
        frappe.db.sql = MagicMock(side_effect=[[], [gle]])
        data = build_report_data(_filters(), "Customer")
        txn_rows = [r for r in data if r.get("row_type") == "entry"]
        self.assertEqual(len(txn_rows), 1)

    def test_running_balance_accumulates(self):
        """Two debit entries should sum in the running balance."""
        gle1 = self._gl_row(debit_in_account_currency=500.0)
        gle2 = self._gl_row(debit_in_account_currency=300.0, voucher_no="PE-0002")
        frappe.db.sql = MagicMock(side_effect=[[], [gle1, gle2]])
        data = build_report_data(_filters(), "Customer")
        entries = [r for r in data if r.get("row_type") == "entry"]
        self.assertAlmostEqual(entries[0]["balance_in_account_currency"], 500.0)
        self.assertAlmostEqual(entries[1]["balance_in_account_currency"], 800.0)

    def test_running_balance_credits_reduce_balance(self):
        gle1 = self._gl_row(debit_in_account_currency=1000.0)
        gle2 = self._gl_row(credit_in_account_currency=400.0, voucher_no="JE-0001")
        frappe.db.sql = MagicMock(side_effect=[[], [gle1, gle2]])
        data = build_report_data(_filters(), "Customer")
        entries = [r for r in data if r.get("row_type") == "entry"]
        self.assertAlmostEqual(entries[1]["balance_in_account_currency"], 600.0)

    def test_opening_balance_carries_into_first_transaction(self):
        ob = self._ob_row(opening_debit=1000.0, opening_credit=0.0)
        gle = self._gl_row(debit_in_account_currency=200.0)
        frappe.db.sql = MagicMock(side_effect=[[ob], [gle]])
        data = build_report_data(_filters(), "Customer")
        entry = next(r for r in data if r.get("row_type") == "entry")
        self.assertAlmostEqual(entry["balance_in_account_currency"], 1200.0)

    # ── Closing balance row ──────────────────────────────────────────────────

    def test_closing_balance_row_emitted(self):
        gle = self._gl_row(debit_in_account_currency=500.0)
        frappe.db.sql = MagicMock(side_effect=[[], [gle]])
        data = build_report_data(_filters(), "Customer")
        self.assertTrue(any(r.get("remarks") == LABEL_CLOSING for r in data))

    def test_closing_balance_is_last_row(self):
        gle = self._gl_row(debit_in_account_currency=500.0)
        frappe.db.sql = MagicMock(side_effect=[[], [gle]])
        data = build_report_data(_filters(), "Customer")
        self.assertEqual(data[-1]["remarks"], LABEL_CLOSING)

    def test_closing_balance_row_type_is_closing(self):
        gle = self._gl_row(debit_in_account_currency=500.0)
        frappe.db.sql = MagicMock(side_effect=[[], [gle]])
        data = build_report_data(_filters(), "Customer")
        self.assertEqual(data[-1]["row_type"], "closing")

    def test_closing_balance_equals_sum_of_net_movements(self):
        ob = self._ob_row(opening_debit=1000.0, opening_credit=200.0)
        gle1 = self._gl_row(debit_in_account_currency=500.0)
        gle2 = self._gl_row(credit_in_account_currency=100.0, voucher_no="JE-0001")
        frappe.db.sql = MagicMock(side_effect=[[ob], [gle1, gle2]])
        data = build_report_data(_filters(), "Customer")
        closing = data[-1]
        # opening = 800, +500, -100 → 1200
        self.assertAlmostEqual(closing["balance_in_account_currency"], 1200.0)

    # ── Company currency columns ─────────────────────────────────────────────

    def test_company_currency_columns_absent_without_flag(self):
        gle = self._gl_row(debit_in_account_currency=500.0, debit=250.0)
        frappe.db.sql = MagicMock(side_effect=[[], [gle]])
        data = build_report_data(_filters(), "Customer")
        entry = next(r for r in data if r.get("row_type") == "entry")
        self.assertNotIn("balance_in_company_currency", entry)

    def test_company_currency_columns_present_with_flag(self):
        gle = self._gl_row(debit_in_account_currency=500.0, debit=250.0)
        frappe.db.sql = MagicMock(side_effect=[[], [gle]])
        data = build_report_data(_filters(show_in_company_currency=1), "Customer")
        entry = next(r for r in data if r.get("row_type") == "entry")
        self.assertIn("balance_in_company_currency", entry)

    def test_company_currency_running_balance(self):
        gle1 = self._gl_row(debit_in_account_currency=100.0, debit=50.0)
        gle2 = self._gl_row(debit_in_account_currency=100.0, debit=50.0, voucher_no="PE-0002")
        frappe.db.sql = MagicMock(side_effect=[[], [gle1, gle2]])
        data = build_report_data(_filters(show_in_company_currency=1), "Customer")
        entries = [r for r in data if r.get("row_type") == "entry"]
        self.assertAlmostEqual(entries[1]["balance_in_company_currency"], 100.0)

    # ── Supplier party type ──────────────────────────────────────────────────

    def test_supplier_execute_returns_columns_and_data(self):
        frappe.db.get_value = MagicMock(return_value="USD")
        frappe.db.sql = MagicMock(return_value=[])
        columns, data = supplier_execute(_filters())
        self.assertIsInstance(columns, list)
        self.assertIsInstance(data, list)

    def test_customer_execute_returns_columns_and_data(self):
        frappe.db.get_value = MagicMock(return_value="USD")
        frappe.db.sql = MagicMock(return_value=[])
        columns, data = customer_execute(_filters())
        self.assertIsInstance(columns, list)
        self.assertIsInstance(data, list)

    # ── Multi-currency scenario ──────────────────────────────────────────────

    def test_multi_currency_separate_running_balances(self):
        """Rows in different account currencies maintain separate balances."""
        gle_ils = self._gl_row(
            debit_in_account_currency=1000.0, account_currency="ILS"
        )
        gle_usd = self._gl_row(
            debit_in_account_currency=500.0,
            account_currency="USD",
            account="AR-USD - TC",
            voucher_no="PE-0002",
        )
        frappe.db.sql = MagicMock(side_effect=[[], [gle_ils, gle_usd]])
        data = build_report_data(_filters(), "Customer")
        entries = [r for r in data if r.get("row_type") == "entry"]
        # ILS balance = 1000, USD balance = 500 – they must NOT be mixed
        ils_entry = next(r for r in entries if r["account_currency"] == "ILS")
        usd_entry = next(r for r in entries if r["account_currency"] == "USD")
        self.assertAlmostEqual(ils_entry["balance_in_account_currency"], 1000.0)
        self.assertAlmostEqual(usd_entry["balance_in_account_currency"], 500.0)


if __name__ == "__main__":
    unittest.main()
