# Copyright (c) 2021, erpcloud.systems and Contributors
# See license.txt
"""
Unit tests for _compute_payment_entry_amounts in multiple_cheque_entry.py.

These tests verify the fix for incorrect source_exchange_rate / paid_amount
assignments when creating a Payment Entry from Multiple Cheque Entry.

The scenario described in the issue
-------------------------------------
  Company currency        : ILS
  paid_from account       : party receivable  (currency = ILS)
  paid_to   account       : cheque wallet      (currency = USD)
  Cheque amount entered   : 1 000 USD  →  row.paid_amount = 1 000
  stored_exchange_rate    : 3.159059          (USD → ILS)

Expected Payment Entry
  paid_amount             : 3 159.059 ILS  (from party ILS account)
  received_amount         : 1 000     USD  (into USD cheque wallet)
  source_exchange_rate    : 1          (ILS → ILS = 1)
  target_exchange_rate    : 3.159059   (USD → ILS)

Wrong behaviour (before fix)
  paid_from_account_currency was USD  →  "Paid Amount (USD) = 3 159.059"
"""

import sys
import types
import unittest

# ---------------------------------------------------------------------------
# Bootstrap a minimal frappe stub so the module can be imported without a
# running Frappe instance.
# ---------------------------------------------------------------------------

def _make_frappe_stub():
	mod = types.ModuleType("frappe")
	mod.db = None                          # not used in pure-computation tests
	mod._ = lambda s, *a: s
	mod.whitelist = lambda fn=None, **kw: (fn if fn else lambda f: f)
	class _VE(Exception):
		pass
	mod.ValidationError = _VE
	mod.throw = lambda msg, exc=None: (_ for _ in ()).throw((exc or _VE)(msg))
	import unittest.mock as _m
	mod.get_cached_value = _m.MagicMock()
	mod.get_all = _m.MagicMock(return_value=[])
	return mod

_frappe = _make_frappe_stub()
sys.modules.setdefault("frappe", _frappe)
sys.modules.setdefault("frappe.model", types.ModuleType("frappe.model"))
_doc_mod = types.ModuleType("frappe.model.document")
_doc_mod.Document = object
sys.modules.setdefault("frappe.model.document", _doc_mod)
sys.modules.setdefault("frappe.desk", types.ModuleType("frappe.desk"))
_ds = types.ModuleType("frappe.desk.search")
_ds.sanitize_searchfield = lambda s: s
sys.modules.setdefault("frappe.desk.search", _ds)

_utils = types.ModuleType("frappe.utils")

def _flt(val, precision=None):
	try:
		v = float(val or 0)
	except (TypeError, ValueError):
		v = 0.0
	return round(v, precision) if precision is not None else v

_utils.flt = _flt
_utils.nowdate = lambda: "2024-01-01"
import unittest.mock as _mock
for _attr in ("getdate", "get_url", "now", "nowtime", "get_time", "today",
              "get_datetime", "add_days", "add_to_date"):
	setattr(_utils, _attr, _mock.MagicMock())
sys.modules["frappe.utils"] = _utils

