// Copyright (c) 2021, erpcloud.systems and contributors
// For license information, please see license.txt

// Utility: parse float safely
function flt(val) { return parseFloat(val) || 0; }

// Cache for company currency to avoid repeated server calls
var _company_currency_cache = {};

// Get company default currency (async, cached)
function get_company_currency(company, callback) {
    if (!company) {
        callback(frappe.boot && frappe.boot.sysdefaults && frappe.boot.sysdefaults.currency || null);
        return;
    }
    if (_company_currency_cache[company]) {
        callback(_company_currency_cache[company]);
        return;
    }
    frappe.call({
        method: "frappe.client.get_value",
        args: {
            doctype: "Company",
            fieldname: "default_currency",
            filters: { name: company }
        },
        callback: function(r) {
            if (r.message && r.message.default_currency) {
                _company_currency_cache[company] = r.message.default_currency;
                callback(r.message.default_currency);
            } else {
                callback(frappe.boot && frappe.boot.sysdefaults && frappe.boot.sysdefaults.currency || null);
            }
        }
    });
}

// Helper function to get exchange rate
function get_exchange_rate(from_currency, to_currency, date) {
    return new Promise((resolve) => {
        if (from_currency === to_currency) {
            resolve(1);
            return;
        }
        
        frappe.call({
            method: "frappe.client.get_list",
            args: {
                doctype: "Currency Exchange",
                filters: {
                    from_currency: from_currency,
                    to_currency: to_currency,
                    date: ["<=", date]
                },
                fields: ["exchange_rate"],
                order_by: "date desc",
                limit_page_length: 1
            },
            callback: function(r) {
                if (r.message && r.message.length > 0) {
                    resolve(r.message[0].exchange_rate);
                } else {
                    // Try reverse
                    frappe.call({
                        method: "frappe.client.get_list",
                        args: {
                            doctype: "Currency Exchange",
                            filters: {
                                from_currency: to_currency,
                                to_currency: from_currency,
                                date: ["<=", date]
                            },
                            fields: ["exchange_rate"],
                            order_by: "date desc",
                            limit_page_length: 1
                        },
                        callback: function(r2) {
                            if (r2.message && r2.message.length > 0) {
                                resolve(1 / r2.message[0].exchange_rate);
                            } else {
                                resolve(null);
                            }
                        }
                    });
                }
            }
        });
    });
}
frappe.ui.form.on("Multiple Cheque Entry", {
    setup: function(frm) {
        // Bank Account Query
        frm.set_query("bank_acc", function() {
            return {
                filters: [
                    ["Bank Account", "bank", "in", frm.doc.cheque_bank]
                ]
            };
        });
        
        // Cheque Bank Query
        frm.set_query("cheque_bank", function() {
            return {
                filters: [
                    ["Bank", "company_bank", "=", '1']
                ]
            };
        });
        
        // Mode of Payment Query
        frm.set_query("mode_of_payment", function() {
            return {
                filters: [
                    ["Mode of Payment", "type", "=", 'Cheque']
                ]
            };
        });
        
        // Party Type Query
        frm.set_query("party_type", function() {
            return {
                filters: [
                    ["DocType", "name", "in", ["Customer", "Supplier"]]
                ]
            };
        });
        
        // Child Table Field Queries
        if (frm.fields_dict.cheque_table) {
            // Account Paid To Query
            frm.fields_dict.cheque_table.grid.get_field('account_paid_to').get_query = function() {
                return {
                    filters: [["Account", "account_type", "in", ["Bank", "Cash"]]]
                };
            };
            
            // Account Currency Query
            frm.fields_dict.cheque_table.grid.get_field('account_currency').get_query = function() {
                return {
                    filters: [["Currency", "enabled", "=", 1]]
                };
            };
            
            // Account Paid From Query
            frm.fields_dict.cheque_table.grid.get_field('account_paid_from').get_query = function() {
                return {
                    filters: [["Account", "account_type", "in", ["Receivable", "Payable"]]]
                };
            };
            
            // Account Currency From Query
            frm.fields_dict.cheque_table.grid.get_field('account_currency_from').get_query = function() {
                return {
                    filters: [["Currency", "enabled", "=", 1]]
                };
            };
            
            // Party Type Query for Child Table
            frm.fields_dict.cheque_table.grid.get_field('party_type').get_query = function() {
                return {
                    filters: [["DocType", "name", "in", ["Customer", "Supplier"]]]
                };
            };
            
            // Mode of Payment Query for Child Table
            frm.fields_dict.cheque_table.grid.get_field('mode_of_payment').get_query = function() {
                return {
                    filters: [["Mode of Payment", "type", "=", 'Cheque']]
                };
            };
            
            // Set Party as Dynamic Link to Party Type
            frm.fields_dict.cheque_table.grid.update_docfield_property('party', 'options', 'party_type');
            frm.fields_dict.cheque_table.grid.update_docfield_property('party', 'fieldname', 'party_type');
            
            // Set paid_amount currency to account_currency
            frm.fields_dict.cheque_table.grid.update_docfield_property('paid_amount', 'options', 'currency');
            frm.fields_dict.cheque_table.grid.update_docfield_property('paid_amount', 'currency', 'account_currency');
            // Set amount_in_company_currency to display in account_currency_from (party account currency)
            frm.fields_dict.cheque_table.grid.update_docfield_property('amount_in_company_currency', 'options', 'account_currency_from');
        }
        
        if (frm.fields_dict.cheque_table_2) {
            // Account Paid To Query
            frm.fields_dict.cheque_table_2.grid.get_field('account_paid_to').get_query = function() {
                return {
                    filters: [["Account", "account_type", "in", ["Payable"]]]
                };
            };
            
            // Account Currency Query
            frm.fields_dict.cheque_table_2.grid.get_field('account_currency').get_query = function() {
                return {
                    filters: [["Currency", "enabled", "=", 1]]
                };
            };
            
            // Account Paid From Query
            frm.fields_dict.cheque_table_2.grid.get_field('account_paid_from').get_query = function() {
                return {
                    filters: [["Account", "account_type", "in", ["Bank", "Cash"]]]
                };
            };
            
            // Account Currency From Query
            frm.fields_dict.cheque_table_2.grid.get_field('account_currency_from').get_query = function() {
                return {
                    filters: [["Currency", "enabled", "=", 1]]
                };
            };
            
            // Party Type Query for Child Table
            frm.fields_dict.cheque_table_2.grid.get_field('party_type').get_query = function() {
                return {
                    filters: [["DocType", "name", "in", ["Customer", "Supplier"]]]
                };
            };
            
            // Mode of Payment Query for Child Table
            frm.fields_dict.cheque_table_2.grid.get_field('mode_of_payment').get_query = function() {
                return {
                    filters: [["Mode of Payment", "type", "=", 'Cheque']]
                };
            };
            
            // Set Party as Dynamic Link to Party Type
            frm.fields_dict.cheque_table_2.grid.update_docfield_property('party', 'options', 'party_type');
            frm.fields_dict.cheque_table_2.grid.update_docfield_property('party', 'fieldname', 'party_type');
            
            // Set paid_amount currency to account_currency
            frm.fields_dict.cheque_table_2.grid.update_docfield_property('paid_amount', 'options', 'currency');
            frm.fields_dict.cheque_table_2.grid.update_docfield_property('paid_amount', 'currency', 'account_currency_from');
            // Set amount_in_company_currency to display in account_currency (party account currency)
            frm.fields_dict.cheque_table_2.grid.update_docfield_property('amount_in_company_currency', 'options', 'account_currency');
        }
    },
    
    refresh: function(frm) {
        // Copy party name to issuer_name in child tables
        if (frm.doc.party && frm.doc.party_name) {
            // Update Cheque Table Receive
            if (frm.fields_dict.cheque_table) {
                frm.doc.cheque_table.forEach(row => {
                    if (!row.issuer_name) {
                        row.issuer_name = frm.doc.party_name;
                    }
                });
                frm.refresh_field('cheque_table');
            }
            
            // Update Cheque Table Pay
            if (frm.fields_dict.cheque_table_2) {
                frm.doc.cheque_table_2.forEach(row => {
                    if (!row.issuer_name) {
                        row.issuer_name = frm.doc.party_name;
                    }
                });
                frm.refresh_field('cheque_table_2');
            }
        }
        
        // Add Excel buttons after render
        frappe.after_ajax(function() {
            add_excel_buttons(frm);
        });
    }
});
// Field Change Handlers
frappe.ui.form.on("Multiple Cheque Entry", "party_type", function(frm) {
    cur_frm.set_value("party", "");
    cur_frm.set_value("party_name", "");
});
frappe.ui.form.on("Multiple Cheque Entry", "cheque_bank", function(frm) {
    cur_frm.set_value("bank_acc", "");
    cur_frm.set_value("account", "");
    cur_frm.set_value("collection_fee_account", "");
    cur_frm.set_value("payable_account", "");
    
    // Clear child table bank accounts
    if (frm.fields_dict.cheque_table) {
        frm.doc.cheque_table.forEach(row => {
            row.account_paid_to = "";
            row.account_currency = "";
            row.target_exchange_rate = 1;
        });
        frm.refresh_field('cheque_table');
    }
    
    if (frm.fields_dict.cheque_table_2) {
        frm.doc.cheque_table_2.forEach(row => {
            row.account_paid_from = "";
            row.account_currency_from = "";
            row.target_exchange_rate = 1;
        });
        frm.refresh_field('cheque_table_2');
    }
});
frappe.ui.form.on("Multiple Cheque Entry", "bank_acc", function(frm) {
    cur_frm.set_value("account", "");
    cur_frm.set_value("collection_fee_account", "");
    cur_frm.set_value("payable_account", "");
    
    // Update child table bank accounts
    if (frm.fields_dict.cheque_table) {
        frm.doc.cheque_table.forEach(row => {
            row.account_paid_to = frm.doc.bank_acc;
            // Get account currency
            frappe.call({
                method: "frappe.client.get_value",
                args: {
                    doctype: "Account",
                    fieldname: "account_currency",
                    filters: { name: frm.doc.bank_acc }
                },
                callback: function(r) {
                    if (r.message) {
                        row.account_currency = r.message.account_currency;
                        // Update target exchange rate
                        update_target_exchange_rate(frm, row, 'cheque_table');
                        frm.refresh_field('cheque_table');
                    }
                }
            });
        });
        frm.refresh_field('cheque_table');
    }
    
    if (frm.fields_dict.cheque_table_2) {
        frm.doc.cheque_table_2.forEach(row => {
            row.account_paid_from = frm.doc.bank_acc;
            // Get account currency
            frappe.call({
                method: "frappe.client.get_value",
                args: {
                    doctype: "Account",
                    fieldname: "account_currency",
                    filters: { name: frm.doc.bank_acc }
                },
                callback: function(r) {
                    if (r.message) {
                        row.account_currency_from = r.message.account_currency;
                        // Update target exchange rate
                        update_target_exchange_rate(frm, row, 'cheque_table_2');
                        frm.refresh_field('cheque_table_2');
                    }
                }
            });
        });
        frm.refresh_field('cheque_table_2');
    }
});
frappe.ui.form.on("Multiple Cheque Entry", "mode_of_payment", function(frm) {
    if (!frm.doc.mode_of_payment) return;
    
    // Get mode of payment details
    frappe.call({
        method: "frappe.client.get",
        args: {
            doctype: "Mode of Payment",
            name: frm.doc.mode_of_payment
        },
        callback: function(r) {
            if (r.message && r.message.accounts && r.message.accounts.length > 0) {
                const default_account = r.message.accounts[0].default_account;
                
                // Update child tables with default account
                if (frm.fields_dict.cheque_table) {
                    frm.doc.cheque_table.forEach(row => {
                        row.account_paid_to = default_account;
                        // Get account currency
                        frappe.call({
                            method: "frappe.client.get_value",
                            args: {
                                doctype: "Account",
                                fieldname: "account_currency",
                                filters: { name: default_account }
                            },
                            callback: function(r2) {
                                if (r2.message) {
                                    row.account_currency = r2.message.account_currency;
                                    // Update target exchange rate
                                    update_target_exchange_rate(frm, row, 'cheque_table');
                                    frm.refresh_field('cheque_table');
                                }
                            }
                        });
                    });
                    frm.refresh_field('cheque_table');
                }
                
                if (frm.fields_dict.cheque_table_2) {
                    frm.doc.cheque_table_2.forEach(row => {
                        row.account_paid_from = default_account;
                        // Get account currency
                        frappe.call({
                            method: "frappe.client.get_value",
                            args: {
                                doctype: "Account",
                                fieldname: "account_currency",
                                filters: { name: default_account }
                            },
                            callback: function(r2) {
                                if (r2.message) {
                                    row.account_currency_from = r2.message.account_currency;
                                    // Update target exchange rate
                                    update_target_exchange_rate(frm, row, 'cheque_table_2');
                                    frm.refresh_field('cheque_table_2');
                                }
                            }
                        });
                    });
                    frm.refresh_field('cheque_table_2');
                }
            }
        }
    });
});
frappe.ui.form.on('Multiple Cheque Entry', 'payment_type', function(frm) {
    // Set parent party_type
    if (frm.doc.payment_type == "Receive") {
        frm.set_value("party_type", "Customer");
    }
    if (frm.doc.payment_type == "Pay") {
        frm.set_value("party_type", "Supplier");
    }
    
    // Set child table party_type based on payment_type
    const party_type_value = frm.doc.payment_type === 'Receive' ? 'Customer' : 'Supplier';
    
    // Update Cheque Table Receive
    if (frm.fields_dict.cheque_table) {
        frm.doc.cheque_table.forEach(row => {
            row.party_type = party_type_value;
            row.party = '';
            row.party_name = '';
            row.account_paid_from = '';
            row.account_currency_from = '';
            row.target_exchange_rate = 1;
        });
        frm.refresh_field('cheque_table');
    }
    
    // Update Cheque Table Pay
    if (frm.fields_dict.cheque_table_2) {
        frm.doc.cheque_table_2.forEach(row => {
            row.party_type = party_type_value;
            row.party = '';
            row.party_name = '';
            row.account_paid_to = '';
            row.account_currency = '';
            row.target_exchange_rate = 1;
        });
        frm.refresh_field('cheque_table_2');
    }
});
frappe.ui.form.on('Multiple Cheque Entry', 'party', function(frm) {
    if (cur_frm.doc.party_type == "Customer") {
        frappe.call({
            method: "frappe.client.get_value",
            args: {
                doctype: "Customer",
                fieldname: "customer_name",
                filters: { 'name': cur_frm.doc.party }
            },
            callback: function(r) {
                cur_frm.set_value("party_name", r.message.customer_name);
            }
        });
    }
    if (cur_frm.doc.party_type == "Supplier") {
        frappe.call({
            method: "frappe.client.get_value",
            args: {
                doctype: "Supplier",
                fieldname: "supplier_name",
                filters: { 'name': cur_frm.doc.party }
            },
            callback: function(r) {
                cur_frm.set_value("party_name", r.message.supplier_name);
            }
        });
    }
    
    // Update issuer_name in child tables
    if (frm.doc.party_name) {
        // Update Cheque Table Receive
        if (frm.fields_dict.cheque_table) {
            frm.doc.cheque_table.forEach(row => {
                row.issuer_name = frm.doc.party_name;
            });
            frm.refresh_field('cheque_table');
        }
        
        // Update Cheque Table Pay
        if (frm.fields_dict.cheque_table_2) {
            frm.doc.cheque_table_2.forEach(row => {
                row.issuer_name = frm.doc.party_name;
            });
            frm.refresh_field('cheque_table_2');
        }
    }
});
frappe.ui.form.on('Multiple Cheque Entry', 'party_type', function(frm) {
    if (cur_frm.doc.payment_type == "Receive" && cur_frm.doc.party_type == "Customer") {
        frappe.call({
            method: "frappe.client.get_value",
            args: {
                doctype: "Company",
                fieldname: "default_receivable_account",
                filters: { 'name': cur_frm.doc.company }
            },
            callback: function(r) {
                cur_frm.set_value("paid_from", r.message.default_receivable_account);
            }
        });
        frappe.call({
            method: "frappe.client.get_value",
            args: {
                doctype: "Company",
                fieldname: "default_incoming_cheque_wallet_account",
                filters: { 'name': cur_frm.doc.company }
            },
            callback: function(r) {
                cur_frm.set_value("paid_to", r.message.default_incoming_cheque_wallet_account);
            }
        });
    }
    if (cur_frm.doc.payment_type == "Receive" && cur_frm.doc.party_type == "Supplier") {
        frappe.call({
            method: "frappe.client.get_value",
            args: {
                doctype: "Company",
                fieldname: "default_payable_account",
                filters: { 'name': cur_frm.doc.company }
            },
            callback: function(r) {
                cur_frm.set_value("paid_from", r.message.default_payable_account);
            }
        });
        frappe.call({
            method: "frappe.client.get_value",
            args: {
                doctype: "Company",
                fieldname: "default_incoming_cheque_wallet_account",
                filters: { 'name': cur_frm.doc.company }
            },
            callback: function(r) {
                cur_frm.set_value("paid_to", r.message.default_incoming_cheque_wallet_account);
            }
        });
    }
    if (cur_frm.doc.payment_type == "Pay" && cur_frm.doc.party_type == "Customer") {
        frappe.call({
            method: "frappe.client.get_value",
            args: {
                doctype: "Company",
                fieldname: "default_receivable_account",
                filters: { 'name': cur_frm.doc.company }
            },
            callback: function(r) {
                cur_frm.set_value("paid_to", r.message.default_receivable_account);
            }
        });
    }
    if (cur_frm.doc.payment_type == "Pay" && cur_frm.doc.party_type == "Supplier") {
        frappe.call({
            method: "frappe.client.get_value",
            args: {
                doctype: "Company",
                fieldname: "default_payable_account",
                filters: { 'name': cur_frm.doc.company }
            },
            callback: function(r) {
                cur_frm.set_value("paid_to", r.message.default_payable_account);
            }
        });
    }
});
// Validation
frappe.ui.form.on("Multiple Cheque Entry", "validate", function(frm) {
  // قم بتعطيل هذه الأسطر بوضع // قبلها
// if (frm.doc.mode_of_payment_type != "Cheque") {
//     frappe.throw("The Type Of The Selected Mode Of Payment Is Not Cheque...");
// }
    
    // Validate child table data
    const isPay = frm.doc.payment_type === "Pay";
    const isReceive = frm.doc.payment_type === "Receive";
    const table = isPay ? frm.doc.cheque_table_2 : frm.doc.cheque_table;
    
    if (!table || !table.length) {
        frappe.throw("Please add cheque entries before submitting.");
    }
    
    table.forEach(row => {
        if (!row.party_type) {
            frappe.throw(`Party Type is required in row ${row.idx}`);
        }
        if (!row.party) {
            frappe.throw(`Party is required in row ${row.idx}`);
        }
        if (!row.account_paid_from) {
            frappe.throw(`Account Paid From is required in row ${row.idx}`);
        }
        if (!row.account_currency_from) {
            frappe.throw(`Account Currency (From) is required in row ${row.idx}`);
        }
        if (!row.account_paid_to) {
            frappe.throw(`Account Paid To is required in row ${row.idx}`);
        }
        if (!row.account_currency) {
            frappe.throw(`Account Currency (To) is required in row ${row.idx}`);
        }
        if (!row.paid_amount || row.paid_amount <= 0) {
            frappe.throw(`Paid Amount is required and must be greater than zero in row ${row.idx}`);
        }
        if (row.account_currency !== row.account_currency_from && (!row.target_exchange_rate || row.target_exchange_rate <= 0)) {
            frappe.throw(`Exchange Rate is required and must be greater than zero in row ${row.idx} (currencies differ: ${row.account_currency} ≠ ${row.account_currency_from}). Please create a Currency Exchange record.`);
        }
    });
});
// Helper function to update target exchange rate (only if not manually overridden)
function update_target_exchange_rate(frm, row, table_name, force) {
    if (!row.account_currency_from || !row.account_currency) {
        return;
    }

    // When both accounts share the same currency, always reset exchange rate to 1
    // and set amount_in_company_currency = paid_amount, regardless of any manual
    // override — there is no conversion to apply.
    if (row.account_currency_from === row.account_currency) {
        frappe.model.set_value(row.doctype, row.name, "target_exchange_rate", 1);
        update_amount_in_company_currency(frm, row, table_name);
        update_amount_in_usd(frm, row, table_name);
        return;
    }

    // Skip auto-fetch if user has manually set the rate (unless forced)
    if (!force && row._rate_manually_set) {
        update_amount_in_company_currency(frm, row, table_name);
        update_amount_in_usd(frm, row, table_name);
        return;
    }

    const posting_date = frm.doc.posting_date || frappe.datetime.nowdate();

    if (table_name === 'cheque_table') {
        // For Receive: get exchange rate from cheque currency (account_currency) to party account currency (account_currency_from)
        get_exchange_rate(row.account_currency, row.account_currency_from, posting_date)
            .then(rate => {
                if (!rate) {
                    frappe.msgprint({
                        title: __('Exchange Rate Not Found'),
                        indicator: 'red',
                        message: __('No Currency Exchange record found for {0} → {1} on or before {2}. Please create one before proceeding.',
                            [row.account_currency, row.account_currency_from, posting_date])
                    });
                    frappe.model.set_value(row.doctype, row.name, "target_exchange_rate", 0);
                    frm.refresh_field(table_name);
                    return;
                }
                frappe.model.set_value(row.doctype, row.name, "target_exchange_rate", rate);
                // Update cheque_currency to match account_currency (bank/to account)
                if (!row.cheque_currency) {
                    frappe.model.set_value(row.doctype, row.name, "cheque_currency", row.account_currency);
                }
                // Sync bidirectional rate fields only when account currencies differ.
                // When both accounts share the same currency, exchange_rate_party_to_mop
                // is meaningless and must not be stored (it would cause a mismatch error
                // later when validating the Payment Entry).
                if (rate > 0 && row.account_currency && row.account_currency_from &&
                        row.account_currency !== row.account_currency_from) {
                    frappe.model.set_value(row.doctype, row.name, "exchange_rate_mop_to_party", rate);
                    frappe.model.set_value(row.doctype, row.name, "exchange_rate_party_to_mop", flt(1.0 / rate, 9));
                }
                update_amount_in_company_currency(frm, locals[row.doctype][row.name], table_name);
                update_amount_in_usd(frm, locals[row.doctype][row.name], table_name);
                frm.refresh_field(table_name);
            });
    } else {
        // For Pay: get exchange rate from cheque currency (account_currency_from) to party account currency (account_currency)
        get_exchange_rate(row.account_currency_from, row.account_currency, posting_date)
            .then(rate => {
                if (!rate) {
                    frappe.msgprint({
                        title: __('Exchange Rate Not Found'),
                        indicator: 'red',
                        message: __('No Currency Exchange record found for {0} → {1} on or before {2}. Please create one before proceeding.',
                            [row.account_currency_from, row.account_currency, posting_date])
                    });
                    frappe.model.set_value(row.doctype, row.name, "target_exchange_rate", 0);
                    frm.refresh_field(table_name);
                    return;
                }
                frappe.model.set_value(row.doctype, row.name, "target_exchange_rate", rate);
                // Update cheque_currency to match account_currency_from (bank/from account)
                if (!row.cheque_currency) {
                    frappe.model.set_value(row.doctype, row.name, "cheque_currency", row.account_currency_from);
                }
                update_amount_in_company_currency(frm, locals[row.doctype][row.name], table_name);
                update_amount_in_usd(frm, locals[row.doctype][row.name], table_name);
                frm.refresh_field(table_name);
            });
    }
}

