// Copyright (c) 2026, erpcloud.systems and contributors
// For license information, please see license.txt
//
// Advanced Customer Statement – client-side report script.
//
// Defines filters, injects professional CSS, and applies custom row/cell
// formatting (bold summary rows, red/green debit/credit indicators).

/* global frappe */

frappe.query_reports["Advanced Customer Statement"] = {
    filters: [
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
            reqd: 1,
            default: frappe.defaults.get_user_default("Company"),
        },
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            reqd: 1,
            default: frappe.datetime.month_start(),
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            reqd: 1,
            default: frappe.datetime.get_today(),
        },
        {
            fieldname: "customers",
            label: __("Customer"),
            fieldtype: "MultiSelectList",
            options: "Customer",
            get_data: function (txt) {
                return frappe.db.get_link_options("Customer", txt);
            },
        },
        {
            fieldname: "accounts",
            label: __("Account"),
            fieldtype: "MultiSelectList",
            options: "Account",
            get_data: function (txt) {
                var company = frappe.query_report.get_filter_value("company");
                return frappe.db.get_link_options("Account", txt, {
                    company: company,
                    account_type: "Receivable",
                });
            },
        },
        {
            fieldname: "show_in_company_currency",
            label: __("Show in Company Currency"),
            fieldtype: "Check",
            default: 0,
        },
    ],

    onload: function (report) {
        _inject_statement_css();
    },

    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        if (!data) return value;

        var row_type = data.row_type || "";
        var is_summary = row_type === "opening" || row_type === "closing";
        var col = column.fieldname;

        // ── Bold summary rows ──────────────────────────────────────────────
        if (is_summary) {
            value = "<strong>" + value + "</strong>";
        }

        // ── Debit columns → green ──────────────────────────────────────────
        if (
            (col === "debit_in_account_currency" || col === "debit") &&
            data[col] > 0
        ) {
            value = '<span class="ecs-debit">' + value + "</span>";
        }

        // ── Credit columns → red ───────────────────────────────────────────
        if (
            (col === "credit_in_account_currency" || col === "credit") &&
            data[col] > 0
        ) {
            value = '<span class="ecs-credit">' + value + "</span>";
        }

        // ── Balance columns: positive → dark-green, negative → dark-red ───
        if (
            col === "balance_in_account_currency" ||
            col === "balance_in_company_currency"
        ) {
            var bal = flt(data[col]);
            if (bal > 0) {
                value = '<span class="ecs-bal-pos">' + value + "</span>";
            } else if (bal < 0) {
                value = '<span class="ecs-bal-neg">' + value + "</span>";
            }
        }

        return value;
    },
};

// ---------------------------------------------------------------------------
// CSS injection
// ---------------------------------------------------------------------------

function _inject_statement_css() {
    if (document.getElementById("ecs-statement-css")) return;

    var css = [
        /* ── Table header ─────────────────────────────────────────── */
        ".report-wrapper .datatable .dt-header .dt-cell__content {",
        "    background-color: #f0f4f7;",
        "    color: #333;",
        "    font-weight: 600;",
        "    font-size: 12px;",
        "    border-bottom: 2px solid #c8d8e4;",
        "}",

        /* ── General row styling ──────────────────────────────────── */
        ".report-wrapper .datatable .dt-row .dt-cell {",
        "    color: #333;",
        "    font-size: 12px;",
        "    border-bottom: 1px solid #e8eef2;",
        "    padding: 6px 8px;",
        "}",

        /* ── Alternating row background ───────────────────────────── */
        ".report-wrapper .datatable .dt-row:nth-child(even) .dt-cell {",
        "    background-color: #f8fbfd;",
        "}",

        /* ── Opening / Closing row highlight ──────────────────────── */
        ".report-wrapper .datatable .dt-row--opening .dt-cell,",
        ".report-wrapper .datatable .dt-row--closing .dt-cell {",
        "    background-color: #e8f0f7 !important;",
        "    border-top: 1px solid #b0c8dc;",
        "    border-bottom: 1px solid #b0c8dc;",
        "}",

        /* ── Debit / Credit colour classes ────────────────────────── */
        ".ecs-debit  { color: #1a7340; font-weight: 500; }",
        ".ecs-credit { color: #c0392b; font-weight: 500; }",

        /* ── Balance colour classes ───────────────────────────────── */
        ".ecs-bal-pos { color: #1a7340; }",
        ".ecs-bal-neg { color: #c0392b; }",

        /* ── Print optimisations ──────────────────────────────────── */
        "@media print {",
        "    .report-wrapper .datatable .dt-header .dt-cell__content {",
        "        background-color: #d8e6f0 !important;",
        "        -webkit-print-color-adjust: exact;",
        "        print-color-adjust: exact;",
        "    }",
        "    .ecs-debit, .ecs-credit, .ecs-bal-pos, .ecs-bal-neg {",
        "        -webkit-print-color-adjust: exact;",
        "        print-color-adjust: exact;",
        "    }",
        "}",
    ].join("\n");

    var style = document.createElement("style");
    style.id = "ecs-statement-css";
    style.textContent = css;
    document.head.appendChild(style);
}

// Minimal float helper (mirrors frappe.utils.flt in JS contexts).
function flt(val) {
    var v = parseFloat(val);
    return isNaN(v) ? 0 : v;
}