# Now import the functions under test.
from ecs_cheques.ecs_cheques.doctype.multiple_cheque_entry.multiple_cheque_entry import (  # noqa: E402
	_compute_payment_entry_amounts,
	_get_account_currency_db,
	create_payment_entry_from_cheque,
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestComputePaymentEntryAmounts(unittest.TestCase):
	"""Verify _compute_payment_entry_amounts for all standard scenarios."""

	# ------------------------------------------------------------------
	# Scenario from the issue
	# ------------------------------------------------------------------

	def test_receive_cross_currency_ils_from_usd_to(self):
		"""
		Receive: paid_from = ILS (party), paid_to = USD (cheque wallet).
		row_paid_amount = 1 000 USD (entered in paid_to currency).
		stored_exchange_rate = 3.159059 (USD → ILS).

		Expected:
		  paid_amount          = 3 159.059 ILS
		  received_amount      = 1 000 USD
		  source_exchange_rate = 1
		  target_exchange_rate = 3.159059
		  paid_from_account_currency = ILS
		  paid_to_account_currency   = USD
		"""
		result = _compute_payment_entry_amounts(
			row_paid_amount=1000.0,
			paid_from_currency="ILS",
			paid_to_currency="USD",
			company_currency="ILS",
			stored_exchange_rate=3.159059,
			payment_type="Receive",
		)
		self.assertAlmostEqual(result["paid_amount"], 3159.059, places=3,
			msg="paid_amount (ILS) must equal 1000 × 3.159059")
		self.assertAlmostEqual(result["received_amount"], 1000.0, places=3,
			msg="received_amount (USD) must be the original cheque amount")
		self.assertAlmostEqual(result["source_exchange_rate"], 1.0, places=6,
			msg="source_exchange_rate must be 1 when paid_from = company currency")
		self.assertAlmostEqual(result["target_exchange_rate"], 3.159059, places=6,
			msg="target_exchange_rate must equal stored_exchange_rate (USD → ILS)")
		self.assertEqual(result["paid_from_account_currency"], "ILS")
		self.assertEqual(result["paid_to_account_currency"], "USD")

	def test_receive_cross_currency_no_inverted_paid_amount(self):
		"""
		Regression: the bug showed 'Paid Amount (USD) = 3 159.059'.
		This is only possible if paid_from_account_currency is wrongly set to USD.
		Verify it is always ILS when paid_from = ILS.
		"""
		result = _compute_payment_entry_amounts(1000.0, "ILS", "USD", "ILS", 3.159059, "Receive")
		self.assertEqual(result["paid_from_account_currency"], "ILS",
			msg="paid_from_account_currency must NOT be USD (regression guard)")
		# The erroneous behaviour was: paid_amount = 3159.059 displayed as USD.
		# With correct currencies this is now 3159.059 ILS, not USD.
		self.assertGreater(result["paid_amount"], result["received_amount"],
			msg="ILS amount must be larger than the USD amount for this exchange rate")

	# ------------------------------------------------------------------
	# Pay: mirror of the Receive scenario
	# ------------------------------------------------------------------

	def test_pay_cross_currency_usd_from_ils_to(self):
		"""
		Pay: paid_from = USD (cheque/bank), paid_to = ILS (supplier account).
		row_paid_amount = 1 000 USD (entered in paid_from currency).
		stored_exchange_rate = 3.159059 (USD → ILS).

		Expected:
		  paid_amount          = 1 000 USD
		  received_amount      = 3 159.059 ILS
		  source_exchange_rate = 3.159059
		  target_exchange_rate = 1
		"""
		result = _compute_payment_entry_amounts(
			row_paid_amount=1000.0,
			paid_from_currency="USD",
			paid_to_currency="ILS",
			company_currency="ILS",
			stored_exchange_rate=3.159059,
			payment_type="Pay",
		)
		self.assertAlmostEqual(result["paid_amount"], 1000.0, places=3)
		self.assertAlmostEqual(result["received_amount"], 3159.059, places=3)
		self.assertAlmostEqual(result["source_exchange_rate"], 3.159059, places=6)
		self.assertAlmostEqual(result["target_exchange_rate"], 1.0, places=6)
		self.assertEqual(result["paid_from_account_currency"], "USD")
		self.assertEqual(result["paid_to_account_currency"], "ILS")

	# ------------------------------------------------------------------
	# Same-currency (no conversion)
	# ------------------------------------------------------------------

	def test_same_currency_ils_both(self):
		"""When both accounts are ILS, all amounts equal and rates = 1."""
		result = _compute_payment_entry_amounts(
			row_paid_amount=5000.0,
			paid_from_currency="ILS",
			paid_to_currency="ILS",
			company_currency="ILS",
			stored_exchange_rate=1.0,
			payment_type="Receive",
		)
		self.assertAlmostEqual(result["paid_amount"], 5000.0, places=3)
		self.assertAlmostEqual(result["received_amount"], 5000.0, places=3)
		self.assertEqual(result["source_exchange_rate"], 1.0)
		self.assertEqual(result["target_exchange_rate"], 1.0)

	def test_same_currency_rate_one_even_if_stored_rate_wrong(self):
		"""Same currency: exchange rates must be 1 regardless of stored_exchange_rate."""
		result = _compute_payment_entry_amounts(5000.0, "ILS", "ILS", "ILS", 3.5, "Pay")
		self.assertEqual(result["source_exchange_rate"], 1.0)
		self.assertEqual(result["target_exchange_rate"], 1.0)
		self.assertAlmostEqual(result["paid_amount"], 5000.0, places=3)

	# ------------------------------------------------------------------
	# ERPNext GL invariant: base_paid == base_received
	# ------------------------------------------------------------------

	def test_gl_balance_receive(self):
		"""base_paid_amount must equal base_received_amount (GL invariant)."""
		result = _compute_payment_entry_amounts(1000.0, "ILS", "USD", "ILS", 3.159059, "Receive")
		base_paid = result["paid_amount"] * result["source_exchange_rate"]
		base_received = result["received_amount"] * result["target_exchange_rate"]
		self.assertAlmostEqual(base_paid, base_received, places=2,
			msg=f"GL imbalance: base_paid={base_paid} ≠ base_received={base_received}")

	def test_gl_balance_pay(self):
		"""GL invariant for Pay type."""
		result = _compute_payment_entry_amounts(1000.0, "USD", "ILS", "ILS", 3.159059, "Pay")
		base_paid = result["paid_amount"] * result["source_exchange_rate"]
		base_received = result["received_amount"] * result["target_exchange_rate"]
		self.assertAlmostEqual(base_paid, base_received, places=2)

	# ------------------------------------------------------------------
	# source_exchange_rate = 1 only when paid_from = company currency
	# ------------------------------------------------------------------

	def test_source_rate_is_1_only_when_paid_from_equals_company(self):
		"""source_exchange_rate must NOT be 1 if paid_from is a foreign currency."""
		result = _compute_payment_entry_amounts(1000.0, "USD", "ILS", "ILS", 3.159059, "Pay")
		self.assertNotEqual(result["source_exchange_rate"], 1.0,
			msg="source_exchange_rate must not default to 1 for a foreign paid_from account")


# ---------------------------------------------------------------------------
# Tests for the DB-fetch path: create_payment_entry_from_cheque(docname, row_id)
# ---------------------------------------------------------------------------

class _Row:
	"""Minimal child-row stub."""
	def __init__(self, **kwargs):
		for k, v in kwargs.items():
			setattr(self, k, v)

	def get(self, key, default=None):
		return getattr(self, key, default)


class _Doc:
	"""Minimal parent-document stub."""
	def __init__(self, **kwargs):
		for k, v in kwargs.items():
			setattr(self, k, v)

	def get(self, key, default=None):
		return getattr(self, key, default)


class TestCreatePaymentEntryFromCheque(unittest.TestCase):
	"""Verify create_payment_entry_from_cheque builds the correct Payment Entry dict.

	We mock all Frappe DB/doc calls so no running instance is required.
	"""

	def _make_receive_row(self):
		return _Row(
			name="ROW-001",
			idx=1,
			account_paid_from="ILS-Receivable",
			account_paid_to="USD-Wallet",
			paid_amount=1000.0,
			amount_in_company_currency=3159.059,
			target_exchange_rate=3.159059,
			mode_of_payment="Cheque",
			party_type="Customer",
			party="CUST-001",
			cheque_type="Crossed",
			reference_no="CHQ-001",
			reference_date="2024-01-15",
			first_beneficiary="Company",
			person_name="Ahmed",
			issuer_name="Ahmed",
			picture_of_check=None,
			bank="National Bank",
			payment_entry=None,
		)

	def _make_parent_doc(self, row):
		return _Doc(
			name="MCE-001",
			company="Test Co",
			payment_type="Receive",
			posting_date="2024-01-15",
			mode_of_payment="Cheque",
			mode_of_payment_type="Cheque",
			cheque_bank="National Bank",
			bank_acc="Bank-ILS",
			cheque_table=[row],
			cheque_table_2=[],
		)

	def setUp(self):
		import sys
		self._frappe = sys.modules["frappe"]

		# Capture inserted PE dict
		self._inserted = {}
		self._submitted = False
		self._set_values = []

		class _FakePE:
			def __init__(inner_self, d):
				inner_self.__dict__.update(d)
				inner_self.name = "PE-TEST-001"
				inner_self.flags = type("F", (), {"ignore_permissions": False})()

			def insert(inner_self):
				self._inserted = inner_self.__dict__.copy()

			def submit(inner_self):
				self._submitted = True

		row = self._make_receive_row()
		doc = self._make_parent_doc(row)

		self._frappe.get_doc = lambda *args, **kwargs: (
			doc if (args and args[0] == "Multiple Cheque Entry") else _FakePE(kwargs or args[0] if args else {})
		)

		# Mock get_doc to return either the parent or a new PE
		orig_get_doc = self._frappe.get_doc
		def _get_doc(arg, *rest):
			if arg == "Multiple Cheque Entry":
				return doc
			# It's a PE dict
			return _FakePE(arg)
		self._frappe.get_doc = _get_doc

		# Mock db
		class _DB:
			def get_value(self, doctype, name, field):
				if doctype == "Company":
					return "ILS"
				if doctype == "Account":
					if name == "ILS-Receivable":
						return "ILS"
					if name == "USD-Wallet":
						return "USD"
				return None

			def set_value(self_, doctype, name, field, value):
				self._set_values.append((doctype, name, field, value))

		self._frappe.db = _DB()
		self._frappe.throw = lambda msg, exc=None: (_ for _ in ()).throw(Exception(msg))

	def test_receive_db_fetch_amounts(self):
		"""Receive: amounts fetched from DB row match v15 conventions."""
		result = create_payment_entry_from_cheque("MCE-001", "ROW-001")

		self.assertEqual(result, "PE-TEST-001")
		self.assertTrue(self._submitted, "Payment Entry must be submitted")

		pe = self._inserted
		self.assertAlmostEqual(pe.get("paid_amount"), 3159.059, places=6,
			msg="paid_amount must equal amount_in_company_currency (ILS)")
		self.assertAlmostEqual(pe.get("received_amount"), 1000.0, places=6,
			msg="received_amount must equal row.paid_amount (USD)")
		self.assertAlmostEqual(pe.get("source_exchange_rate"), 1.0, places=6)
		self.assertAlmostEqual(pe.get("target_exchange_rate"), 3.159059, places=6)
		self.assertEqual(pe.get("paid_from_account_currency"), "ILS")
		self.assertEqual(pe.get("paid_to_account_currency"), "USD")

	def test_receive_child_row_updated(self):
		"""Child row payment_entry must be updated via frappe.db.set_value."""
		create_payment_entry_from_cheque("MCE-001", "ROW-001")

		self.assertTrue(
			any(
				sv[0] == "Cheque Table Receive" and sv[1] == "ROW-001"
				and sv[2] == "payment_entry" and sv[3] == "PE-TEST-001"
				for sv in self._set_values
			),
			"frappe.db.set_value must be called to persist payment_entry on the child row",
		)

	def test_receive_ignore_permissions_set(self):
		"""pe.flags.ignore_permissions must be True before insert."""
		# Re-capture flag state at insert time
		flags_at_insert = {}

		orig_get_doc = self._frappe.get_doc
		class _FlagCapturePE:
			def __init__(inner_self, d):
				inner_self.__dict__.update(d)
				inner_self.name = "PE-TEST-001"
				inner_self.flags = type("F", (), {"ignore_permissions": False})()

			def insert(inner_self):
				flags_at_insert["ignore_permissions"] = inner_self.flags.ignore_permissions

			def submit(inner_self):
				pass

		def _get_doc2(arg, *rest):
			if arg == "Multiple Cheque Entry":
				return orig_get_doc("Multiple Cheque Entry")
			return _FlagCapturePE(arg)

		self._frappe.get_doc = _get_doc2
		create_payment_entry_from_cheque("MCE-001", "ROW-001")
		self.assertTrue(flags_at_insert.get("ignore_permissions"),
			"flags.ignore_permissions must be True when inserting the Payment Entry")


if __name__ == "__main__":
	unittest.main()



class TestExchangeRatePartyToMop(unittest.TestCase):
	"""Tests for the bidirectional exchange-rate fields and their effect on PE creation.

	Scenario: company currency = USD, party account = ILS, bank/MOP account = USD.
	  exchange_rate_mop_to_party = 3.159059  (USD -> ILS)
	  exchange_rate_party_to_mop = 0.316555  (ILS -> USD  = 1 / 3.159059)
	"""

	_RATE_MOP_TO_PARTY = 3.159059
	_RATE_PARTY_TO_MOP = round(1.0 / 3.159059, 9)

	# ------------------------------------------------------------------
	# _compute_payment_entry_amounts with company = paid_to currency (USD)
	# ------------------------------------------------------------------

	def test_receive_company_equals_paid_to(self):
		"""Receive: company = paid_to (USD). source = 1/stored_rate, target = 1."""
		result = _compute_payment_entry_amounts(
			row_paid_amount=1000.0,
			paid_from_currency="ILS",
			paid_to_currency="USD",
			company_currency="USD",
			stored_exchange_rate=self._RATE_MOP_TO_PARTY,
			payment_type="Receive",
		)
		self.assertAlmostEqual(result["source_exchange_rate"], self._RATE_PARTY_TO_MOP, places=6,
			msg="source_exchange_rate must be 1/stored_rate when paid_to = company currency")
		self.assertAlmostEqual(result["target_exchange_rate"], 1.0, places=6,
			msg="target_exchange_rate must be 1 when paid_to = company currency")
		self.assertEqual(result["paid_from_account_currency"], "ILS")
		self.assertEqual(result["paid_to_account_currency"], "USD")

	def test_receive_company_equals_paid_to_gl_balance(self):
		"""GL invariant: base_paid == base_received for company=USD scenario."""
		result = _compute_payment_entry_amounts(
			1000.0, "ILS", "USD", "USD", self._RATE_MOP_TO_PARTY, "Receive",
		)
		base_paid = result["paid_amount"] * result["source_exchange_rate"]
		base_received = result["received_amount"] * result["target_exchange_rate"]
		self.assertAlmostEqual(base_paid, base_received, places=2)

	# ------------------------------------------------------------------
	# create_payment_entry_from_cheque with exchange_rate_party_to_mop set
	# ------------------------------------------------------------------

	def _make_row_with_party_to_mop(self):
		"""Row with exchange_rate_party_to_mop set (company=USD scenario)."""
		return _Row(
			name="ROW-002",
			idx=1,
			account_paid_from="ILS-Receivable",
			account_paid_to="USD-Bank",
			paid_amount=1000.0,
			amount_in_company_currency=3159.059,
			target_exchange_rate=3.159059,
			exchange_rate_mop_to_party=3.159059,
			exchange_rate_party_to_mop=round(1.0 / 3.159059, 9),
			mode_of_payment="Cheque",
			party_type="Customer",
			party="CUST-002",
			cheque_type="Crossed",
			reference_no="CHQ-002",
			reference_date="2024-01-15",
			first_beneficiary="Company",
			person_name="Sara",
			issuer_name="Sara",
			picture_of_check=None,
			bank="Test Bank",
			payment_entry=None,
		)

	def setUp(self):
		import sys
		self._frappe = sys.modules["frappe"]
		self._inserted = {}
		self._submitted = False
		self._set_values = []

		row = self._make_row_with_party_to_mop()
		doc = _Doc(
			name="MCE-002",
			company="Test Co USD",
			payment_type="Receive",
			posting_date="2024-01-15",
			mode_of_payment="Cheque",
			mode_of_payment_type="Cheque",
			cheque_bank="Test Bank",
			bank_acc="Bank-USD",
			cheque_table=[row],
			cheque_table_2=[],
		)

		class _FakePE:
			def __init__(inner_self, d):
				inner_self.__dict__.update(d)
				inner_self.name = "PE-TEST-002"
				inner_self.flags = type("F", (), {"ignore_permissions": False})()

			def insert(inner_self):
				self._inserted = inner_self.__dict__.copy()

			def submit(inner_self):
				self._submitted = True

		def _get_doc(arg, *rest):
			if arg == "Multiple Cheque Entry":
				return doc
			return _FakePE(arg)

		self._frappe.get_doc = _get_doc

		class _DB:
			def get_value(self, doctype, name, field):
				if doctype == "Company":
					return "USD"
				if doctype == "Account":
					if name == "ILS-Receivable":
						return "ILS"
					if name == "USD-Bank":
						return "USD"
				return None

			def set_value(self_, doctype, name, field, value):
				self._set_values.append((doctype, name, field, value))

		self._frappe.db = _DB()
		self._frappe.throw = lambda msg, exc=None: (_ for _ in ()).throw(Exception(msg))

	def test_party_to_mop_used_as_source_exchange_rate(self):
		"""exchange_rate_party_to_mop must become source_exchange_rate in the PE."""
		create_payment_entry_from_cheque("MCE-002", "ROW-002")
		pe = self._inserted
		expected_source = round(1.0 / 3.159059, 9)
		self.assertAlmostEqual(pe.get("source_exchange_rate"), expected_source, places=6,
			msg="source_exchange_rate must equal exchange_rate_party_to_mop")

	def test_gl_balance_with_party_to_mop(self):
		"""base_paid == base_received when exchange_rate_party_to_mop is used."""
		create_payment_entry_from_cheque("MCE-002", "ROW-002")
		pe = self._inserted
		base_paid = pe.get("paid_amount") * pe.get("source_exchange_rate")
		base_received = pe.get("received_amount") * pe.get("target_exchange_rate")
		self.assertAlmostEqual(base_paid, base_received, places=2,
			msg="GL imbalance: base_paid={0} != base_received={1}".format(base_paid, base_received))

	def test_received_amount_equals_cheque_amount(self):
		"""received_amount must equal the original cheque amount (1000 USD)."""
		create_payment_entry_from_cheque("MCE-002", "ROW-002")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("received_amount"), 1000.0, places=6,
			msg="received_amount must be the original cheque amount")

	def test_paid_amount_equals_amount_in_company_currency(self):
		"""paid_amount (ILS) must equal amount_in_company_currency (3159.059)."""
		create_payment_entry_from_cheque("MCE-002", "ROW-002")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("paid_amount"), 3159.059, places=6,
			msg="paid_amount must equal amount_in_company_currency")