// Helper to calculate and set amount_in_company_currency
function update_amount_in_company_currency(frm, row, table_name) {
    const amount = flt(row.paid_amount);
    // When cheque_currency, account_currency_from, and account_currency all match,
    // the amount is already in the correct currency (no conversion needed).
    // If the cheque currency differs from either account currency (e.g. a JOD cheque
    // deposited into a USD account), multiply by target_exchange_rate to get the
    // company-currency equivalent.
    const cheque_currency = row.cheque_currency || row.account_currency;
    const same_currency = cheque_currency && row.account_currency_from && row.account_currency &&
        cheque_currency === row.account_currency_from &&
        cheque_currency === row.account_currency;
    const amt_company_currency = same_currency ? amount : amount * (flt(row.target_exchange_rate) || 1);
    frappe.model.set_value(row.doctype, row.name, "amount_in_company_currency", amt_company_currency);
    frm.refresh_field(table_name);
}

// Helper to calculate and set amount_in_usd (amount in company currency, e.g. USD)
// Uses exchange rate: cheque_currency → company_currency
function update_amount_in_usd(frm, row, table_name) {
    const amount = flt(row.paid_amount);
    const cheque_currency = row.cheque_currency || (table_name === 'cheque_table' ? row.account_currency : row.account_currency_from);
    if (!cheque_currency) {
        frappe.model.set_value(row.doctype, row.name, "amount_in_usd", amount);
        frm.refresh_field(table_name);
        return;
    }
    get_company_currency(frm.doc.company, function(company_currency) {
        if (!company_currency || cheque_currency === company_currency) {
            frappe.model.set_value(row.doctype, row.name, "amount_in_usd", amount);
            frm.refresh_field(table_name);
            return;
        }
        const posting_date = frm.doc.posting_date || frappe.datetime.nowdate();
        get_exchange_rate(cheque_currency, company_currency, posting_date)
            .then(function(rate) {
                const usd_amount = rate ? flt(amount * rate, 9) : amount;
                frappe.model.set_value(row.doctype, row.name, "amount_in_usd", usd_amount);
                frm.refresh_field(table_name);
            });
    });
}

