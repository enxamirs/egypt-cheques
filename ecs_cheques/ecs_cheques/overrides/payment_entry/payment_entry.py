# -*- coding: utf-8 -*-
# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe, json
from frappe.model.document import Document
from frappe import _
from frappe.desk.search import sanitize_searchfield
from frappe.utils import (flt, getdate, get_url, now,
nowtime, get_time, today, get_datetime, add_days)
from frappe.utils import add_to_date, now, nowdate


def _get_account_currency(account_name, company_currency):
    """Return the account's currency, or company_currency if not found."""
    if not account_name:
        return company_currency
    acc_currency = frappe.db.get_value("Account", account_name, "account_currency")
    return acc_currency or company_currency


def _je_account(account, amount_company, is_debit, doc, company_currency,
                 party_type=None, party=None, user_remark=None,
                 amount_in_account_currency=None):
    """
    Build a Journal Entry Account dict with correct company-currency and
    in-account-currency amounts, plus the exchange_rate for the account.

    * amount_company  – the amount expressed in company currency.
    * is_debit        – True for a debit entry, False for a credit entry.
    * amount_in_account_currency – (optional) when provided, this exact value
      is used as the account-currency amount instead of deriving it from
      ``amount_company / exchange_rate``.  This is essential for tri-currency
      scenarios (e.g. party=ILS, MOP=JOD, company=USD) where deriving the
      amount from a single exchange-rate pair can produce incorrect values
      (e.g. using ``paid_amount`` for both legs instead of ``received_amount``
      for the target account).
    """
    account_currency = _get_account_currency(account, company_currency)

    if account_currency == company_currency:
        exchange_rate = 1.0
        amount_in_acc = amount_company
    elif amount_in_account_currency is not None:
        # Caller supplied the authoritative account-currency amount (e.g.
        # doc.paid_amount for the party account, doc.received_amount for the
        # MOP account).  Derive the exchange rate so that:
        #   amount_in_acc × exchange_rate ≈ amount_company
        amount_in_acc = flt(amount_in_account_currency, 9)
        exchange_rate = flt(amount_company / amount_in_acc, 9) if amount_in_acc else 1.0
    elif account_currency == (doc.paid_to_account_currency or ""):
        exchange_rate = flt(doc.target_exchange_rate) or 1.0
        amount_in_acc = flt(amount_company / exchange_rate, 9)
    elif account_currency == (doc.paid_from_account_currency or ""):
        exchange_rate = flt(doc.source_exchange_rate) or 1.0
        amount_in_acc = flt(amount_company / exchange_rate, 9)
    else:
        exchange_rate = 1.0
        amount_in_acc = amount_company

    entry = {
        "doctype": "Journal Entry Account",
        "account": account,
        "exchange_rate": exchange_rate,
        "debit": amount_company if is_debit else 0,
        "credit": 0 if is_debit else amount_company,
        "debit_in_account_currency": amount_in_acc if is_debit else 0,
        "credit_in_account_currency": 0 if is_debit else amount_in_acc,
        "user_remark": user_remark or doc.name,
    }
    if party_type:
        entry["party_type"] = party_type
    if party:
        entry["party"] = party
    return entry


def _needs_multi_currency(account_names, company_currency):
    """Return True if any of the given accounts has a non-company currency."""
    for name in account_names:
        if name and _get_account_currency(name, company_currency) != company_currency:
            return True
    return False