# ---------------------------------------------------------------------------
# Bug 1: USD→USD same-company-currency – exchange_rate_party_to_mop cleared
# ---------------------------------------------------------------------------

class TestUsdToUsdSameCurrency(unittest.TestCase):
	"""Verify that a USD→USD PE (company = USD) clears exchange_rate_party_to_mop.

	Scenario:
	  company_currency   = USD
	  paid_from account  = USD-Receivable  (USD)
	  paid_to   account  = USD-Wallet      (USD)
	  cheque_currency    = USD
	  exchange_rate_party_to_mop = 0.31655  ← stale value from a prior row state
	"""

	def _make_usd_row(self, exchange_rate_party_to_mop=0.31655, cheque_currency="USD"):
		return _Row(
			name="ROW-USD",
			idx=1,
			account_paid_from="USD-Receivable",
			account_paid_to="USD-Wallet",
			paid_amount=1000.0,
			amount_in_company_currency=1000.0,
			target_exchange_rate=1.0,
			exchange_rate_party_to_mop=exchange_rate_party_to_mop,
			cheque_currency=cheque_currency,
			mode_of_payment="Cheque",
			party_type="Customer",
			party="CUST-USD",
			cheque_type="Crossed",
			reference_no="CHQ-USD-001",
			reference_date="2024-01-15",
			first_beneficiary="Company",
			person_name="Test",
			issuer_name="Test",
			picture_of_check=None,
			bank="USD Bank",
			payment_entry=None,
		)

	def setUp(self):
		import sys
		self._frappe = sys.modules["frappe"]
		self._inserted = {}
		self._submitted = False
		self._set_values = []

		row = self._make_usd_row()
		doc = _Doc(
			name="MCE-USD",
			company="USD Co",
			payment_type="Receive",
			posting_date="2024-01-15",
			mode_of_payment="Cheque",
			mode_of_payment_type="Cheque",
			cheque_bank="USD Bank",
			bank_acc="Bank-USD",
			cheque_table=[row],
			cheque_table_2=[],
		)

		class _FakePE:
			def __init__(inner_self, d):
				inner_self.__dict__.update(d)
				inner_self.name = "PE-USD-001"
				inner_self.flags = type("F", (), {"ignore_permissions": False})()

			def insert(inner_self):
				self._inserted = inner_self.__dict__.copy()

			def submit(inner_self):
				self._submitted = True

		def _get_doc(arg, *rest):
			if arg == "Multiple Cheque Entry":
				return doc
			return _FakePE(arg)

		self._frappe.get_doc = _get_doc

		class _DB:
			def get_value(self, doctype, name, field):
				if doctype == "Company":
					return "USD"
				if doctype == "Account":
					if name in ("USD-Receivable", "USD-Wallet"):
						return "USD"
				return None

			def set_value(self_, doctype, name, field, value):
				self._set_values.append((doctype, name, field, value))

		self._frappe.db = _DB()
		self._frappe.throw = lambda msg, exc=None: (_ for _ in ()).throw(Exception(msg))

	def test_paid_amount_equals_cheque_amount(self):
		"""USD cheque in USD accounts: paid_amount must equal the cheque amount."""
		create_payment_entry_from_cheque("MCE-USD", "ROW-USD")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("paid_amount"), 1000.0, places=6)
		self.assertAlmostEqual(pe.get("received_amount"), 1000.0, places=6)

	def test_exchange_rates_are_one(self):
		"""USD→USD: both exchange rates must be 1."""
		create_payment_entry_from_cheque("MCE-USD", "ROW-USD")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("source_exchange_rate"), 1.0, places=6)
		self.assertAlmostEqual(pe.get("target_exchange_rate"), 1.0, places=6)

	def test_stale_exchange_rate_party_to_mop_is_cleared(self):
		"""Bug 1: stale exchange_rate_party_to_mop must be cleared (set to 0) in the DB."""
		create_payment_entry_from_cheque("MCE-USD", "ROW-USD")
		cleared = any(
			sv[0] == "Cheque Table Receive" and sv[1] == "ROW-USD"
			and sv[2] == "exchange_rate_party_to_mop" and sv[3] == 0
			for sv in self._set_values
		)
		self.assertTrue(cleared,
			"exchange_rate_party_to_mop must be cleared to 0 when both accounts are company currency")