// Add Excel upload/download buttons to the form
function add_excel_buttons(frm) {
    const isPay = frm.doc.payment_type === "Pay";
    const table_field = isPay ? "cheque_table_2" : "cheque_table";
    const payment_type = frm.doc.payment_type;

    if (!frm.fields_dict[table_field]) return;

    // Download Template button
    const download_btn_id = "btn-download-template-" + table_field;
    if (!frm.fields_dict[table_field].wrapper.querySelector("#" + download_btn_id)) {
        const $wrapper = $(frm.fields_dict[table_field].wrapper);
        const $btn_group = $wrapper.find(".grid-heading-row .grid-buttons");

        const $download_btn = $(`<button id="${download_btn_id}" class="btn btn-xs btn-default" style="margin-left:5px;">
            <i class="fa fa-download"></i> ${__("Download Template")}
        </button>`);

        $download_btn.on("click", function() {
            window.location.href = frappe.urllib.get_full_url(
                "/api/method/ecs_cheques.ecs_cheques.doctype.multiple_cheque_entry.multiple_cheque_entry.get_cheques_excel_template"
                + "?payment_type=" + encodeURIComponent(payment_type)
            );
        });

        const $upload_btn = $(`<button id="btn-upload-excel-${table_field}" class="btn btn-xs btn-default" style="margin-left:5px;">
            <i class="fa fa-upload"></i> ${__("Upload Excel")}
        </button>`);

        $upload_btn.on("click", function() {
            const input = document.createElement("input");
            input.type = "file";
            input.accept = ".xlsx";
            input.onchange = function() {
                const file = input.files[0];
                if (!file) return;
                const reader = new FileReader();
                reader.onload = function(e) {
                    const base64 = e.target.result.split(",")[1];
                    frappe.call({
                        method: "ecs_cheques.ecs_cheques.doctype.multiple_cheque_entry.multiple_cheque_entry.upload_cheques_excel",
                        args: { file_data: base64, payment_type: payment_type },
                        callback: function(r) {
                            if (r.message && r.message.length) {
                                r.message.forEach(function(row_data) {
                                    const child_doctype = isPay ? "Cheque Table Pay" : "Cheque Table Receive";
                                    const new_row = frappe.model.add_child(frm.doc, child_doctype, table_field);
                                    Object.keys(row_data).forEach(function(key) {
                                        if (row_data[key] !== null && row_data[key] !== undefined) {
                                            frappe.model.set_value(new_row.doctype, new_row.name, key, row_data[key]);
                                        }
                                    });
                                });
                                frm.refresh_field(table_field);
                                frappe.msgprint(__("تم رفع {0} شيك بنجاح", [r.message.length]));
                            }
                        }
                    });
                };
                reader.readAsDataURL(file);
            };
            input.click();
        });

        if ($btn_group.length) {
            $btn_group.append($download_btn).append($upload_btn);
        }
    }
}
// Unified Submit Handler
frappe.ui.form.on("Multiple Cheque Entry", "on_submit", function(frm) {
    const isPay = frm.doc.payment_type === "Pay";
    const table = isPay ? frm.doc.cheque_table_2 : frm.doc.cheque_table;

    if (!table || !table.length) return;

    const promises = [];
    const created_entries = [];

    table.forEach((row) => {
        if (row.payment_entry) return; // already processed

        // Pass only docname and row_id; the server fetches all data from the DB,
        // eliminating reliance on potentially stale client-side row values.
        promises.push(new Promise((resolve) => {
            frappe.call({
                method: "ecs_cheques.ecs_cheques.doctype.multiple_cheque_entry.multiple_cheque_entry.create_payment_entry_from_cheque",
                args: { docname: frm.doc.name, row_id: row.name },
                callback: function(r) {
                    if (r.message) {
                        created_entries.push(r.message);
                    }
                    resolve();
                },
                error: function() { resolve(); }
            });
        }));
    });

    if (promises.length > 0) {
        Promise.all(promises).then(() => {
            if (created_entries.length > 0) {
                const links = created_entries.map(name =>
                    `<a href="/app/payment-entry/${name}" target="_blank">${name}</a>`
                ).join("<br>");
                frappe.msgprint({
                    title: __("Payment Entries Created"),
                    indicator: "green",
                    message: __(
                        "تم إنشاء {0} قيد دفع من Multiple Cheque Entry:<br>{1}",
                        [created_entries.length, links]
                    )
                });
            }
            frm.reload_doc();
        });
    }
});
// Child Table Auto-fill Handlers
frappe.ui.form.on("Cheque Table Pay", "first_beneficiary", function(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    row.person_name = frm.doc.party_name;
    row.issuer_name = frm.doc.company;
    frm.refresh_field("cheque_table_2");
});
frappe.ui.form.on("Cheque Table Receive", "first_beneficiary", function(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    row.person_name = frm.doc.company;
    row.issuer_name = frm.doc.party_name;
    frm.refresh_field("cheque_table");
});
// Child Table Account Currency Auto-fill (To)
frappe.ui.form.on("Cheque Table Receive", "account_paid_to", function(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    if (row.account_paid_to) {
        frappe.call({
            method: "frappe.client.get_value",
            args: {
                doctype: "Account",
                fieldname: "account_currency",
                filters: { name: row.account_paid_to }
            },
            callback: function(r) {
                if (r.message) {
                    frappe.model.set_value(cdt, cdn, "account_currency", r.message.account_currency);
                    // Set cheque_currency to bank account currency (cheque is denominated in bank currency)
                    frappe.model.set_value(cdt, cdn, "cheque_currency", r.message.account_currency);
                    // Reset manual flag on fresh row reference inside callback
                    locals[cdt][cdn]._rate_manually_set = false;
                    // Update target exchange rate
                    update_target_exchange_rate(frm, locals[cdt][cdn], 'cheque_table');
                    frm.refresh_field("cheque_table");
                }
            }
        });
    }
});
frappe.ui.form.on("Cheque Table Pay", "account_paid_to", function(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    if (row.account_paid_to) {
        frappe.call({
            method: "frappe.client.get_value",
            args: {
                doctype: "Account",
                fieldname: "account_currency",
                filters: { name: row.account_paid_to }
            },
            callback: function(r) {
                if (r.message) {
                    frappe.model.set_value(cdt, cdn, "account_currency", r.message.account_currency);
                    // Update target exchange rate
                    update_target_exchange_rate(frm, locals[cdt][cdn], 'cheque_table_2');
                    frm.refresh_field("cheque_table_2");
                }
            }
        });
    }
});
// Child Table Account Currency Auto-fill (From)
frappe.ui.form.on("Cheque Table Receive", "account_paid_from", function(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    if (row.account_paid_from) {
        frappe.call({
            method: "frappe.client.get_value",
            args: {
                doctype: "Account",
                fieldname: "account_currency",
                filters: { name: row.account_paid_from }
            },
            callback: function(r) {
                if (r.message) {
                    frappe.model.set_value(cdt, cdn, "account_currency_from", r.message.account_currency);
                    // Update target exchange rate
                    update_target_exchange_rate(frm, locals[cdt][cdn], 'cheque_table');
                    frm.refresh_field("cheque_table");
                }
            }
        });
    }
});
frappe.ui.form.on("Cheque Table Pay", "account_paid_from", function(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    if (row.account_paid_from) {
        frappe.call({
            method: "frappe.client.get_value",
            args: {
                doctype: "Account",
                fieldname: "account_currency",
                filters: { name: row.account_paid_from }
            },
            callback: function(r) {
                if (r.message) {
                    frappe.model.set_value(cdt, cdn, "account_currency_from", r.message.account_currency);
                    // For Pay: cheque currency = bank account (from) currency
                    frappe.model.set_value(cdt, cdn, "cheque_currency", r.message.account_currency);
                    // Reset manual flag on fresh row reference inside callback
                    locals[cdt][cdn]._rate_manually_set = false;
                    // Update target exchange rate
                    update_target_exchange_rate(frm, locals[cdt][cdn], 'cheque_table_2');
                    frm.refresh_field("cheque_table_2");
                }
            }
        });
    }
});
// Child Table Party Type Change Handler
frappe.ui.form.on("Cheque Table Receive", "party_type", function(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    frappe.model.set_value(cdt, cdn, "party", "");
    frappe.model.set_value(cdt, cdn, "party_name", "");
    frappe.model.set_value(cdt, cdn, "account_paid_from", "");
    frappe.model.set_value(cdt, cdn, "account_currency_from", "");
    frappe.model.set_value(cdt, cdn, "target_exchange_rate", 1);
    frm.refresh_field("cheque_table");
});
frappe.ui.form.on("Cheque Table Pay", "party_type", function(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    frappe.model.set_value(cdt, cdn, "party", "");
    frappe.model.set_value(cdt, cdn, "party_name", "");
    frappe.model.set_value(cdt, cdn, "account_paid_to", "");
    frappe.model.set_value(cdt, cdn, "account_currency", "");
    frappe.model.set_value(cdt, cdn, "target_exchange_rate", 1);
    frm.refresh_field("cheque_table_2");
});
// Child Table Party Change Handler
frappe.ui.form.on("Cheque Table Receive", "party", function(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    if (row.party && row.party_type) {
        // Get party name
        const fieldname = row.party_type === "Customer" ? "customer_name" : "supplier_name";
        frappe.call({
            method: "frappe.client.get_value",
            args: {
                doctype: row.party_type,
                fieldname: fieldname,
                filters: { name: row.party }
            },
            callback: function(r) {
                if (r.message) {
                    frappe.model.set_value(cdt, cdn, "party_name", r.message[fieldname]);
                    frappe.model.set_value(cdt, cdn, "issuer_name", r.message[fieldname]);
                    frm.refresh_field("cheque_table");
                }
            }
        });
        
        // Use get_party_account to fetch party-specific account (falls back to company default)
        if (frm.doc.company) {
            frappe.call({
                method: "erpnext.accounts.party.get_party_account",
                args: {
                    party_type: row.party_type,
                    party: row.party,
                    company: frm.doc.company
                },
                callback: function(r) {
                    if (r.message) {
                        frappe.model.set_value(cdt, cdn, "account_paid_from", r.message);

                        // Get account currency for the party account
                        frappe.call({
                            method: "frappe.client.get_value",
                            args: {
                                doctype: "Account",
                                fieldname: "account_currency",
                                filters: { name: r.message }
                            },
                            callback: function(r2) {
                                if (r2.message) {
                                    frappe.model.set_value(cdt, cdn, "account_currency_from", r2.message.account_currency);
                                    // Update target exchange rate
                                    update_target_exchange_rate(frm, locals[cdt][cdn], 'cheque_table');
                                    frm.refresh_field("cheque_table");
                                }
                            }
                        });
                    }
                }
            });
        }

        // Set account_paid_to to parent's bank_acc
        if (frm.doc.bank_acc) {
            frappe.model.set_value(cdt, cdn, "account_paid_to", frm.doc.bank_acc);
            
            // Get account currency
            frappe.call({
                method: "frappe.client.get_value",
                args: {
                    doctype: "Account",
                    fieldname: "account_currency",
                    filters: { name: frm.doc.bank_acc }
                },
                callback: function(r) {
                    if (r.message) {
                        frappe.model.set_value(cdt, cdn, "account_currency", r.message.account_currency);
                        frappe.model.set_value(cdt, cdn, "cheque_currency", r.message.account_currency);
                        // Update target exchange rate
                        update_target_exchange_rate(frm, locals[cdt][cdn], 'cheque_table');
                        frm.refresh_field("cheque_table");
                    }
                }
            });
        }
    }
});
frappe.ui.form.on("Cheque Table Pay", "party", function(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    if (row.party && row.party_type) {
        // Get party name
        const fieldname = row.party_type === "Customer" ? "customer_name" : "supplier_name";
        frappe.call({
            method: "frappe.client.get_value",
            args: {
                doctype: row.party_type,
                fieldname: fieldname,
                filters: { name: row.party }
            },
            callback: function(r) {
                if (r.message) {
                    frappe.model.set_value(cdt, cdn, "party_name", r.message[fieldname]);
                    frappe.model.set_value(cdt, cdn, "issuer_name", r.message[fieldname]);
                    frm.refresh_field("cheque_table_2");
                }
            }
        });
        
        // Use get_party_account to fetch party-specific account (falls back to company default)
        if (frm.doc.company) {
            frappe.call({
                method: "erpnext.accounts.party.get_party_account",
                args: {
                    party_type: row.party_type,
                    party: row.party,
                    company: frm.doc.company
                },
                callback: function(r) {
                    if (r.message) {
                        frappe.model.set_value(cdt, cdn, "account_paid_to", r.message);

                        // Get account currency for the party account
                        frappe.call({
                            method: "frappe.client.get_value",
                            args: {
                                doctype: "Account",
                                fieldname: "account_currency",
                                filters: { name: r.message }
                            },
                            callback: function(r2) {
                                if (r2.message) {
                                    frappe.model.set_value(cdt, cdn, "account_currency", r2.message.account_currency);
                                    // Update target exchange rate
                                    update_target_exchange_rate(frm, locals[cdt][cdn], 'cheque_table_2');
                                    frm.refresh_field("cheque_table_2");
                                }
                            }
                        });
                    }
                }
            });
        }
        
        // Set account_paid_from to parent's bank_acc
        if (frm.doc.bank_acc) {
            frappe.model.set_value(cdt, cdn, "account_paid_from", frm.doc.bank_acc);
            
            // Get account currency
            frappe.call({
                method: "frappe.client.get_value",
                args: {
                    doctype: "Account",
                    fieldname: "account_currency",
                    filters: { name: frm.doc.bank_acc }
                },
                callback: function(r) {
                    if (r.message) {
                        frappe.model.set_value(cdt, cdn, "account_currency_from", r.message.account_currency);
                        frappe.model.set_value(cdt, cdn, "cheque_currency", r.message.account_currency);
                        // Update target exchange rate
                        update_target_exchange_rate(frm, locals[cdt][cdn], 'cheque_table_2');
                        frm.refresh_field("cheque_table_2");
                    }
                }
            });
        }
    }
});
// Child Table Mode of Payment Change Handler
frappe.ui.form.on("Cheque Table Receive", "mode_of_payment", function(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    if (row.mode_of_payment) {
        frappe.call({
            method: "frappe.client.get",
            args: {
                doctype: "Mode of Payment",
                name: row.mode_of_payment
            },
            callback: function(r) {
                if (r.message && r.message.accounts && r.message.accounts.length > 0) {
                    const default_account = r.message.accounts[0].default_account;
                    frappe.model.set_value(cdt, cdn, "account_paid_to", default_account);
                    
                    // Get account currency
                    frappe.call({
                        method: "frappe.client.get_value",
                        args: {
                            doctype: "Account",
                            fieldname: "account_currency",
                            filters: { name: default_account }
                        },
                        callback: function(r2) {
                            if (r2.message) {
                                frappe.model.set_value(cdt, cdn, "account_currency", r2.message.account_currency);
                                frappe.model.set_value(cdt, cdn, "cheque_currency", r2.message.account_currency);
                                // Update target exchange rate
                                update_target_exchange_rate(frm, locals[cdt][cdn], 'cheque_table');
                                frm.refresh_field("cheque_table");
                            }
                        }
                    });
                }
            }
        });
    }
});
frappe.ui.form.on("Cheque Table Pay", "mode_of_payment", function(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    if (row.mode_of_payment) {
        frappe.call({
            method: "frappe.client.get",
            args: {
                doctype: "Mode of Payment",
                name: row.mode_of_payment
            },
            callback: function(r) {
                if (r.message && r.message.accounts && r.message.accounts.length > 0) {
                    const default_account = r.message.accounts[0].default_account;
                    frappe.model.set_value(cdt, cdn, "account_paid_from", default_account);
                    
                    // Get account currency
                    frappe.call({
                        method: "frappe.client.get_value",
                        args: {
                            doctype: "Account",
                            fieldname: "account_currency",
                            filters: { name: default_account }
                        },
                        callback: function(r2) {
                            if (r2.message) {
                                frappe.model.set_value(cdt, cdn, "account_currency_from", r2.message.account_currency);
                                frappe.model.set_value(cdt, cdn, "cheque_currency", r2.message.account_currency);
                                // Update target exchange rate
                                update_target_exchange_rate(frm, locals[cdt][cdn], 'cheque_table_2');
                                frm.refresh_field("cheque_table_2");
                            }
                        }
                    });
                }
            }
        });
    }
});
// Child Table Row Add Handler
frappe.ui.form.on("Cheque Table Receive", "cheque_table_add", function(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    // Set default values
    row.party_type = frm.doc.party_type;
    row.issuer_name = frm.doc.party_name;
    row.mode_of_payment = frm.doc.mode_of_payment;
    row.target_exchange_rate = 1;
    frm.refresh_field("cheque_table");
    
    // If mode_of_payment is set, fetch default account
    if (row.mode_of_payment) {
        frappe.call({
            method: "frappe.client.get",
            args: {
                doctype: "Mode of Payment",
                name: row.mode_of_payment
            },
            callback: function(r) {
                if (r.message && r.message.accounts && r.message.accounts.length > 0) {
                    const default_account = r.message.accounts[0].default_account;
                    frappe.model.set_value(cdt, cdn, "account_paid_to", default_account);
                    
                    // Get account currency
                    frappe.call({
                        method: "frappe.client.get_value",
                        args: {
                            doctype: "Account",
                            fieldname: "account_currency",
                            filters: { name: default_account }
                        },
                        callback: function(r2) {
                            if (r2.message) {
                                frappe.model.set_value(cdt, cdn, "account_currency", r2.message.account_currency);
                                frappe.model.set_value(cdt, cdn, "cheque_currency", r2.message.account_currency);
                                // Update target exchange rate
                                update_target_exchange_rate(frm, locals[cdt][cdn], 'cheque_table');
                                frm.refresh_field("cheque_table");
                            }
                        }
                    });
                }
            }
        });
    }
});
frappe.ui.form.on("Cheque Table Pay", "cheque_table_2_add", function(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    // Set default values
    row.party_type = frm.doc.party_type;
    row.issuer_name = frm.doc.party_name;
    row.mode_of_payment = frm.doc.mode_of_payment;
    row.target_exchange_rate = 1;
    frm.refresh_field("cheque_table_2");
    
    // If mode_of_payment is set, fetch default account
    if (row.mode_of_payment) {
        frappe.call({
            method: "frappe.client.get",
            args: {
                doctype: "Mode of Payment",
                name: row.mode_of_payment
            },
            callback: function(r) {
                if (r.message && r.message.accounts && r.message.accounts.length > 0) {
                    const default_account = r.message.accounts[0].default_account;
                    frappe.model.set_value(cdt, cdn, "account_paid_from", default_account);
                    
                    // Get account currency
                    frappe.call({
                        method: "frappe.client.get_value",
                        args: {
                            doctype: "Account",
                            fieldname: "account_currency",
                            filters: { name: default_account }
                        },
                        callback: function(r2) {
                            if (r2.message) {
                                frappe.model.set_value(cdt, cdn, "account_currency_from", r2.message.account_currency);
                                frappe.model.set_value(cdt, cdn, "cheque_currency", r2.message.account_currency);
                                // Update target exchange rate
                                update_target_exchange_rate(frm, locals[cdt][cdn], 'cheque_table_2');
                                frm.refresh_field("cheque_table_2");
                            }
                        }
                    });
                }
            }
        });
    }
});

