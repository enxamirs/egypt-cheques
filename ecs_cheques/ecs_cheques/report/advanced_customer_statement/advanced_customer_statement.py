# Copyright (c) 2026, erpcloud.systems and contributors
# For license information, please see license.txt

"""
Advanced Customer Statement – Script Report

Produces a professional, multi-currency customer ledger statement directly
from GL Entry records.  The report supports:

  * Company-mandatory filter with date range
  * Multi-select Account and Customer filters
  * Per-row account-currency running balance
  * Optional "Show in Company Currency" columns (Debit / Credit / Balance in USD)
  * Opening Balance and Closing Balance summary rows
"""

from __future__ import unicode_literals

import frappe
from frappe import _

from ecs_cheques.ecs_cheques.report.party_statement_utils import (
    validate_filters,
    get_columns,
    build_report_data,
)

_PARTY_TYPE = "Customer"


def execute(filters=None):
    if not filters:
        filters = {}
    validate_filters(filters, _PARTY_TYPE)
    columns = get_columns(filters, _PARTY_TYPE)
    data = build_report_data(filters, _PARTY_TYPE)
    return columns, data