# ---------------------------------------------------------------------------
# Bug 2: JOD→USD – foreign cheque in company-currency accounts
# ---------------------------------------------------------------------------

class TestJodChequeUsdAccounts(unittest.TestCase):
	"""Verify that a JOD cheque deposited via USD accounts (company = USD) uses
	the company-currency equivalent (paid_amount × target_exchange_rate) for the PE.

	Scenario:
	  company_currency          = USD
	  paid_from account         = USD-Receivable  (USD)
	  paid_to   account         = USD-Wallet      (USD)
	  cheque_currency           = JOD
	  paid_amount               = 1000 JOD  (face value of the cheque)
	  target_exchange_rate      = 1.41044    (JOD → USD rate)
	  Expected PE paid_amount   = 1000 × 1.41044 = 1410.44 USD
	"""

	_JOD_RATE = 1.41044

	def _make_jod_row(self):
		return _Row(
			name="ROW-JOD",
			idx=1,
			account_paid_from="USD-Receivable",
			account_paid_to="USD-Wallet",
			paid_amount=1000.0,
			amount_in_company_currency=1000.0,  # old/stale JS value (before fix)
			target_exchange_rate=self._JOD_RATE,
			exchange_rate_party_to_mop=0,
			cheque_currency="JOD",
			mode_of_payment="Cheque",
			party_type="Customer",
			party="CUST-JOD",
			cheque_type="Crossed",
			reference_no="CHQ-JOD-001",
			reference_date="2024-01-15",
			first_beneficiary="Company",
			person_name="Test",
			issuer_name="Test",
			picture_of_check=None,
			bank="JOD Bank",
			payment_entry=None,
		)

	def setUp(self):
		import sys
		self._frappe = sys.modules["frappe"]
		self._inserted = {}
		self._submitted = False
		self._set_values = []

		row = self._make_jod_row()
		doc = _Doc(
			name="MCE-JOD",
			company="USD Co",
			payment_type="Receive",
			posting_date="2024-01-15",
			mode_of_payment="Cheque",
			mode_of_payment_type="Cheque",
			cheque_bank="JOD Bank",
			bank_acc="Bank-USD",
			cheque_table=[row],
			cheque_table_2=[],
		)

		class _FakePE:
			def __init__(inner_self, d):
				inner_self.__dict__.update(d)
				inner_self.name = "PE-JOD-001"
				inner_self.flags = type("F", (), {"ignore_permissions": False})()

			def insert(inner_self):
				self._inserted = inner_self.__dict__.copy()

			def submit(inner_self):
				self._submitted = True

		def _get_doc(arg, *rest):
			if arg == "Multiple Cheque Entry":
				return doc
			return _FakePE(arg)

		self._frappe.get_doc = _get_doc

		class _DB:
			def get_value(self, doctype, name, field):
				if doctype == "Company":
					return "USD"
				if doctype == "Account":
					if name in ("USD-Receivable", "USD-Wallet"):
						return "USD"
				return None

			def set_value(self_, doctype, name, field, value):
				self._set_values.append((doctype, name, field, value))

		self._frappe.db = _DB()
		self._frappe.throw = lambda msg, exc=None: (_ for _ in ()).throw(Exception(msg))

	def test_paid_amount_is_usd_equivalent(self):
		"""Bug 2: paid_amount must be the USD equivalent (1000 × JOD_rate), not the JOD face value."""
		create_payment_entry_from_cheque("MCE-JOD", "ROW-JOD")
		pe = self._inserted
		expected_usd = 1000.0 * self._JOD_RATE
		self.assertAlmostEqual(pe.get("paid_amount"), expected_usd, places=4,
			msg="paid_amount must equal 1000 JOD × JOD→USD rate = {0} USD".format(expected_usd))

	def test_received_amount_equals_paid_amount(self):
		"""Bug 2: received_amount must equal paid_amount (both in USD)."""
		create_payment_entry_from_cheque("MCE-JOD", "ROW-JOD")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("paid_amount"), pe.get("received_amount"), places=4,
			msg="paid_amount and received_amount must be equal for same-currency accounts")

	def test_exchange_rates_are_one(self):
		"""JOD cheque in USD accounts: PE exchange rates must remain 1 (no PE-level conversion)."""
		create_payment_entry_from_cheque("MCE-JOD", "ROW-JOD")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("source_exchange_rate"), 1.0, places=6)
		self.assertAlmostEqual(pe.get("target_exchange_rate"), 1.0, places=6)

	def test_paid_amount_not_raw_jod_amount(self):
		"""Regression guard: paid_amount must NOT equal the raw JOD face value (1000)."""
		create_payment_entry_from_cheque("MCE-JOD", "ROW-JOD")
		pe = self._inserted
		self.assertNotAlmostEqual(pe.get("paid_amount"), 1000.0, places=1,
			msg="paid_amount must not be the raw JOD amount – it must be converted to USD")


