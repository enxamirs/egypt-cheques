// Copyright (c) 2026, erpcloud.systems and contributors
// For license information, please see license.txt
//
// Smart General Ledger Pro – Client-side report definition.
//
// Provides:
//   * Filter definitions with dynamic party link
//   * Color coding by currency layer (JOD=Blue, ILS=Green, USD=Orange)
//   * Bold debit rows, normal credit rows
//   * Audit mode toggle
//   * Export buttons (Excel, CSV)

frappe.query_reports["Smart General Ledger Pro"] = {
    filters: [
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
            default: frappe.defaults.get_user_default("Company"),
            reqd: 1
        },
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
            reqd: 1
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
            reqd: 1
        },
        {
            fieldname: "account",
            label: __("Account"),
            fieldtype: "Link",
            options: "Account",
            get_query: function () {
                return {
                    filters: { company: frappe.query_report.get_filter_value("company") }
                };
            }
        },
        {
            fieldname: "party_type",
            label: __("Party Type"),
            fieldtype: "Select",
            options: "\nCustomer\nSupplier\nEmployee",
            on_change: function () {
                frappe.query_report.set_filter_value("party", "");
            }
        },
        {
            fieldname: "party",
            label: __("Party"),
            fieldtype: "Dynamic Link",
            options: "party_type"
        },
        {
            fieldname: "voucher_type",
            label: __("Voucher Type"),
            fieldtype: "Select",
            options: [
                "",
                "Payment Entry",
                "Journal Entry",
                "Sales Invoice",
                "Purchase Invoice",
                "Expense Claim",
                "Asset",
                "Stock Entry"
            ].join("\n")
        },
        {
            fieldname: "voucher_no",
            label: __("Voucher No"),
            fieldtype: "Data"
        },
        {
            fieldname: "currency",
            label: __("Currency"),
            fieldtype: "Link",
            options: "Currency"
        },
        {
            fieldname: "cost_center",
            label: __("Cost Center"),
            fieldtype: "Link",
            options: "Cost Center",
            get_query: function () {
                return {
                    filters: { company: frappe.query_report.get_filter_value("company") }
                };
            }
        },
        {
            fieldname: "project",
            label: __("Project"),
            fieldtype: "Link",
            options: "Project"
        },
        {
            fieldname: "audit_mode",
            label: __("Enable Audit Mode"),
            fieldtype: "Check",
            default: 0
        }
    ],

    // ── Formatter: color coding + bold debit ─────────────────────────────
    formatter: function (value, row, column, data, default_formatter) {
        if (!data) {
            return default_formatter(value, row, column, data);
        }

        var col_id = column && (column.id || column.fieldname);

        // Currency options: use per-row currency metadata
        if (col_id === "debit_jod" || col_id === "credit_jod") {
            column = Object.assign({}, column, { options: data.payment_currency || "JOD" });
        } else if (col_id === "debit_ils" || col_id === "credit_ils") {
            column = Object.assign({}, column, { options: data.party_currency || "ILS" });
        } else if (col_id === "debit_usd" || col_id === "credit_usd") {
            column = Object.assign({}, column, { options: data.company_currency || "USD" });
        }

        var result = default_formatter(value, row, column, data);

        // Color coding by column group
        var jod_cols = ["debit_jod", "credit_jod"];
        var ils_cols = ["debit_ils", "credit_ils"];
        var usd_cols = ["debit_usd", "credit_usd"];
        var warn_cols = ["rate_mismatch_flag", "amount_mismatch_flag"];

        // Warning columns → red text
        if (warn_cols.indexOf(col_id) !== -1 && value) {
            return `<span style="color:#dc3545;font-weight:bold;">${result}</span>`;
        }

        if (!value || value === 0) {
            return result;
        }

        // JOD columns → blue
        if (jod_cols.indexOf(col_id) !== -1) {
            var style = "color:#0066cc;";
            if (col_id.indexOf("debit") !== -1) style += "font-weight:bold;";
            return `<span style="${style}">${result}</span>`;
        }

        // ILS columns → green
        if (ils_cols.indexOf(col_id) !== -1) {
            var style = "color:#28a745;";
            if (col_id.indexOf("debit") !== -1) style += "font-weight:bold;";
            return `<span style="${style}">${result}</span>`;
        }

        // USD columns → orange
        if (usd_cols.indexOf(col_id) !== -1) {
            var style = "color:#fd7e14;";
            if (col_id.indexOf("debit") !== -1) style += "font-weight:bold;";
            return `<span style="${style}">${result}</span>`;
        }

        return result;
    },

    // ── Custom buttons: Export Excel / CSV ───────────────────────────────
    onload: function (report) {
        report.page.add_inner_button(__("Export Excel"), function () {
            frappe.query_report.export_report("xlsx");
        });

        report.page.add_inner_button(__("Export CSV"), function () {
            frappe.query_report.export_report("csv");
        });
    }
};