// --- paid_amount change: recalculate amount_in_company_currency and amount_in_usd ---
frappe.ui.form.on("Cheque Table Receive", "paid_amount", function(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    update_amount_in_company_currency(frm, row, 'cheque_table');
    update_amount_in_usd(frm, row, 'cheque_table');
});
frappe.ui.form.on("Cheque Table Pay", "paid_amount", function(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    update_amount_in_company_currency(frm, row, 'cheque_table_2');
    update_amount_in_usd(frm, row, 'cheque_table_2');
});

// --- target_exchange_rate change: mark as manually set and recalculate ---
frappe.ui.form.on("Cheque Table Receive", "target_exchange_rate", function(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    row._rate_manually_set = true;
    update_amount_in_company_currency(frm, row, 'cheque_table');
    update_amount_in_usd(frm, row, 'cheque_table');
    // Keep the bidirectional rate fields in sync with target_exchange_rate
    _sync_mop_party_rates_from_target(frm, cdt, cdn, row);
});

// --- bidirectional exchange rate helpers (MOP↔Party) ---
// Sync exchange_rate_mop_to_party and exchange_rate_party_to_mop from target_exchange_rate
function _sync_mop_party_rates_from_target(frm, cdt, cdn, row) {
    const rate = flt(row.target_exchange_rate);
    if (rate <= 0 || frm._setting_exchange_rate) return;

    // Don't set bidirectional rates when both accounts share the same currency
    // as it would store a meaningless 1.0 that confuses later validation.
    if (row.account_currency_from && row.account_currency &&
        row.account_currency_from === row.account_currency) {
        return;
    }

    frm._setting_exchange_rate = true;
    frappe.model.set_value(cdt, cdn, "exchange_rate_mop_to_party", rate);
    frappe.model.set_value(cdt, cdn, "exchange_rate_party_to_mop", flt(1.0 / rate, 9));
    frm._setting_exchange_rate = false;
}