# ---------------------------------------------------------------------------
# JOD→USD cross-currency: paid_from=USD (company), paid_to=JOD
# Issue: exch_party_to_mop=0.709 was wrongly used as source_exchange_rate,
# causing $410 Exchange Gain/Loss
# ---------------------------------------------------------------------------

class TestJodToUsdCrossAccountReceive(unittest.TestCase):
	"""JOD cheque with USD paid_from (company currency) and JOD paid_to account.

	Scenario (from PAY-2026-00041):
	  company_currency              = USD
	  paid_from account currency    = USD  (= company currency)
	  paid_to account currency      = JOD  (≠ company currency)
	  row.paid_amount               = 1,000 JOD (cheque face value)
	  row.amount_in_company_currency= 1,410.437 USD
	  row.target_exchange_rate      = 1.410
	  row.exchange_rate_party_to_mop= 0.709   (JOD → USD)

	Expected Payment Entry:
	  paid_amount          = 1,410.437 USD  (paid_from currency = company currency)
	  received_amount      = 1,000     JOD
	  source_exchange_rate = 1.0            (USD = company currency)
	  target_exchange_rate ≈ 1.410437       (JOD → USD)
	  Exchange Gain/Loss   = $0.00
	"""

	_JOD_RATE = 1.410
	_EXCH_PARTY_TO_MOP = 0.709
	_PAID_AMOUNT_USD = 1410.437
	_CHEQUE_JOD = 1000.0

	def _make_jod_cross_row(self):
		return _Row(
			name="ROW-JOD-CROSS",
			idx=1,
			account_paid_from="USD-Receivable",
			account_paid_to="JOD-Wallet",
			paid_amount=self._CHEQUE_JOD,
			amount_in_company_currency=self._PAID_AMOUNT_USD,
			target_exchange_rate=self._JOD_RATE,
			exchange_rate_mop_to_party=self._JOD_RATE,
			exchange_rate_party_to_mop=self._EXCH_PARTY_TO_MOP,
			cheque_currency="JOD",
			mode_of_payment="Cheque",
			party_type="Customer",
			party="CUST-JOD-CROSS",
			cheque_type="Crossed",
			reference_no="CHQ-JOD-CROSS-001",
			reference_date="2024-01-15",
			first_beneficiary="Company",
			person_name="Test",
			issuer_name="Test",
			picture_of_check=None,
			bank="JOD Bank",
			payment_entry=None,
		)

	def setUp(self):
		import sys
		self._frappe = sys.modules["frappe"]
		self._inserted = {}
		self._submitted = False
		self._set_values = []

		row = self._make_jod_cross_row()
		doc = _Doc(
			name="MCE-JOD-CROSS",
			company="USD Co",
			payment_type="Receive",
			posting_date="2024-01-15",
			mode_of_payment="Cheque",
			mode_of_payment_type="Cheque",
			cheque_bank="JOD Bank",
			bank_acc="Bank-JOD",
			cheque_table=[row],
			cheque_table_2=[],
		)

		class _FakePE:
			def __init__(inner_self, d):
				inner_self.__dict__.update(d)
				inner_self.name = "PE-JOD-CROSS-001"
				inner_self.flags = type("F", (), {"ignore_permissions": False})()

			def insert(inner_self):
				self._inserted = inner_self.__dict__.copy()

			def submit(inner_self):
				self._submitted = True

		def _get_doc(arg, *rest):
			if arg == "Multiple Cheque Entry":
				return doc
			return _FakePE(arg)

		self._frappe.get_doc = _get_doc

		class _DB:
			def get_value(self, doctype, name, field):
				if doctype == "Company":
					return "USD"
				if doctype == "Account":
					if name == "USD-Receivable":
						return "USD"
					if name == "JOD-Wallet":
						return "JOD"
				return None

			def set_value(self_, doctype, name, field, value):
				self._set_values.append((doctype, name, field, value))

		self._frappe.db = _DB()
		self._frappe.throw = lambda msg, exc=None: (_ for _ in ()).throw(Exception(msg))

	def test_source_exchange_rate_is_one(self):
		"""source_exchange_rate must be 1.0 because paid_from=USD=company_currency."""
		create_payment_entry_from_cheque("MCE-JOD-CROSS", "ROW-JOD-CROSS")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("source_exchange_rate"), 1.0, places=6,
			msg="source_exchange_rate must be 1 when paid_from = company currency (USD)")

	def test_target_exchange_rate_is_jod_to_usd(self):
		"""target_exchange_rate must reflect the JOD→USD rate (~1.410), not 1.0."""
		create_payment_entry_from_cheque("MCE-JOD-CROSS", "ROW-JOD-CROSS")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("target_exchange_rate"),
			self._PAID_AMOUNT_USD / self._CHEQUE_JOD, places=4,
			msg="target_exchange_rate must equal paid_amount_usd / jod_amount ≈ 1.410437")
		self.assertGreater(pe.get("target_exchange_rate"), 1.0,
			msg="target_exchange_rate must be > 1 for JOD→USD (JOD is worth more than USD)")

	def test_paid_amount_is_usd(self):
		"""paid_amount must be the USD equivalent (1410.437), not 1000 JOD."""
		create_payment_entry_from_cheque("MCE-JOD-CROSS", "ROW-JOD-CROSS")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("paid_amount"), self._PAID_AMOUNT_USD, places=3,
			msg="paid_amount must be the USD equivalent")

	def test_received_amount_is_jod(self):
		"""received_amount must equal the JOD cheque face value (1000)."""
		create_payment_entry_from_cheque("MCE-JOD-CROSS", "ROW-JOD-CROSS")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("received_amount"), self._CHEQUE_JOD, places=3,
			msg="received_amount must equal the JOD cheque amount")

	def test_no_exchange_gain_loss(self):
		"""GL base amounts must be equal → no Exchange Gain/Loss entry.

		base_paid     = paid_amount    × source_exchange_rate = 1410.437 × 1.0
		base_received = received_amount × target_exchange_rate = 1000 × 1.410437
		Both must equal 1410.437 USD.
		"""
		create_payment_entry_from_cheque("MCE-JOD-CROSS", "ROW-JOD-CROSS")
		pe = self._inserted
		base_paid = pe.get("paid_amount") * pe.get("source_exchange_rate")
		base_received = pe.get("received_amount") * pe.get("target_exchange_rate")
		self.assertAlmostEqual(base_paid, base_received, places=2,
			msg="GL imbalance would cause Exchange Gain/Loss: "
			"base_paid={0} ≠ base_received={1}".format(base_paid, base_received))

	def test_exch_party_to_mop_not_used_as_source(self):
		"""exchange_rate_party_to_mop (0.709) must NOT become source_exchange_rate."""
		create_payment_entry_from_cheque("MCE-JOD-CROSS", "ROW-JOD-CROSS")
		pe = self._inserted
		self.assertNotAlmostEqual(pe.get("source_exchange_rate"), self._EXCH_PARTY_TO_MOP,
			places=3,
			msg="exchange_rate_party_to_mop must not be used as source_exchange_rate "
			"when paid_from = company currency")


