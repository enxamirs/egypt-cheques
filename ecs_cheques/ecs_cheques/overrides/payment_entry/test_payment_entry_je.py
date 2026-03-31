# Copyright (c) 2021, erpcloud.systems and Contributors
# See license.txt
"""
Unit tests for the Journal Entry creation helpers in payment_entry.py.

These tests verify the fix for the "Total Debit must equal Total Credit"
imbalance that occurred when a Payment Entry's exchange rate differed from
the Cheque Table Receive exchange rate, causing one side of the JE to use
the wrong base amount.

Tests are written to run with Python's built-in unittest and use
unittest.mock to replace frappe DB calls, so they do NOT require a live
Frappe/ERPNext instance.
"""

from __future__ import unicode_literals
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Bootstrap a minimal frappe stub so the module under test can be imported
# without a running Frappe instance.
# ---------------------------------------------------------------------------

def _make_frappe_stub():
    frappe_mod = types.ModuleType("frappe")
    frappe_mod.db = MagicMock()
    frappe_mod._ = lambda s, *a: s
    frappe_mod.whitelist = lambda fn=None, **kw: (fn if fn else lambda f: f)

    class _ValidationError(Exception):
        pass

    frappe_mod.ValidationError = _ValidationError

    def _throw(msg, exc=None):
        raise (exc or _ValidationError)(msg)

    frappe_mod.throw = _throw
    return frappe_mod


_frappe_stub = _make_frappe_stub()
sys.modules.setdefault("frappe", _frappe_stub)
sys.modules.setdefault("frappe.model", types.ModuleType("frappe.model"))
sys.modules.setdefault("frappe.model.document", types.ModuleType("frappe.model.document"))
sys.modules["frappe.model.document"].Document = object
sys.modules.setdefault("frappe.desk", types.ModuleType("frappe.desk"))
sys.modules.setdefault("frappe.desk.search", types.ModuleType("frappe.desk.search"))
sys.modules["frappe.desk.search"].sanitize_searchfield = lambda s: s

def _flt(val, precision=None):
    """Minimal float helper mimicking frappe.utils.flt."""
    try:
        v = float(val or 0)
    except (TypeError, ValueError):
        v = 0.0
    if precision is not None:
        v = round(v, precision)
    return v


_utils_mod = types.ModuleType("frappe.utils")
_utils_mod.flt = _flt
flt = _flt
_utils_mod.getdate = MagicMock()
_utils_mod.get_url = MagicMock()
_utils_mod.now = MagicMock()
_utils_mod.nowtime = MagicMock()
_utils_mod.get_time = MagicMock()
_utils_mod.today = MagicMock(return_value="2024-01-01")
_utils_mod.get_datetime = MagicMock()
_utils_mod.add_days = MagicMock()
_utils_mod.add_to_date = MagicMock()
_utils_mod.nowdate = MagicMock()
sys.modules["frappe.utils"] = _utils_mod

# Now we can import the module under test.
from ecs_cheques.ecs_cheques.overrides.payment_entry.payment_entry import (  # noqa: E402
    _get_cheque_paid_amount,
    _je_account,
    _needs_multi_currency,
    _get_account_currency,
)

import frappe  # noqa: E402  (the stub we just registered)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_doc(**kwargs):
    """Return a lightweight doc-like object with the given attributes."""
    doc = MagicMock()
    doc.cheque_table_no = kwargs.get("cheque_table_no", None)
    doc.paid_amount = kwargs.get("paid_amount", 0)
    doc.source_exchange_rate = kwargs.get("source_exchange_rate", 1.0)
    doc.target_exchange_rate = kwargs.get("target_exchange_rate", 1.0)
    doc.paid_from_account_currency = kwargs.get("paid_from_account_currency", "ILS")
    doc.paid_to_account_currency = kwargs.get("paid_to_account_currency", "USD")
    doc.name = kwargs.get("name", "PE-0001")
    return doc


class _FrappeDict(dict):
    """Minimal frappe._dict mimic: a dict that also supports attribute access."""
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            return None

    def __setattr__(self, key, value):
        self[key] = value


def _mock_cheque_table(paid_amount, target_exchange_rate, exchange_rate_party_to_mop=0,
                       account_currency_from="", account_currency=""):
    """Return a dict-like object that mimics frappe.db.get_value(..., as_dict=True).

    frappe.db.get_value with as_dict=True returns a frappe._dict which supports
    both dict-style (.get()) and attribute-style access.
    """
    return _FrappeDict(
        paid_amount=paid_amount,
        target_exchange_rate=target_exchange_rate,
        exchange_rate_party_to_mop=exchange_rate_party_to_mop,
        account_currency_from=account_currency_from,
        account_currency=account_currency,
    )