def _get_cheque_paid_amount(doc, company_currency):
    """Return paid_amount expressed in company currency, derived from Cheque Table Receive.

    For Receive-type Payment Entries that are linked to a Cheque Table Receive row
    (via doc.cheque_table_no), the canonical amount for the Journal Entry must be
    consistent with the exchange rates stored in the Payment Entry.

    When ``exchange_rate_party_to_mop`` is set on the Cheque Table Receive row the
    Payment Entry was created with that value as ``source_exchange_rate``, so the
    company-currency base is ``doc.paid_amount × source_exchange_rate``.

    Otherwise the legacy path is used: ``ctr.paid_amount × ctr.target_exchange_rate``.

    Also updates doc.target_exchange_rate in-memory (legacy path only) so that
    _je_account uses the same rate for non-company-currency accounts.

    Returns the paid_amount_company (float).
    """
    if not doc.cheque_table_no:
        return flt(doc.paid_amount) * (flt(doc.source_exchange_rate) or 1.0)

    ctr = frappe.db.get_value(
        "Cheque Table Receive",
        doc.cheque_table_no,
        ["paid_amount", "target_exchange_rate", "exchange_rate_party_to_mop",
         "account_currency_from", "account_currency"],
        as_dict=True,
    )
    if not ctr:
        frappe.throw(
            _(
                "Cheque Table Receive '{0}' was not found. "
                "Cannot compute Journal Entry amount."
            ).format(doc.cheque_table_no)
        )

    ctr_paid = flt(ctr.paid_amount)
    if ctr_paid <= 0:
        frappe.throw(
            _(
                "Cheque Table Receive '{0}': paid_amount must be greater than zero "
                "before creating a Journal Entry."
            ).format(doc.cheque_table_no)
        )

    exch_party_to_mop = flt(ctr.get("exchange_rate_party_to_mop") or 0)

    # If the Payment Entry itself has the same currency on both sides, any stored
    # exchange_rate_party_to_mop is meaningless (e.g. a stale value left over from
    # when the accounts had different currencies).  Clear it so the fallback path
    # (ctr.paid_amount × ctr.target_exchange_rate) is used instead of the
    # bidirectional-rate path that would raise a false mismatch error.
    pe_paid_from_currency = getattr(doc, "paid_from_account_currency", None) or ""
    pe_paid_to_currency = getattr(doc, "paid_to_account_currency", None) or ""
    if pe_paid_from_currency and pe_paid_to_currency and pe_paid_from_currency == pe_paid_to_currency:
        exch_party_to_mop = 0

    # Detect the "same-currency pair" scenario: both accounts share the same
    # non-company currency (e.g. both ILS when company currency is USD).
    # In that case exchange_rate_party_to_mop = 1.0 is meaningless for
    # company-currency conversion and must not be used in the rate check.
    ctr_from_currency = ctr.get("account_currency_from") or ""
    ctr_to_currency = ctr.get("account_currency") or ""
    same_non_company_currency = (
        ctr_from_currency
        and ctr_to_currency
        and ctr_from_currency == ctr_to_currency
        and ctr_from_currency != company_currency
    )

    if exch_party_to_mop > 0 and not same_non_company_currency:
        # Bidirectional rate path: company-currency base is
        # PE.paid_amount × source_exchange_rate (= exchange_rate_party_to_mop).
        # Exception: when paid_from is already in company currency, source_exchange_rate
        # is necessarily 1.0 and does not equal exch_party_to_mop – fall through to the
        # legacy path (ctr.paid_amount × ctr.target_exchange_rate) instead.
        if not (pe_paid_from_currency and pe_paid_from_currency == company_currency):
            pe_source = flt(doc.source_exchange_rate) or 1.0
            if abs(pe_source - exch_party_to_mop) / exch_party_to_mop > 0.01:
                # Rates have drifted (e.g. the cheque was originally recorded at a
                # different rate than the current Payment Entry). The PE has already
                # been validated by ERPNext to ensure both sides balance in company
                # currency, so it is the authoritative source of truth.  Synchronise
                # the stored rate on the Cheque Table Receive row so that future
                # lookups remain consistent, then proceed with the PE rate.
                frappe.db.set_value(
                    "Cheque Table Receive",
                    doc.cheque_table_no,
                    "exchange_rate_party_to_mop",
                    pe_source,
                )
            return flt(flt(doc.paid_amount) * pe_source, 9)

    if same_non_company_currency:
        # Both accounts share the same non-company currency: trust the PE's
        # source_exchange_rate (set by ERPNext validate) for company-currency conversion.
        pe_source = flt(doc.source_exchange_rate) or 1.0
        return flt(flt(doc.paid_amount) * pe_source, 9)

    # Legacy path: company-currency base from ctr.paid_amount × target_exchange_rate.
    ctr_rate = flt(ctr.target_exchange_rate) or 1.0
    paid_amount_company = flt(ctr_paid * ctr_rate, 9)

    # Validate consistency between the Cheque Table and the Payment Entry.
    pe_base = flt(doc.paid_amount) * (flt(doc.source_exchange_rate) or 1.0)
    if pe_base and abs(paid_amount_company - pe_base) / pe_base > 0.01:
        frappe.throw(
            _(
                "Cheque Table Receive '{0}' amount in company currency ({1}) does not "
                "match the Payment Entry base amount ({2}). "
                "Please correct the paid_amount or exchange rate before creating a "
                "Journal Entry."
            ).format(doc.cheque_table_no, paid_amount_company, pe_base)
        )

    # Ensure _je_account uses the rate that matches the cheque table so that
    # both sides of every JE use the same exchange rate.
    if flt(doc.target_exchange_rate) != ctr_rate:
        doc.target_exchange_rate = ctr_rate

    return paid_amount_company