# ---------------------------------------------------------------------------
# Triple-currency Receive: company=USD, party=ILS, cheque/bank=JOD
# ---------------------------------------------------------------------------

class TestTripleCurrencyReceive(unittest.TestCase):
	"""company=USD, paid_from=ILS (party), paid_to=JOD (bank) – Receive.

	This is the exact scenario from the bug report:
	  company_currency              = USD
	  paid_from account currency    = ILS  (customer receivable)
	  paid_to   account currency    = JOD  (cheque/bank account)
	  row.paid_amount               = 1,000 JOD  (cheque face value)
	  row.amount_in_company_currency= 3,000 ILS   (fixed party-account amount)
	  row.target_exchange_rate      = 3.0          (JOD → ILS, NOT used for PE)
	  JOD → USD rate (Currency Exchange) = 1.410437

	Expected Payment Entry:
	  paid_amount          = 3,000 ILS  (strict copy of amount_in_company_currency)
	  received_amount      = 1,000 JOD
	  target_exchange_rate = 1.410437   (JOD → USD, fetched from Currency Exchange)
	  source_exchange_rate = base / paid_amount
	                       = (1000 × 1.410437) / 3000 ≈ 0.470146
	  GL balance: 3000 × 0.470146 ≈ 1000 × 1.410437 ≈ 1,410.437 USD
	"""

	_CHEQUE_JOD = 1000.0
	_ILS_AMOUNT = 3000.0
	_JOD_TO_USD = 1.410437
	_EXCH_MOP_TO_PARTY = 3.0    # JOD → ILS (stored on row, must NOT affect PE)

	def _make_row(self):
		return _Row(
			name="ROW-TRIPLE-RCV",
			idx=1,
			account_paid_from="ILS-Receivable",
			account_paid_to="JOD-Bank",
			paid_amount=self._CHEQUE_JOD,
			amount_in_company_currency=self._ILS_AMOUNT,
			target_exchange_rate=self._EXCH_MOP_TO_PARTY,
			exchange_rate_mop_to_party=self._EXCH_MOP_TO_PARTY,
			exchange_rate_party_to_mop=round(1.0 / self._EXCH_MOP_TO_PARTY, 9),
			cheque_currency="JOD",
			mode_of_payment="Cheque",
			party_type="Customer",
			party="CUST-TRIPLE",
			cheque_type="Crossed",
			reference_no="CHQ-TRIPLE-001",
			reference_date="2024-01-15",
			first_beneficiary="Company",
			person_name="Test",
			issuer_name="Test",
			picture_of_check=None,
			bank="JOD Bank",
			payment_entry=None,
		)

	def setUp(self):
		import sys
		self._frappe = sys.modules["frappe"]
		self._inserted = {}
		self._submitted = False
		self._set_values = []

		row = self._make_row()
		doc = _Doc(
			name="MCE-TRIPLE-RCV",
			company="USD Co",
			payment_type="Receive",
			posting_date="2024-01-15",
			mode_of_payment="Cheque",
			mode_of_payment_type="Cheque",
			cheque_bank="JOD Bank",
			bank_acc="Bank-JOD",
			cheque_table=[row],
			cheque_table_2=[],
		)

		class _FakePE:
			def __init__(inner_self, d):
				inner_self.__dict__.update(d)
				inner_self.name = "PE-TRIPLE-RCV-001"
				inner_self.flags = type("F", (), {"ignore_permissions": False})()

			def insert(inner_self):
				self._inserted = inner_self.__dict__.copy()

			def submit(inner_self):
				self._submitted = True

		def _get_doc(arg, *rest):
			if arg == "Multiple Cheque Entry":
				return doc
			return _FakePE(arg)

		self._frappe.get_doc = _get_doc

		_jod_to_usd = self._JOD_TO_USD

		class _DB:
			def get_value(self_, doctype, name, field, **kwargs):
				if doctype == "Company":
					return "USD"
				if doctype == "Account":
					if name == "ILS-Receivable":
						return "ILS"
					if name == "JOD-Bank":
						return "JOD"
				if doctype == "Currency Exchange":
					# _fetch_exchange_rate_to_company queries Currency Exchange.
					# Return JOD→USD rate.
					if isinstance(name, dict):
						fc = name.get("from_currency", "")
						tc = name.get("to_currency", "")
						if fc == "JOD" and tc == "USD":
							return _jod_to_usd
					return None
				return None

			def set_value(self_, doctype, name, field, value):
				self._set_values.append((doctype, name, field, value))

		self._frappe.db = _DB()
		self._frappe.throw = lambda msg, exc=None: (_ for _ in ()).throw(Exception(msg))

	def test_paid_amount_is_fixed_ils_value(self):
		"""paid_amount must be the fixed 3,000 ILS from amount_in_company_currency."""
		create_payment_entry_from_cheque("MCE-TRIPLE-RCV", "ROW-TRIPLE-RCV")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("paid_amount"), self._ILS_AMOUNT, places=3,
			msg="paid_amount must be the fixed ILS value (3000), not a recalculation")

	def test_paid_amount_is_not_recalculated_from_usd_ils_rate(self):
		"""paid_amount must NOT equal the system-recalculated ILS value (4,455 ILS).

		The old (buggy) behaviour fetched ILS→USD from Currency Exchange and then
		divided the USD base by that rate, producing ~4,455 ILS instead of 3,000 ILS.
		"""
		create_payment_entry_from_cheque("MCE-TRIPLE-RCV", "ROW-TRIPLE-RCV")
		pe = self._inserted
		# Approximate recalculated value if ILS→USD ≈ 0.3166:
		# base = 1000 × 1.410437 = 1410.437; paid = 1410.437 / 0.3166 ≈ 4455
		wrong_value = (self._CHEQUE_JOD * self._JOD_TO_USD) / 0.3166
		self.assertNotAlmostEqual(pe.get("paid_amount"), wrong_value, places=0,
			msg="paid_amount must not be the auto-recalculated value (~4455 ILS)")

	def test_received_amount_is_jod_cheque_value(self):
		"""received_amount must equal the JOD cheque face value (1,000 JOD)."""
		create_payment_entry_from_cheque("MCE-TRIPLE-RCV", "ROW-TRIPLE-RCV")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("received_amount"), self._CHEQUE_JOD, places=3,
			msg="received_amount must equal the original JOD cheque amount")

	def test_target_exchange_rate_is_jod_to_usd(self):
		"""target_exchange_rate must be the JOD→USD rate (1.410437)."""
		create_payment_entry_from_cheque("MCE-TRIPLE-RCV", "ROW-TRIPLE-RCV")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("target_exchange_rate"), self._JOD_TO_USD, places=4,
			msg="target_exchange_rate must be the JOD→USD rate from Currency Exchange")

	def test_source_exchange_rate_derived_from_balance(self):
		"""source_exchange_rate must be derived so the GL equation balances.

		source_rate = (received × target) / paid
		            = (1000 × 1.410437) / 3000 ≈ 0.470146
		"""
		create_payment_entry_from_cheque("MCE-TRIPLE-RCV", "ROW-TRIPLE-RCV")
		pe = self._inserted
		expected_source = (self._CHEQUE_JOD * self._JOD_TO_USD) / self._ILS_AMOUNT
		self.assertAlmostEqual(pe.get("source_exchange_rate"), expected_source, places=4,
			msg="source_exchange_rate must balance the GL equation")

	def test_gl_balance(self):
		"""paid_amount × source_rate must equal received_amount × target_rate (GL invariant)."""
		create_payment_entry_from_cheque("MCE-TRIPLE-RCV", "ROW-TRIPLE-RCV")
		pe = self._inserted
		base_paid = pe.get("paid_amount") * pe.get("source_exchange_rate")
		base_received = pe.get("received_amount") * pe.get("target_exchange_rate")
		self.assertAlmostEqual(base_paid, base_received, places=2,
			msg="GL imbalance: base_paid={0} ≠ base_received={1}".format(
				base_paid, base_received))