frappe.ui.form.on("Cheque Table Receive", "exchange_rate_mop_to_party", function(frm, cdt, cdn) {
    if (frm._setting_exchange_rate) return;
    const row = locals[cdt][cdn];
    const val = flt(row.exchange_rate_mop_to_party);
    if (val <= 0) return;
    const reciprocal = flt(1.0 / val, 9);
    // Only update if the value has actually changed to prevent circular updates.
    if (Math.abs(flt(row.exchange_rate_party_to_mop) - reciprocal) < 1e-9) return;
    frm._setting_exchange_rate = true;
    frappe.model.set_value(cdt, cdn, "exchange_rate_party_to_mop", reciprocal);
    frm._setting_exchange_rate = false;
    update_amount_in_company_currency(frm, row, 'cheque_table');
});

frappe.ui.form.on("Cheque Table Receive", "exchange_rate_party_to_mop", function(frm, cdt, cdn) {
    if (frm._setting_exchange_rate) return;
    const row = locals[cdt][cdn];
    const val = flt(row.exchange_rate_party_to_mop);
    if (val <= 0) return;
    const reciprocal = flt(1.0 / val, 9);
    // Only update if the value has actually changed to prevent circular updates.
    if (Math.abs(flt(row.exchange_rate_mop_to_party) - reciprocal) < 1e-9) return;
    frm._setting_exchange_rate = true;
    frappe.model.set_value(cdt, cdn, "exchange_rate_mop_to_party", reciprocal);
    frm._setting_exchange_rate = false;
});
frappe.ui.form.on("Cheque Table Pay", "target_exchange_rate", function(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    row._rate_manually_set = true;
    update_amount_in_company_currency(frm, row, 'cheque_table_2');
    update_amount_in_usd(frm, row, 'cheque_table_2');
});

