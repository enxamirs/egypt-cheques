// Copyright (c) 2021, erpcloud.systems and contributors
// For license information, please see license.txt
//
// General Ledger "Add Columns in Transaction Currency" – client-side fix.
//
// When the GL report renders its transaction-currency columns (debit, credit,
// balance in account_currency), it uses the column's `options` field to
// determine which currency symbol to display.  If the report definition uses
// a fixed/global currency instead of the per-row `account_currency`, every
// row ends up showing the same (wrong) symbol.
//
// This script is loaded on every query-report page (via `page_js` in
// hooks.py).  It waits until the "General Ledger" report definition is
// available and then patches its `formatter` to use the per-row
// `account_currency` for the transaction-currency columns and the per-row
// `payment_currency` for the new payment-currency columns.

(function () {
    "use strict";

    var REPORT_NAME = "General Ledger";
    var TX_CURRENCY_COLUMNS = [
        "debit_in_account_currency",
        "credit_in_account_currency",
        "balance_in_account_currency"
    ];
    var PAYMENT_CURRENCY_COLUMNS = [
        "debit_in_payment_currency",
        "credit_in_payment_currency"
    ];

    /**
     * Wrap the report's `formatter` so that transaction-currency columns pick
     * up the per-row `account_currency` rather than any fixed options value,
     * and payment-currency columns use the per-row `payment_currency`.
     */
    function patch_gl_formatter(report_def) {
        if (report_def._ecs_gl_patched) return;
        report_def._ecs_gl_patched = true;

        var original_formatter = report_def.formatter;

        report_def.formatter = function (value, row, column, data, default_formatter) {
            var col_id = column && (column.id || column.fieldname);

            // For transaction-currency columns, override options with per-row account_currency.
            if (
                data &&
                data.account_currency &&
                TX_CURRENCY_COLUMNS.indexOf(col_id) !== -1
            ) {
                column = Object.assign({}, column, { options: data.account_currency });
            }

            // For payment-currency columns, override options with per-row payment_currency.
            if (
                data &&
                data.payment_currency &&
                PAYMENT_CURRENCY_COLUMNS.indexOf(col_id) !== -1
            ) {
                column = Object.assign({}, column, { options: data.payment_currency });
            }

            if (original_formatter) {
                return original_formatter(value, row, column, data, default_formatter);
            }
            return default_formatter(value, row, column, data);
        };
    }

    /**
     * Try to patch the GL report definition.  Returns true if the report
     * definition was found (and patched), false otherwise.
     */
    function try_patch() {
        if (
            frappe.query_reports &&
            frappe.query_reports[REPORT_NAME]
        ) {
            patch_gl_formatter(frappe.query_reports[REPORT_NAME]);
            return true;
        }
        return false;
    }

    // The report JS is loaded lazily when the user opens the GL report page.
    // We listen for route changes and retry patching each time the query-report
    // page becomes active.
    $(document).on("page:show", function () {
        // Small delay to allow the report JS to finish registering itself.
        setTimeout(try_patch, 50);
    });

    // Also try immediately in case the script loads after the report JS.
    try_patch();
}());