# ---------------------------------------------------------------------------
# Tests for _get_cheque_paid_amount
# ---------------------------------------------------------------------------

class TestGetChequePaidAmount(unittest.TestCase):
    """Verify that _get_cheque_paid_amount correctly derives the base amount."""

    def _patch_db(self, cheque_table_data):
        """Patch frappe.db.get_value to return cheque_table_data."""
        return patch.object(frappe.db, "get_value", return_value=cheque_table_data)

    # ------------------------------------------------------------------
    # Scenario described in the issue:
    #   company_currency = ILS
    #   received_amount (USD) = 1,000  →  paid_to account in USD
    #   Cheque Table Receive.paid_amount = 1,000 USD
    #   Cheque Table Receive.target_exchange_rate = 3.159059 (USD → ILS)
    #   Expected paid_amount_company = 3,159.059 ILS
    # ------------------------------------------------------------------

    def test_derive_from_cheque_table_cross_currency(self):
        """paid_amount_company must equal ctr.paid_amount × ctr.target_exchange_rate."""
        ctr = _mock_cheque_table(paid_amount=1000.0, target_exchange_rate=3.159059)
        doc = _make_doc(
            cheque_table_no="CHQ-0001",
            paid_amount=3159.059,       # correctly set ILS amount on PE
            source_exchange_rate=1.0,   # ILS → ILS = 1
            target_exchange_rate=3.159059,
        )
        with self._patch_db(ctr):
            result = _get_cheque_paid_amount(doc, "ILS")
        self.assertAlmostEqual(result, 3159.059, places=3)

    def test_target_exchange_rate_updated_on_doc_when_wrong(self):
        """doc.target_exchange_rate must be corrected in-memory from the cheque table."""
        ctr = _mock_cheque_table(paid_amount=1000.0, target_exchange_rate=3.159059)
        # Simulate a PE where source_exchange_rate was set to 1 and paid_amount = 3159.059
        doc = _make_doc(
            cheque_table_no="CHQ-0002",
            paid_amount=3159.059,
            source_exchange_rate=1.0,
            target_exchange_rate=1.0,   # ← incorrectly 1 on the PE
        )
        with self._patch_db(ctr):
            _get_cheque_paid_amount(doc, "ILS")
        # After the call the in-memory rate must be corrected
        self.assertAlmostEqual(doc.target_exchange_rate, 3.159059, places=6)

    def test_no_cheque_table_falls_back_to_pe_amounts(self):
        """When cheque_table_no is blank, use doc.paid_amount × source_exchange_rate."""
        doc = _make_doc(
            cheque_table_no=None,
            paid_amount=3159.059,
            source_exchange_rate=1.0,
        )
        result = _get_cheque_paid_amount(doc, "ILS")
        self.assertAlmostEqual(result, 3159.059, places=3)

    def test_raises_when_cheque_table_not_found(self):
        """A missing Cheque Table Receive record must raise an error."""
        doc = _make_doc(cheque_table_no="CHQ-MISSING", paid_amount=1000.0)
        with patch.object(frappe.db, "get_value", return_value=None):
            with self.assertRaises(Exception):
                _get_cheque_paid_amount(doc, "ILS")

    def test_raises_when_paid_amount_zero(self):
        """Zero paid_amount in Cheque Table must raise an error."""
        ctr = _mock_cheque_table(paid_amount=0, target_exchange_rate=3.159059)
        doc = _make_doc(cheque_table_no="CHQ-ZERO", paid_amount=0)
        with patch.object(frappe.db, "get_value", return_value=ctr):
            with self.assertRaises(Exception):
                _get_cheque_paid_amount(doc, "ILS")

    def test_raises_when_paid_amount_negative(self):
        """Negative paid_amount must raise an error."""
        ctr = _mock_cheque_table(paid_amount=-500, target_exchange_rate=1.0)
        doc = _make_doc(cheque_table_no="CHQ-NEG", paid_amount=-500)
        with patch.object(frappe.db, "get_value", return_value=ctr):
            with self.assertRaises(Exception):
                _get_cheque_paid_amount(doc, "ILS")

    def test_raises_on_significant_mismatch_with_pe(self):
        """If cheque table amount differs from PE base amount by > 1 %, raise."""
        # ctr says 3159.059 ILS, but PE says 1000 ILS → ~216 % mismatch
        ctr = _mock_cheque_table(paid_amount=1000.0, target_exchange_rate=3.159059)
        doc = _make_doc(
            cheque_table_no="CHQ-MISMATCH",
            paid_amount=1000.0,          # ← wrong: should be 3159.059
            source_exchange_rate=1.0,    # PE base = 1000 × 1 = 1000
        )
        with patch.object(frappe.db, "get_value", return_value=ctr):
            with self.assertRaises(Exception):
                _get_cheque_paid_amount(doc, "ILS")

    def test_no_raise_on_tiny_floating_point_difference(self):
        """Sub-1 % rounding differences must NOT raise."""
        # ctr: 1000 × 3.159059 = 3159.059
        # PE: 3159.059 × 1 = 3159.059  → exact match (or negligible fp error)
        ctr = _mock_cheque_table(paid_amount=1000.0, target_exchange_rate=3.159059)
        doc = _make_doc(
            cheque_table_no="CHQ-OK",
            paid_amount=3159.059,
            source_exchange_rate=1.0,
        )
        with patch.object(frappe.db, "get_value", return_value=ctr):
            result = _get_cheque_paid_amount(doc, "ILS")
        self.assertAlmostEqual(result, 3159.059, places=2)

    def test_same_currency_cheque_table(self):
        """When paid_amount is in company currency, target_exchange_rate = 1."""
        ctr = _mock_cheque_table(paid_amount=5000.0, target_exchange_rate=1.0)
        doc = _make_doc(
            cheque_table_no="CHQ-SAME",
            paid_amount=5000.0,
            source_exchange_rate=1.0,
        )
        with patch.object(frappe.db, "get_value", return_value=ctr):
            result = _get_cheque_paid_amount(doc, "ILS")
        self.assertAlmostEqual(result, 5000.0, places=3)

    def test_same_non_company_currency_uses_source_exchange_rate(self):
        """When both accounts are ILS but company is USD, use PE source_exchange_rate.

        Bug scenario: Paid From and Paid To both ILS, company USD.
        JS sets exchange_rate_party_to_mop = 1.0 (meaningless).
        ERPNext validate sets source_exchange_rate = 0.31655 (ILS → USD).
        _get_cheque_paid_amount must NOT raise and must return
        paid_amount × source_exchange_rate = 5000 × 0.31655 = 1582.75.
        """
        ctr = _mock_cheque_table(
            paid_amount=5000.0,
            target_exchange_rate=0.31655,
            exchange_rate_party_to_mop=1.0,   # set by JS for same-currency pair
            account_currency_from="ILS",
            account_currency="ILS",
        )
        doc = _make_doc(
            cheque_table_no="CHQ-SAME-ILS",
            paid_amount=5000.0,
            source_exchange_rate=0.31655,      # ILS → USD set by ERPNext validate
        )
        with patch.object(frappe.db, "get_value", return_value=ctr):
            result = _get_cheque_paid_amount(doc, "USD")
        self.assertAlmostEqual(result, 1582.75, places=2)

    def test_same_non_company_currency_no_throw_on_rate_mismatch(self):
        """Same-currency pair must NOT throw even when source_exchange_rate != exchange_rate_party_to_mop."""
        ctr = _mock_cheque_table(
            paid_amount=5000.0,
            target_exchange_rate=0.31655,
            exchange_rate_party_to_mop=1.0,
            account_currency_from="ILS",
            account_currency="ILS",
        )
        # source_exchange_rate differs from exchange_rate_party_to_mop by >1%
        # but must NOT raise because the same-currency path skips the check.
        doc = _make_doc(
            cheque_table_no="CHQ-SAME-ILS-2",
            paid_amount=5000.0,
            source_exchange_rate=0.31655,
        )
        with patch.object(frappe.db, "get_value", return_value=ctr):
            # Should not raise
            result = _get_cheque_paid_amount(doc, "USD")
        self.assertGreater(result, 0)

    def test_usd_to_usd_company_currency_ignores_stale_exch_party_to_mop(self):
        """Bug 1: USD→USD PE (company=USD) must NOT throw even with stale exchange_rate_party_to_mop.

        Root cause: after editing a cheque row that previously had ILS→USD accounts,
        exchange_rate_party_to_mop could retain a stale value (e.g. 0.31655 = 1/3.159059).
        When both PE account currencies are USD the value is meaningless and must be
        ignored entirely so the legacy path (ctr.paid_amount × ctr.target_exchange_rate)
        is used instead.
        """
        ctr = _mock_cheque_table(
            paid_amount=1000.0,
            target_exchange_rate=1.0,
            exchange_rate_party_to_mop=0.31655,   # stale ILS→USD rate
            account_currency_from="USD",
            account_currency="USD",
        )
        doc = _make_doc(
            cheque_table_no="CHQ-USD-USD",
            paid_amount=1000.0,
            source_exchange_rate=1.0,
            paid_from_account_currency="USD",
            paid_to_account_currency="USD",
        )
        with patch.object(frappe.db, "get_value", return_value=ctr):
            # Must NOT raise a mismatch error
            result = _get_cheque_paid_amount(doc, "USD")
        self.assertAlmostEqual(result, 1000.0, places=2)

    def test_usd_to_usd_company_currency_returns_legacy_amount(self):
        """USD→USD (company=USD): result must be ctr.paid_amount × ctr.target_exchange_rate."""
        ctr = _mock_cheque_table(
            paid_amount=1000.0,
            target_exchange_rate=1.0,
            exchange_rate_party_to_mop=0.31655,   # stale value, must be ignored
            account_currency_from="USD",
            account_currency="USD",
        )
        doc = _make_doc(
            cheque_table_no="CHQ-USD-USD-2",
            paid_amount=1000.0,
            source_exchange_rate=1.0,
            paid_from_account_currency="USD",
            paid_to_account_currency="USD",
        )
        with patch.object(frappe.db, "get_value", return_value=ctr):
            result = _get_cheque_paid_amount(doc, "USD")
        self.assertAlmostEqual(result, 1000.0, places=2)

    def test_jod_cheque_usd_accounts_returns_converted_amount(self):
        """Bug 2: JOD cheque, both accounts USD, company USD → result must use target_exchange_rate.

        When a JOD cheque is deposited via a USD-account PE, ctr.target_exchange_rate is
        the JOD→USD rate (e.g. 1.41044) and ctr.paid_amount is the JOD face value (1000).
        The PE paid_amount has already been set to 1000 × 1.41044 = 1410.44 (USD) by the
        Python fix in create_payment_entry_from_cheque.  The legacy path in
        _get_cheque_paid_amount must return 1000 × 1.41044 = 1410.44.
        """
        ctr = _mock_cheque_table(
            paid_amount=1000.0,
            target_exchange_rate=1.41044,   # JOD → USD
            exchange_rate_party_to_mop=0,
            account_currency_from="USD",
            account_currency="USD",
        )
        doc = _make_doc(
            cheque_table_no="CHQ-JOD-USD",
            paid_amount=1410.44,             # USD equivalent set by PE creation fix
            source_exchange_rate=1.0,
            paid_from_account_currency="USD",
            paid_to_account_currency="USD",
        )
        with patch.object(frappe.db, "get_value", return_value=ctr):
            result = _get_cheque_paid_amount(doc, "USD")
        self.assertAlmostEqual(result, 1410.44, places=2)

    def test_jod_cross_account_no_mismatch_error(self):
        """JOD→USD cross-currency (paid_from=USD=company, paid_to=JOD) must NOT raise.

        Scenario from PAY-2026-00041: company=USD, paid_from=USD, paid_to=JOD.
        exch_party_to_mop=0.709 is stored on the Cheque Table Receive row but
        source_exchange_rate on the PE is 1.0 (USD = company currency).
        The bidirectional-rate mismatch check must be skipped because the
        paid_from account is already in company currency.
        """
        ctr = _mock_cheque_table(
            paid_amount=1000.0,
            target_exchange_rate=1.410437,
            exchange_rate_party_to_mop=0.709,   # JOD → USD rate on the row
            account_currency_from="USD",
            account_currency="JOD",
        )
        doc = _make_doc(
            cheque_table_no="CHQ-JOD-CROSS",
            paid_amount=1410.437,
            source_exchange_rate=1.0,            # USD = company currency
            target_exchange_rate=1.410437,
            paid_from_account_currency="USD",
            paid_to_account_currency="JOD",
        )
        with patch.object(frappe.db, "get_value", return_value=ctr):
            # Must NOT raise a mismatch error
            result = _get_cheque_paid_amount(doc, "USD")
        self.assertAlmostEqual(result, 1410.437, places=2)

    def test_jod_cross_account_returns_usd_amount(self):
        """JOD→USD cross-currency: returned amount must be the USD equivalent (1410.437)."""
        ctr = _mock_cheque_table(
            paid_amount=1000.0,
            target_exchange_rate=1.410437,
            exchange_rate_party_to_mop=0.709,
            account_currency_from="USD",
            account_currency="JOD",
        )
        doc = _make_doc(
            cheque_table_no="CHQ-JOD-CROSS-2",
            paid_amount=1410.437,
            source_exchange_rate=1.0,
            target_exchange_rate=1.410437,
            paid_from_account_currency="USD",
            paid_to_account_currency="JOD",
        )
        with patch.object(frappe.db, "get_value", return_value=ctr):
            result = _get_cheque_paid_amount(doc, "USD")
        # Legacy path: 1000 × 1.410437 = 1410.437
        self.assertAlmostEqual(result, 1410.437, places=2)

    def test_rate_drift_syncs_cheque_table_and_uses_pe_rate(self):
        """Rate-drift scenario from the issue: ILS→JOD, company USD.

        Exact numbers from the problem statement:
          Paid Amount (ILS): 2173.73
          source_exchange_rate (PE): 0.324427881   (ILS → USD)
          Target Amount (JOD): 500.00
          target_exchange_rate (PE): 1.410437235   (JOD → USD)
          exchange_rate_party_to_mop (cheque table): 0.230019368  (stale)
          Expected base: 2173.73 × 0.324427881 ≈ 705.219 USD

        The cheque was originally received at a different rate
        (exchange_rate_party_to_mop = 0.230019368) but the current Payment
        Entry uses 0.324427881.  Both sides of the PE balance to 705.219 USD,
        so submission must succeed.

        Expected behaviour:
        1. No exception is raised.
        2. The returned base amount equals paid_amount × source_exchange_rate.
        3. frappe.db.set_value is called to sync exchange_rate_party_to_mop.
        """
        ctr = _mock_cheque_table(
            paid_amount=2173.73,
            target_exchange_rate=1.410437235,
            exchange_rate_party_to_mop=0.230019368,   # stale stored rate
            account_currency_from="ILS",
            account_currency="JOD",
        )
        doc = _make_doc(
            cheque_table_no="966m7hk2o0",
            paid_amount=2173.73,
            source_exchange_rate=0.324427881,
            target_exchange_rate=1.410437235,
            paid_from_account_currency="ILS",
            paid_to_account_currency="JOD",
        )
        with patch.object(frappe.db, "get_value", return_value=ctr), \
             patch.object(frappe.db, "set_value") as mock_set_value:
            # Must NOT raise
            result = _get_cheque_paid_amount(doc, "USD")

        # 1. Returned base amount uses PE source_exchange_rate
        expected_base = flt(2173.73 * 0.324427881, 9)
        self.assertAlmostEqual(result, expected_base, places=4)

        # 2. The stale cheque-table rate was synchronised to the PE rate
        mock_set_value.assert_called_once_with(
            "Cheque Table Receive",
            "966m7hk2o0",
            "exchange_rate_party_to_mop",
            0.324427881,
        )