// --- Hide exchange rate fields in child row form when currencies are the same ---
function _toggle_exchange_rate_fields(frm, cdt, cdn, table_fieldname) {
    const row = locals[cdt][cdn];
    if (!row) return;
    const same = row.account_currency_from && row.account_currency &&
        row.account_currency_from === row.account_currency;
    const grid_row = frm.fields_dict[table_fieldname] &&
        frm.fields_dict[table_fieldname].grid &&
        frm.fields_dict[table_fieldname].grid.grid_rows_by_docname &&
        frm.fields_dict[table_fieldname].grid.grid_rows_by_docname[cdn];
    if (!grid_row || !grid_row.grid_form) return;
    const gf = grid_row.grid_form;
    ["target_exchange_rate", "exchange_rate_mop_to_party", "exchange_rate_party_to_mop"].forEach(fn => {
        if (gf.fields_dict[fn]) {
            gf.fields_dict[fn].df.hidden = same ? 1 : 0;
            gf.fields_dict[fn].refresh();
        }
    });
}

frappe.ui.form.on("Cheque Table Receive", {
    form_render: function(frm, cdt, cdn) {
        _toggle_exchange_rate_fields(frm, cdt, cdn, 'cheque_table');
    },
    account_currency: function(frm, cdt, cdn) {
        _toggle_exchange_rate_fields(frm, cdt, cdn, 'cheque_table');
    },
    account_currency_from: function(frm, cdt, cdn) {
        _toggle_exchange_rate_fields(frm, cdt, cdn, 'cheque_table');
    }
});