# ---------------------------------------------------------------------------
# Triple-currency Pay: company=USD, bank=JOD, party=ILS
# ---------------------------------------------------------------------------

class TestTripleCurrencyPay(unittest.TestCase):
	"""company=USD, paid_from=JOD (bank), paid_to=ILS (supplier) – Pay.

	Mirror of TestTripleCurrencyReceive for the Pay direction:
	  company_currency              = USD
	  paid_from account currency    = JOD  (cheque/bank account)
	  paid_to   account currency    = ILS  (supplier payable)
	  row.paid_amount               = 1,000 JOD  (cheque face value)
	  row.amount_in_company_currency= 3,000 ILS   (fixed party-account amount)
	  JOD → USD rate (Currency Exchange) = 1.410437

	Expected Payment Entry:
	  paid_amount          = 1,000 JOD
	  received_amount      = 3,000 ILS  (strict copy of amount_in_company_currency)
	  source_exchange_rate = 1.410437   (JOD → USD, fetched from Currency Exchange)
	  target_exchange_rate = base / received
	                       = (1000 × 1.410437) / 3000 ≈ 0.470146
	"""

	_CHEQUE_JOD = 1000.0
	_ILS_AMOUNT = 3000.0
	_JOD_TO_USD = 1.410437

	def _make_row(self):
		return _Row(
			name="ROW-TRIPLE-PAY",
			idx=1,
			account_paid_from="JOD-Bank",
			account_paid_to="ILS-Payable",
			paid_amount=self._CHEQUE_JOD,
			amount_in_company_currency=self._ILS_AMOUNT,
			target_exchange_rate=3.0,
			cheque_currency="JOD",
			mode_of_payment="Cheque",
			party_type="Supplier",
			party="SUPP-TRIPLE",
			cheque_type="Crossed",
			reference_no="CHQ-TRIPLE-PAY-001",
			reference_date="2024-01-15",
			first_beneficiary="Company",
			person_name="Test",
			issuer_name="Test",
			picture_of_check=None,
			bank="JOD Bank",
			payment_entry=None,
		)

	def setUp(self):
		import sys
		self._frappe = sys.modules["frappe"]
		self._inserted = {}
		self._submitted = False
		self._set_values = []

		row = self._make_row()
		doc = _Doc(
			name="MCE-TRIPLE-PAY",
			company="USD Co",
			payment_type="Pay",
			posting_date="2024-01-15",
			mode_of_payment="Cheque",
			mode_of_payment_type="Cheque",
			cheque_bank="JOD Bank",
			bank_acc="Bank-JOD",
			cheque_table=[],
			cheque_table_2=[row],
		)

		class _FakePE:
			def __init__(inner_self, d):
				inner_self.__dict__.update(d)
				inner_self.name = "PE-TRIPLE-PAY-001"
				inner_self.flags = type("F", (), {"ignore_permissions": False})()

			def insert(inner_self):
				self._inserted = inner_self.__dict__.copy()

			def submit(inner_self):
				self._submitted = True

		def _get_doc(arg, *rest):
			if arg == "Multiple Cheque Entry":
				return doc
			return _FakePE(arg)

		self._frappe.get_doc = _get_doc

		_jod_to_usd = self._JOD_TO_USD

		class _DB:
			def get_value(self_, doctype, name, field, **kwargs):
				if doctype == "Company":
					return "USD"
				if doctype == "Account":
					if name == "JOD-Bank":
						return "JOD"
					if name == "ILS-Payable":
						return "ILS"
				if doctype == "Currency Exchange":
					if isinstance(name, dict):
						fc = name.get("from_currency", "")
						tc = name.get("to_currency", "")
						if fc == "JOD" and tc == "USD":
							return _jod_to_usd
					return None
				return None

			def set_value(self_, doctype, name, field, value):
				self._set_values.append((doctype, name, field, value))

		self._frappe.db = _DB()
		self._frappe.throw = lambda msg, exc=None: (_ for _ in ()).throw(Exception(msg))

	def test_paid_amount_is_jod_cheque_value(self):
		"""paid_amount must equal the JOD cheque face value (1,000 JOD)."""
		create_payment_entry_from_cheque("MCE-TRIPLE-PAY", "ROW-TRIPLE-PAY")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("paid_amount"), self._CHEQUE_JOD, places=3,
			msg="paid_amount must equal the original JOD cheque amount")

	def test_received_amount_is_fixed_ils_value(self):
		"""received_amount must be the fixed 3,000 ILS from amount_in_company_currency."""
		create_payment_entry_from_cheque("MCE-TRIPLE-PAY", "ROW-TRIPLE-PAY")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("received_amount"), self._ILS_AMOUNT, places=3,
			msg="received_amount must be the fixed ILS value (3000), not a recalculation")

	def test_source_exchange_rate_is_jod_to_usd(self):
		"""source_exchange_rate must be the JOD→USD rate (1.410437)."""
		create_payment_entry_from_cheque("MCE-TRIPLE-PAY", "ROW-TRIPLE-PAY")
		pe = self._inserted
		self.assertAlmostEqual(pe.get("source_exchange_rate"), self._JOD_TO_USD, places=4,
			msg="source_exchange_rate must be the JOD→USD rate from Currency Exchange")

	def test_target_exchange_rate_derived_from_balance(self):
		"""target_exchange_rate must be derived so the GL equation balances.

		target_rate = (paid × source) / received
		            = (1000 × 1.410437) / 3000 ≈ 0.470146
		"""
		create_payment_entry_from_cheque("MCE-TRIPLE-PAY", "ROW-TRIPLE-PAY")
		pe = self._inserted
		expected_target = (self._CHEQUE_JOD * self._JOD_TO_USD) / self._ILS_AMOUNT
		self.assertAlmostEqual(pe.get("target_exchange_rate"), expected_target, places=4,
			msg="target_exchange_rate must balance the GL equation")

	def test_gl_balance(self):
		"""paid_amount × source_rate must equal received_amount × target_rate (GL invariant)."""
		create_payment_entry_from_cheque("MCE-TRIPLE-PAY", "ROW-TRIPLE-PAY")
		pe = self._inserted
		base_paid = pe.get("paid_amount") * pe.get("source_exchange_rate")
		base_received = pe.get("received_amount") * pe.get("target_exchange_rate")
		self.assertAlmostEqual(base_paid, base_received, places=2,
			msg="GL imbalance: base_paid={0} ≠ base_received={1}".format(
				base_paid, base_received))