@frappe.whitelist()
def cheque(doc, method=None):
    default_payback_cheque_wallet_account = frappe.db.get_value("Company", doc.company, "default_payback_cheque_wallet_account")
    default_rejected_cheque_account = frappe.db.get_value("Company", doc.company, "default_rejected_cheque_account")
    default_cash_account = frappe.db.get_value("Company", doc.company, "default_cash_account")
    default_bank_commissions_account = frappe.db.get_value("Company", doc.company, "default_bank_commissions_account")

    # Company currency and the payment amount expressed in company currency.
    # Always derived from the linked Cheque Table Receive (when present) so that
    # both debit and credit accounts use the same base amount, preventing the
    # "Total Debit must equal Total Credit" validation error.
    company_currency = frappe.db.get_value("Company", doc.company, "default_currency") or ""
    paid_amount_company = _get_cheque_paid_amount(doc, company_currency)

    if not doc.cheque_bank and doc.cheque_action == "إيداع شيك تحت التحصيل":
        frappe.throw(_(" برجاء تحديد البنك والحساب البنكي "))

    if not doc.bank_acc and doc.cheque_action == "إيداع شيك تحت التحصيل":
        frappe.throw(_("برجاء تحديد الحساب البنكي"))

    if not doc.account and doc.cheque_action == "إيداع شيك تحت التحصيل" and doc.with_bank_commission:
        frappe.throw(_(" برجاء تحديد الحساب الجاري داخل الحساب البنكي وإعادة إختيار الحساب البنكي مرة أخرى "))

    if not doc.account and doc.cheque_action == "صرف شيك تحت التحصيل":
        frappe.throw(_(" برجاء تحديد الحساب الجاري داخل الحساب البنكي وإعادة إختيار الحساب البنكي مرة أخرى "))

    if not doc.account and doc.cheque_action == "رفض شيك تحت التحصيل" and doc.with_bank_commission:
        frappe.throw(_(" برجاء تحديد الحساب الجاري داخل الحساب البنكي وإعادة إختيار الحساب البنكي مرة أخرى "))

    if not doc.account and doc.cheque_action == "صرف الشيك":
        frappe.throw(_(" برجاء تحديد الحساب الجاري داخل الحساب البنكي وإعادة إختيار الحساب البنكي مرة أخرى "))

    if not doc.collection_fee_account and doc.cheque_action == "إيداع شيك تحت التحصيل":
        frappe.throw(_(" برجاء تحديد حساب برسم التحصيل داخل الحساب البنكي وإعادة إختيار الحساب البنكي مرة أخرى "))

    if not doc.collection_fee_account and doc.cheque_action == "صرف شيك تحت التحصيل":
        frappe.throw(_(" برجاء تحديد حساب برسم التحصيل داخل الحساب البنكي وإعادة إختيار الحساب البنكي مرة أخرى "))

    if not doc.collection_fee_account and doc.cheque_action == "رفض شيك تحت التحصيل":
        frappe.throw(_(" برجاء تحديد حساب برسم التحصيل داخل الحساب البنكي وإعادة إختيار الحساب البنكي مرة أخرى "))

    if not doc.payable_account and doc.cheque_action == "صرف الشيك":
        frappe.throw(_(" برجاء تحديد حساب برسم الدفع داخل الحساب البنكي وإعادة إختيار الحساب البنكي مرة أخرى "))

    if doc.cheque_action == "تحويل إلى حافظة شيكات أخرى":
        new_mode_of_payment_account = frappe.db.get_value('Mode of Payment Account', {'parent': doc.new_mode_of_payment}, 'default_account')
        old_mode_of_payment_account = frappe.db.get_value("Mode of Payment Account", {'parent': doc.mode_of_payment}, 'default_account')
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action = "" where name = %s""", doc.name)
        if not new_mode_of_payment_account == old_mode_of_payment_account:
            accounts = [
                _je_account(new_mode_of_payment_account, paid_amount_company, True, doc, company_currency),
                _je_account(old_mode_of_payment_account, paid_amount_company, False, doc, company_currency),
            ]
            new_doc = frappe.get_doc({
                "doctype": "Journal Entry",
                "voucher_type": "Bank Entry",
                "reference_doctype": "Payment Entry",
                "reference_link": doc.name,
                "cheque_no": doc.reference_no,
                "cheque_date": doc.reference_date,
                "pe_status": "حافظة شيكات واردة",
                "posting_date": doc.cheque_action_date,
                "multi_currency": 1 if _needs_multi_currency([new_mode_of_payment_account, old_mode_of_payment_account], company_currency) else 0,
                "accounts": accounts,
                "payment_type": doc.payment_type,
                "user_remark": doc.party_name

            })
            new_doc.insert()
            new_doc.submit()
            #frappe.db.sql(""" update `tabPayment Entry` set cheque_action_date = NULL where name = %s""", doc.name)
            #doc.reload()

        x = str(doc.logs) + "\n" + str(doc.new_mode_of_payment) + " " + str(doc.cheque_action_date)
        frappe.db.set_value('Payment Entry', doc.name, 'logs', x)
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action = "" where name = %s""", doc.name)
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action_date = NULL where name = %s""", doc.name)
        doc.reload()

    if doc.cheque_action == "تحصيل فوري للشيك":
        frappe.db.sql("""update `tabPayment Entry` set clearance_date = %s where name=%s """, (doc.cheque_action_date, doc.name))
        frappe.db.sql(""" update `tabPayment Entry` set cheque_status = "محصل فوري" where name = %s""", doc.name)
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action = "" where name = %s""", doc.name)
        accounts = [
            _je_account(default_cash_account, paid_amount_company, True, doc, company_currency),
            _je_account(doc.paid_to, paid_amount_company, False, doc, company_currency,
                        amount_in_account_currency=flt(doc.received_amount)),
        ]
        new_doc = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Bank Entry",
            "reference_doctype": "Payment Entry",
            "reference_link": doc.name,
            "cheque_no": doc.reference_no,
            "cheque_date": doc.reference_date,
            "pe_status": "محصل فوري",
            "posting_date": doc.cheque_action_date,
            "multi_currency": 1 if _needs_multi_currency([default_cash_account, doc.paid_to], company_currency) else 0,
            "accounts": accounts,
            "payment_type": doc.payment_type,
            "user_remark": doc.party_name

        })
        new_doc.insert()
        new_doc.submit()
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action_date = NULL where name = %s""", doc.name)
        doc.reload()

    if doc.cheque_action == "إيداع شيك تحت التحصيل" and doc.with_bank_commission and not doc.cheque_status == "مرفوض بالبنك":
        frappe.db.sql(""" update `tabPayment Entry` set cheque_status = "تحت التحصيل" where name = %s""", doc.name)
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action = "" where name = %s""", doc.name)
        accounts = [
            _je_account(doc.collection_fee_account, paid_amount_company, True, doc, company_currency),
            _je_account(default_bank_commissions_account, flt(doc.co3_), True, doc, company_currency),
            _je_account(doc.paid_to, paid_amount_company, False, doc, company_currency,
                        amount_in_account_currency=flt(doc.received_amount)),
            _je_account(doc.account, flt(doc.co3_), False, doc, company_currency),
        ]
        new_doc = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Bank Entry",
            "reference_doctype": "Payment Entry",
            "reference_link": doc.name,
            "cheque_no": doc.reference_no,
            "cheque_date": doc.reference_date,
            "pe_status": "تحت التحصيل",
            "posting_date": doc.cheque_action_date,
            "multi_currency": 1 if _needs_multi_currency([doc.collection_fee_account, default_bank_commissions_account, doc.paid_to, doc.account], company_currency) else 0,
            "accounts": accounts,
            "payment_type": doc.payment_type,
            "user_remark": doc.party_name
        })
        new_doc.insert()
        new_doc.submit()
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action_date = NULL where name = %s""", doc.name)
        doc.reload()

    if doc.cheque_action == "إيداع شيك تحت التحصيل" and not doc.with_bank_commission and not doc.cheque_status == "مرفوض بالبنك":
        frappe.db.sql(""" update `tabPayment Entry` set cheque_status = "تحت التحصيل" where name = %s""", doc.name)
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action = "" where name = %s""", doc.name)
        accounts = [
            _je_account(doc.collection_fee_account, paid_amount_company, True, doc, company_currency),
            _je_account(doc.paid_to, paid_amount_company, False, doc, company_currency,
                        amount_in_account_currency=flt(doc.received_amount)),
        ]
        new_doc = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Bank Entry",
            "reference_doctype": "Payment Entry",
            "reference_link": doc.name,
            "cheque_no": doc.reference_no,
            "cheque_date": doc.reference_date,
            "pe_status": "تحت التحصيل",
            "posting_date": doc.cheque_action_date,
            "multi_currency": 1 if _needs_multi_currency([doc.collection_fee_account, doc.paid_to], company_currency) else 0,
            "accounts": accounts,
            "payment_type": doc.payment_type,
            "user_remark": doc.party_name
        })
        new_doc.insert()
        new_doc.submit()
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action_date = NULL where name = %s""", doc.name)
        doc.reload()

    if doc.cheque_action == "إيداع شيك تحت التحصيل" and not doc.with_bank_commission and doc.cheque_status == "مرفوض بالبنك":
        frappe.db.sql(""" update `tabPayment Entry` set cheque_status = "تحت التحصيل" where name = %s""", doc.name)
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action = "" where name = %s""", doc.name)
        accounts = [
            _je_account(doc.collection_fee_account, paid_amount_company, True, doc, company_currency),
            _je_account(default_payback_cheque_wallet_account, paid_amount_company, False, doc, company_currency),
        ]
        new_doc = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Bank Entry",
            "reference_doctype": "Payment Entry",
            "reference_link": doc.name,
            "cheque_no": doc.reference_no,
            "cheque_date": doc.reference_date,
            "pe_status": "تحت التحصيل 2",
            "posting_date": doc.cheque_action_date,
            "multi_currency": 1 if _needs_multi_currency([doc.collection_fee_account, default_payback_cheque_wallet_account], company_currency) else 0,
            "accounts": accounts,
            "payment_type": doc.payment_type,
            "user_remark": doc.party_name
        })
        new_doc.insert()
        new_doc.submit()
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action_date = NULL where name = %s""", doc.name)
        doc.reload()


    if doc.cheque_action == "إرجاع لحافظة شيكات واردة" and not doc.with_bank_commission and doc.cheque_status == "مرفوض بالبنك":
        frappe.db.sql(""" update `tabPayment Entry` set cheque_status = "حافظة شيكات واردة" where name = %s""", doc.name)
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action = "" where name = %s""", doc.name)
        accounts = [
            _je_account(doc.paid_to, paid_amount_company, True, doc, company_currency,
                        amount_in_account_currency=flt(doc.received_amount)),
            _je_account(default_rejected_cheque_account, paid_amount_company, False, doc, company_currency),
        ]
        new_doc = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Bank Entry",
            "reference_doctype": "Payment Entry",
            "reference_link": doc.name,
            "cheque_no": doc.reference_no,
            "cheque_date": doc.reference_date,
            "pe_status": "حافظة شيكات واردة",
            "posting_date": doc.cheque_action_date,
            "multi_currency": 1 if _needs_multi_currency([doc.paid_to, default_rejected_cheque_account], company_currency) else 0,
            "accounts": accounts,
            "payment_type": doc.payment_type,
            "user_remark": doc.party_name
        })
        new_doc.insert()
        new_doc.submit()
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action_date = NULL where name = %s""", doc.name)
        doc.reload()

    if doc.cheque_action == "رد شيك" and not doc.with_bank_commission and doc.cheque_status == "مرفوض بالبنك":
        frappe.db.sql(""" update `tabPayment Entry` set cheque_status = "مردود" where name = %s""", doc.name)
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action = "" where name = %s""", doc.name)
        accounts = [
            _je_account(doc.paid_from, paid_amount_company, True, doc, company_currency,
                        party_type="Customer", party=doc.party,
                        amount_in_account_currency=flt(doc.paid_amount)),
            _je_account(doc.paid_to, paid_amount_company, False, doc, company_currency,
                        amount_in_account_currency=flt(doc.received_amount)),
        ]
        new_doc = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Bank Entry",
            "reference_doctype": "Payment Entry",
            "reference_link": doc.name,
            "cheque_no": doc.reference_no,
            "cheque_date": doc.reference_date,
            "pe_status": "مردود 2",
            "posting_date": doc.cheque_action_date,
            "multi_currency": 1 if _needs_multi_currency([doc.paid_from, doc.paid_to], company_currency) else 0,
            "accounts": accounts,
            "payment_type": doc.payment_type,
            "user_remark": doc.party_name
        })
        new_doc.insert()
        new_doc.submit()
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action_date = NULL where name = %s""", doc.name)
        doc.reload()

    if doc.cheque_action == "إيداع شيك تحت التحصيل" and doc.with_bank_commission and doc.cheque_status == "مرفوض بالبنك":
        frappe.db.sql(""" update `tabPayment Entry` set cheque_status = "تحت التحصيل" where name = %s""", doc.name)
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action = "" where name = %s""", doc.name)
        accounts = [
            _je_account(doc.collection_fee_account, paid_amount_company, True, doc, company_currency),
            _je_account(default_bank_commissions_account, flt(doc.co3_), True, doc, company_currency),
            _je_account(default_payback_cheque_wallet_account, paid_amount_company, False, doc, company_currency),
            _je_account(doc.account, flt(doc.co3_), False, doc, company_currency),
        ]
        new_doc = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Bank Entry",
            "reference_doctype": "Payment Entry",
            "reference_link": doc.name,
            "cheque_no": doc.reference_no,
            "cheque_date": doc.reference_date,
            "pe_status": "تحت التحصيل 2",
            "posting_date": doc.cheque_action_date,
            "multi_currency": 1 if _needs_multi_currency([doc.collection_fee_account, default_bank_commissions_account, default_payback_cheque_wallet_account, doc.account], company_currency) else 0,
            "accounts": accounts,
            "payment_type": doc.payment_type,
            "user_remark": doc.party_name
        })
        new_doc.insert()
        new_doc.submit()
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action_date = NULL where name = %s""", doc.name)
        doc.reload()

    if doc.cheque_action == "صرف شيك تحت التحصيل":
        frappe.db.sql("""update `tabPayment Entry` set clearance_date = %s where name=%s """, (doc.cheque_action_date, doc.name))
        frappe.db.sql(""" update `tabPayment Entry` set cheque_status = "محصل" where name = %s""", doc.name)
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action = "" where name = %s""", doc.name)
        accounts = [
            _je_account(doc.account, paid_amount_company, True, doc, company_currency),
            _je_account(doc.collection_fee_account, paid_amount_company, False, doc, company_currency),
        ]
        new_doc = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Bank Entry",
            "reference_doctype": "Payment Entry",
            "reference_link": doc.name,
            "cheque_no": doc.reference_no,
            "cheque_date": doc.reference_date,
            "pe_status": "محصل",
            "posting_date": doc.cheque_action_date,
            "multi_currency": 1 if _needs_multi_currency([doc.account, doc.collection_fee_account], company_currency) else 0,
            "accounts": accounts,
            "payment_type": doc.payment_type,
            "user_remark": doc.party_name
        })
        new_doc.insert()
        new_doc.submit()
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action_date = NULL where name = %s""", doc.name)
        doc.reload()

    if doc.cheque_action == "رفض شيك تحت التحصيل" and doc.with_bank_commission:
        frappe.db.sql(""" update `tabPayment Entry` set cheque_status = "مرفوض بالبنك" where name = %s""",
                      doc.name)
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action = "" where name = %s""", doc.name)
        accounts = [
            _je_account(default_payback_cheque_wallet_account, paid_amount_company, True, doc, company_currency),
            _je_account(default_bank_commissions_account, flt(doc.co5_), True, doc, company_currency),
            _je_account(doc.collection_fee_account, paid_amount_company, False, doc, company_currency),
            _je_account(doc.account, flt(doc.co5_), False, doc, company_currency),
        ]
        new_doc = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Bank Entry",
            "reference_doctype": "Payment Entry",
            "reference_link": doc.name,
            "cheque_no": doc.reference_no,
            "cheque_date": doc.reference_date,
            "pe_status": "مرفوض بالبنك",
            "posting_date": doc.cheque_action_date,
            "multi_currency": 1 if _needs_multi_currency([default_payback_cheque_wallet_account, default_bank_commissions_account, doc.collection_fee_account, doc.account], company_currency) else 0,
            "accounts": accounts,
            "payment_type": doc.payment_type,
            "user_remark": doc.party_name
        })
        new_doc.insert()
        new_doc.submit()
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action_date = NULL where name = %s""", doc.name)
        doc.reload()

    if doc.cheque_action == "رفض شيك تحت التحصيل" and not doc.with_bank_commission:
        frappe.db.sql(""" update `tabPayment Entry` set cheque_status = "مرفوض بالبنك" where name = %s""",
                      doc.name)
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action = "" where name = %s""", doc.name)
        accounts = [
            _je_account(default_payback_cheque_wallet_account, paid_amount_company, True, doc, company_currency),
            _je_account(doc.collection_fee_account, paid_amount_company, False, doc, company_currency),
        ]
        new_doc = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Bank Entry",
            "reference_doctype": "Payment Entry",
            "reference_link": doc.name,
            "cheque_no": doc.reference_no,
            "cheque_date": doc.reference_date,
            "pe_status": "مرفوض بالبنك",
            "posting_date": doc.cheque_action_date,
            "multi_currency": 1 if _needs_multi_currency([default_payback_cheque_wallet_account, doc.collection_fee_account], company_currency) else 0,
            "accounts": accounts,
            "payment_type": doc.payment_type,
            "user_remark": doc.party_name
        })
        new_doc.insert()
        new_doc.submit()
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action_date = NULL where name = %s""", doc.name)
        doc.reload()

    if doc.cheque_action == "تظهير شيك":
        frappe.db.sql(""" update `tabPayment Entry` set cheque_status = "مظهر" where name = %s""", doc.name)
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action = "" where name = %s""", doc.name)
        accounts = [
            _je_account(doc.account_1, paid_amount_company, True, doc, company_currency,
                        party_type=doc.party_type_, party=doc.party_),
            _je_account(doc.paid_to, paid_amount_company, False, doc, company_currency,
                        amount_in_account_currency=flt(doc.received_amount)),
        ]
        new_doc = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Bank Entry",
            "reference_doctype": "Payment Entry",
            "reference_link": doc.name,
            "cheque_no": doc.reference_no,
            "cheque_date": doc.reference_date,
            "pe_status": "مظهر",
            "posting_date": doc.cheque_action_date,
            "multi_currency": 1 if _needs_multi_currency([doc.account_1, doc.paid_to], company_currency) else 0,
            "accounts": accounts,
            "payment_type": doc.payment_type,
            "user_remark": doc.party_name
        })
        new_doc.insert()
        new_doc.submit()
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action_date = NULL where name = %s""", doc.name)
        doc.reload()

    if not doc.encashment_amount and doc.cheque_action == "تسييل الشيك":
        frappe.throw(_("برجاء إدخال مبلغ التسييل"))

    if doc.encashment_amount > doc.paid_amount and doc.cheque_action == "تسييل الشيك":
        frappe.throw(_("مبلغ التسييل لا يمكن أن يكون أكبر من مبلغ الشيك"))
        doc.reload()

    if doc.encashed_amount > doc.paid_amount and doc.cheque_action == "تسييل الشيك":
        frappe.throw(_("مبلغ التسييل لا يمكن أن يكون أكبر من المبلغ الغير مسيل"))
        doc.reload()

    if doc.cheque_action == "تسييل الشيك":
        frappe.db.sql(""" update `tabPayment Entry` set cheque_status = "حافظة شيكات مرجعة" where name = %s""",
                      doc.name)
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action = "" where name = %s""", doc.name)
        accounts = [
            _je_account(default_cash_account, flt(doc.encashment_amount), True, doc, company_currency),
            _je_account(default_payback_cheque_wallet_account, flt(doc.encashment_amount), False, doc, company_currency),
        ]
        new_doc = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Bank Entry",
            "reference_doctype": "Payment Entry",
            "reference_link": doc.name,
            "cheque_no": doc.reference_no,
            "cheque_date": doc.reference_date,
            "pe_status": "حافظة شيكات مرجعة",
            "posting_date": doc.cheque_action_date,
            "multi_currency": 1 if _needs_multi_currency([default_cash_account, default_payback_cheque_wallet_account], company_currency) else 0,
            "accounts": accounts,
            "payment_type": doc.payment_type,
            "user_remark": doc.party_name
        })
        new_doc.insert()
        new_doc.submit()
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action_date = NULL where name = %s""", doc.name)
        frappe.db.sql(""" update `tabPayment Entry` set encashment_amount = 0 where name = %s""", doc.name)
        doc.reload()

    if doc.cheque_action == "رد شيك" and doc.cheque_status == "حافظة شيكات واردة":
        frappe.db.sql(""" update `tabPayment Entry` set cheque_status = "مردود" where name = %s""", doc.name)
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action = "" where name = %s""", doc.name)
        doc.reload()

        accounts = [
            _je_account(doc.paid_from, paid_amount_company, True, doc, company_currency,
                        party_type=doc.party_type, party=doc.party,
                        amount_in_account_currency=flt(doc.paid_amount)),
            _je_account(doc.paid_to, paid_amount_company, False, doc, company_currency,
                        amount_in_account_currency=flt(doc.received_amount)),
        ]
        new_doc = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Bank Entry",
            "reference_doctype": "Payment Entry",
            "reference_link": doc.name,
            "cheque_no": doc.reference_no,
            "cheque_date": doc.reference_date,
            "pe_status": "مردود 1",
            "posting_date": doc.cheque_action_date,
            "multi_currency": 1 if _needs_multi_currency([doc.paid_from, doc.paid_to], company_currency) else 0,
            "accounts": accounts,
            "payment_type": doc.payment_type,
            "user_remark": doc.party_name
        })
        new_doc.insert()
        new_doc.submit()
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action_date = NULL where name = %s""", doc.name)
        doc.reload()

    if not doc.bank_acc and doc.cheque_action in ("سحب الشيك", "صرف الشيك"):
        frappe.throw(_("برجاء تحديد الحساب البنكي"))

    if doc.cheque_action == "صرف الشيك" and doc.payment_type in ("Pay", "Internal Transfer"):
        frappe.db.sql("""update `tabPayment Entry` set clearance_date = %s where name=%s """, (doc.cheque_action_date, doc.name))
        frappe.db.sql(""" update `tabPayment Entry` set cheque_status_pay = "مدفوع" where name = %s""", doc.name)
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action = "" where name = %s""", doc.name)
        accounts = [
            _je_account(doc.payable_account, paid_amount_company, True, doc, company_currency),
            _je_account(doc.account, paid_amount_company, False, doc, company_currency),
        ]
        new_doc = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Bank Entry",
            "reference_doctype": "Payment Entry",
            "reference_link": doc.name,
            "cheque_no": doc.reference_no,
            "cheque_date": doc.reference_date,
            "pe_status": "مدفوع",
            "posting_date": doc.cheque_action_date,
            "multi_currency": 1 if _needs_multi_currency([doc.payable_account, doc.account], company_currency) else 0,
            "accounts": accounts,
            "payment_type": doc.payment_type,
            "user_remark": doc.party_name
        })
        new_doc.insert()
        new_doc.submit()
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action_date = NULL where name = %s""", doc.name)
        doc.reload()

    if doc.cheque_action == "سحب الشيك":
        frappe.db.sql(""" update `tabPayment Entry` set cheque_status_pay = "مسحوب" where name = %s""", doc.name)
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action = "" where name = %s""", doc.name)
        accounts = [
            _je_account(doc.payable_account, paid_amount_company, True, doc, company_currency),
            _je_account(doc.paid_to, paid_amount_company, False, doc, company_currency,
                        party_type=doc.party_type, party=doc.party,
                        amount_in_account_currency=flt(doc.received_amount)),
        ]
        new_doc = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Bank Entry",
            "reference_doctype": "Payment Entry",
            "reference_link": doc.name,
            "cheque_no": doc.reference_no,
            "cheque_date": doc.reference_date,
            "pe_status": "مسحوب",
            "posting_date": doc.cheque_action_date,
            "multi_currency": 1 if _needs_multi_currency([doc.payable_account, doc.paid_to], company_currency) else 0,
            "accounts": accounts,
            "payment_type": doc.payment_type,
            "user_remark": doc.party_name
        })
        new_doc.insert()
        new_doc.submit()
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action_date = NULL where name = %s""", doc.name)
        doc.reload()
    
    if doc.cheque_action == "سحب شيك من التحصيل":
        frappe.db.sql(""" update `tabPayment Entry` set cheque_status = "حافظة شيكات واردة" where name = %s""", doc.name)
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action = "" where name = %s""", doc.name)
        
        accounts = [
            _je_account(doc.paid_to, paid_amount_company, True, doc, company_currency,
                        amount_in_account_currency=flt(doc.received_amount)),
            _je_account(doc.collection_fee_account, paid_amount_company, False, doc, company_currency),
        ]
        
        new_doc = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Bank Entry",
            "reference_doctype": "Payment Entry",
            "reference_link": doc.name,
            "cheque_no": doc.reference_no,
            "cheque_date": doc.reference_date,
            "pe_status": "سحب من التحصيل",
            "posting_date": doc.cheque_action_date or today(),
            "multi_currency": 1 if _needs_multi_currency([doc.paid_to, doc.collection_fee_account], company_currency) else 0,
            "accounts": accounts,
            "payment_type": doc.payment_type,
            "user_remark": doc.party_name
        })
        
        new_doc.insert()
        new_doc.submit()
        
        frappe.db.sql(""" update `tabPayment Entry` set cheque_action_date = NULL where name = %s""", doc.name)
        doc.reload()