frappe.ui.form.on("Cheque Table Pay", {
    form_render: function(frm, cdt, cdn) {
        _toggle_exchange_rate_fields(frm, cdt, cdn, 'cheque_table_2');
    },
    account_currency: function(frm, cdt, cdn) {
        _toggle_exchange_rate_fields(frm, cdt, cdn, 'cheque_table_2');
    },
    account_currency_from: function(frm, cdt, cdn) {
        _toggle_exchange_rate_fields(frm, cdt, cdn, 'cheque_table_2');
    }
});

// --- cheque_currency change: update account currency and refresh exchange rate ---
frappe.ui.form.on("Cheque Table Receive", "cheque_currency", function(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    row._rate_manually_set = false;
    if (row.cheque_currency) {
        frappe.model.set_value(cdt, cdn, "account_currency", row.cheque_currency);
        update_target_exchange_rate(frm, row, 'cheque_table', true);
        update_amount_in_usd(frm, locals[cdt][cdn], 'cheque_table');
    }
});
frappe.ui.form.on("Cheque Table Pay", "cheque_currency", function(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    row._rate_manually_set = false;
    if (row.cheque_currency) {
        frappe.model.set_value(cdt, cdn, "account_currency_from", row.cheque_currency);
        update_target_exchange_rate(frm, row, 'cheque_table_2', true);
        update_amount_in_usd(frm, locals[cdt][cdn], 'cheque_table_2');
    }
});

// --- posting_date change: refresh exchange rates for all rows that were not manually set ---
frappe.ui.form.on("Multiple Cheque Entry", "posting_date", function(frm) {
    if (frm.fields_dict.cheque_table) {
        frm.doc.cheque_table.forEach(row => {
            if (!row._rate_manually_set) {
                update_target_exchange_rate(frm, row, 'cheque_table', false);
            }
            update_amount_in_usd(frm, row, 'cheque_table');
        });
    }
    if (frm.fields_dict.cheque_table_2) {
        frm.doc.cheque_table_2.forEach(row => {
            if (!row._rate_manually_set) {
                update_target_exchange_rate(frm, row, 'cheque_table_2', false);
            }
            update_amount_in_usd(frm, row, 'cheque_table_2');
        });
    }
});