# ---------------------------------------------------------------------------
# Tests for JE account balance using _je_account
# ---------------------------------------------------------------------------

class TestJeAccountBalance(unittest.TestCase):
    """Verify that _je_account produces balanced debit/credit entries."""

    def _patch_account_currency(self, currency_map):
        """Patch frappe.db.get_value for Account.account_currency lookups."""
        def _side_effect(doctype, name, field, **kwargs):
            if doctype == "Account":
                return currency_map.get(name, "ILS")
            return None
        return patch.object(frappe.db, "get_value", side_effect=_side_effect)

    def _total_debit_credit(self, accounts):
        """Return (total_debit, total_credit) for a list of JE account dicts."""
        # Simulate ERPNext set_amounts: d.debit = d.debit_in_acc * d.exchange_rate
        total_debit = 0.0
        total_credit = 0.0
        for acc in accounts:
            if acc.get("debit_in_account_currency"):
                debit = flt(acc["debit_in_account_currency"]) * flt(acc["exchange_rate"] or 1.0)
                total_debit += round(debit, 9)
            if acc.get("credit_in_account_currency"):
                credit = flt(acc["credit_in_account_currency"]) * flt(acc["exchange_rate"] or 1.0)
                total_credit += round(credit, 9)
        return total_debit, total_credit

    def test_je_balance_cross_currency_receive(self):
        """
        Scenario from the issue:
          company = ILS
          collection_fee_account = ILS account
          paid_to = USD cheque wallet account
          paid_amount_company = 3159.059 ILS  (= 1000 USD × 3.159059)

        Both JE accounts must produce the same base (ILS) amount so that
        total_debit == total_credit.
        """
        paid_amount_company = 3159.059
        exchange_rate = 3.159059

        doc = _make_doc(
            target_exchange_rate=exchange_rate,
            paid_to_account_currency="USD",
            paid_from_account_currency="ILS",
        )

        currency_map = {
            "collection_fee_acc": "ILS",
            "usd_cheque_wallet": "USD",
        }
        with self._patch_account_currency(currency_map):
            debit_entry = _je_account(
                "collection_fee_acc", paid_amount_company, True, doc, "ILS"
            )
            credit_entry = _je_account(
                "usd_cheque_wallet", paid_amount_company, False, doc, "ILS"
            )

        total_debit, total_credit = self._total_debit_credit([debit_entry, credit_entry])
        self.assertAlmostEqual(
            total_debit, total_credit, places=2,
            msg=(
                "JE imbalance detected: debit={}, credit={}, diff={}".format(
                    total_debit, total_credit, total_debit - total_credit
                )
            ),
        )

    def test_je_balance_same_currency(self):
        """ILS-only JE must balance trivially."""
        paid_amount_company = 5000.0
        doc = _make_doc(
            target_exchange_rate=1.0,
            paid_to_account_currency="ILS",
            paid_from_account_currency="ILS",
        )
        currency_map = {"acc_a": "ILS", "acc_b": "ILS"}
        with self._patch_account_currency(currency_map):
            debit_entry = _je_account("acc_a", paid_amount_company, True, doc, "ILS")
            credit_entry = _je_account("acc_b", paid_amount_company, False, doc, "ILS")

        total_debit, total_credit = self._total_debit_credit([debit_entry, credit_entry])
        self.assertAlmostEqual(total_debit, total_credit, places=2)

    def test_debit_amount_equals_paid_amount_company(self):
        """The debit field on a company-currency account must equal paid_amount_company."""
        paid_amount_company = 3159.059
        doc = _make_doc(target_exchange_rate=1.0, paid_to_account_currency="ILS")
        currency_map = {"ils_acc": "ILS"}
        with self._patch_account_currency(currency_map):
            entry = _je_account("ils_acc", paid_amount_company, True, doc, "ILS")
        self.assertAlmostEqual(entry["debit"], paid_amount_company, places=3)
        self.assertAlmostEqual(entry["debit_in_account_currency"], paid_amount_company, places=3)

    def test_credit_in_account_currency_matches_cheque_paid_amount(self):
        """
        For a USD account with target_exchange_rate=3.159059,
        credit_in_account_currency must equal paid_amount_company / exchange_rate
        (i.e., the original cheque amount in USD).
        """
        paid_amount_company = 3159.059
        exchange_rate = 3.159059
        doc = _make_doc(
            target_exchange_rate=exchange_rate,
            paid_to_account_currency="USD",
        )
        currency_map = {"usd_acc": "USD"}
        with self._patch_account_currency(currency_map):
            entry = _je_account("usd_acc", paid_amount_company, False, doc, "ILS")

        expected_usd = paid_amount_company / exchange_rate  # ≈ 1000 USD
        self.assertAlmostEqual(
            entry["credit_in_account_currency"], expected_usd, places=2
        )


# ---------------------------------------------------------------------------
# Tests for _needs_multi_currency
# ---------------------------------------------------------------------------

class TestNeedsMultiCurrency(unittest.TestCase):
    def _patch_account_currency(self, currency_map):
        def _side_effect(doctype, name, field, **kwargs):
            if doctype == "Account":
                return currency_map.get(name, "ILS")
            return None
        return patch.object(frappe.db, "get_value", side_effect=_side_effect)

    def test_all_company_currency(self):
        currency_map = {"a": "ILS", "b": "ILS"}
        with self._patch_account_currency(currency_map):
            self.assertFalse(_needs_multi_currency(["a", "b"], "ILS"))

    def test_one_foreign_currency(self):
        currency_map = {"a": "ILS", "b": "USD"}
        with self._patch_account_currency(currency_map):
            self.assertTrue(_needs_multi_currency(["a", "b"], "ILS"))

    def test_empty_list(self):
        with patch.object(frappe.db, "get_value", return_value="ILS"):
            self.assertFalse(_needs_multi_currency([], "ILS"))


if __name__ == "__main__":
    unittest.main()
