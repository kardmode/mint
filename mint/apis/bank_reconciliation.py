import frappe
from frappe import _
from frappe.utils import flt, cstr
import json
import datetime
from erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool import create_payment_entry_bts, create_journal_entry_bts
from erpnext.accounts.party import get_party_account
from erpnext import get_default_cost_center

@frappe.whitelist()
def clear_clearing_date(voucher_type: str, voucher_name: str):
    """
        Clear the clearing date of a voucher
    """
    # using db_set to trigger notification
    payment_entry = frappe.get_doc(voucher_type, voucher_name)

    if payment_entry.has_permission("write"):
        payment_entry.db_set("clearance_date", None)



def validate_voucher_permission(doctype, name):
    if not frappe.has_permission(doctype, "write", doc=name):
        frappe.throw(_("Permission denied: You cannot modify {0} {1}").format(doctype, name), frappe.PermissionError)

def validate_bank_transaction_permission(bank_transaction_name):
    if not frappe.has_permission("Bank Transaction", "write", doc=bank_transaction_name):
        frappe.throw(_("Permission denied: You cannot modify Bank Transaction {0}").format(bank_transaction_name), frappe.PermissionError)

def validate_party_permission(party_type, party):
    if party and party_type and not frappe.has_permission(party_type, "read", doc=party):
        frappe.throw(_("Permission denied for {0} {1}").format(party_type, party), frappe.PermissionError)

def set_multi_currency_amounts(pe):
    """Set real exchange rates when the bank and party/offset accounts differ in currency."""
    company_currency = frappe.get_cached_value("Company", pe.company, "default_currency")
    pe.paid_from_account_currency = frappe.get_cached_value("Account", pe.paid_from, "account_currency")
    pe.paid_to_account_currency = frappe.get_cached_value("Account", pe.paid_to, "account_currency")

    from erpnext.setup.utils import get_exchange_rate

    pe.source_exchange_rate = (
        1.0
        if pe.paid_from_account_currency == company_currency
        else (get_exchange_rate(pe.paid_from_account_currency, company_currency, pe.posting_date) or 1.0)
    )
    pe.target_exchange_rate = (
        1.0
        if pe.paid_to_account_currency == company_currency
        else (get_exchange_rate(pe.paid_to_account_currency, company_currency, pe.posting_date) or 1.0)
    )

    if pe.payment_type == "Receive" and pe.source_exchange_rate:
        base_amount = flt(pe.received_amount) * pe.target_exchange_rate
        pe.paid_amount = flt(base_amount / pe.source_exchange_rate, pe.precision("paid_amount"))
        pe.base_paid_amount = base_amount
        pe.base_received_amount = base_amount
    elif pe.payment_type == "Pay" and pe.target_exchange_rate:
        base_amount = flt(pe.paid_amount) * pe.source_exchange_rate
        pe.received_amount = flt(base_amount / pe.target_exchange_rate, pe.precision("received_amount"))
        pe.base_paid_amount = base_amount
        pe.base_received_amount = base_amount
    elif pe.payment_type == "Internal Transfer":
        if pe.paid_from_account_currency == pe.paid_to_account_currency:
            pe.received_amount = pe.paid_amount
            pe.base_paid_amount = flt(pe.paid_amount) * pe.source_exchange_rate
            pe.base_received_amount = pe.base_paid_amount
        else:
            base_amount = flt(pe.paid_amount) * pe.source_exchange_rate
            pe.received_amount = flt(base_amount / pe.target_exchange_rate, pe.precision("received_amount"))
            pe.base_paid_amount = base_amount
            pe.base_received_amount = base_amount

@frappe.whitelist()
def reconcile_vouchers(bank_transaction_name: str | int, vouchers: str, is_new_voucher: bool = False):
	 
    # updated clear date of all the vouchers based on the bank transaction
    validate_bank_transaction_permission(bank_transaction_name)
    vouchers = json.loads(vouchers)
    for v in vouchers:
        validate_voucher_permission(v["payment_doctype"], v["payment_name"])
    transaction = frappe.get_doc("Bank Transaction", bank_transaction_name)
    
    # Add the vouchers with zero allocation. Save() will perform the allocations and clearance
    # We are overriding the default behavior of the method to set the reconciliation type
    if 0.0 >= transaction.unallocated_amount:
        frappe.throw(_("Bank Transaction {0} is already fully reconciled").format(transaction.name))
    
    for voucher in vouchers:
        transaction.append(
            "payment_entries",
            {
                "payment_document": voucher["payment_doctype"],
                "payment_entry": voucher["payment_name"],
                "allocated_amount": 0.0,  # Temporary
                "reconciliation_type": "Voucher Created" if is_new_voucher else "Matched",
            },
        )
    if hasattr(transaction, "validate_duplicate_references"):
        transaction.validate_duplicate_references()
    else:
        # Fallback for ERPNext v14: validate against duplicate voucher references
        seen = set()
        for pe in transaction.payment_entries:
            ref = (pe.payment_document, pe.payment_entry)
            if ref in seen:
                frappe.throw(_("Payment Entry {0} already added").format(pe.payment_entry))
            seen.add(ref)

    transaction.allocate_payment_entries()

    if hasattr(transaction, "update_allocated_amount"):
        transaction.update_allocated_amount()
    elif hasattr(transaction, "update_allocations"):
        transaction.update_allocations()

    transaction.set_status()
    transaction.save()
    
    return transaction

@frappe.whitelist()
def unreconcile_transaction(transaction_name: str | int):
    """
        Unreconcile an entire bank transaction - so this does not handle individual entries

        If the individual entries in the bank transaction are matched, just remove the payment entries
        Else, cancel the individual entries
    """
    transaction = frappe.get_doc("Bank Transaction", transaction_name)

    vouchers_to_cancel = []

    for entry in transaction.payment_entries:
        if entry.reconciliation_type == "Voucher Created":
            vouchers_to_cancel.append({
                "doctype": entry.payment_document,
                "name": entry.payment_entry,
            })
            
    transaction.remove_payment_entries()

    for voucher in vouchers_to_cancel:
        frappe.get_doc(voucher["doctype"], voucher["name"]).cancel()

@frappe.whitelist()
def undo_reconciliation_action(bank_transaction_id: str | int, voucher_type: str, voucher_id: str | int):
    """
     API to remove a single reconciliation action - for example only undoing one voucher instead of undoing the entire transaction
    """

    bank_transaction = frappe.get_doc("Bank Transaction", bank_transaction_id)

    # Find the voucher in the bank transaction and depending on the action, either remove it or cancel the voucher
    for entry in bank_transaction.payment_entries:
        if entry.payment_document == voucher_type and entry.payment_entry == voucher_id:
            if entry.reconciliation_type  == "Voucher Created":
                frappe.get_doc(voucher_type, voucher_id).cancel()
            else:
                bank_transaction.remove_payment_entry(entry)
                bank_transaction.save()

    return {
        "success": True,
    }


@frappe.whitelist(methods=["POST"])
def create_bulk_internal_transfer(bank_transaction_names: list[str|int], 
                                  bank_account: str):
    """
        Create an internal transfer for multiple bank transactions
    """
    output = []

    for bank_transaction_name in bank_transaction_names:

        bank_transaction = frappe.db.get_value("Bank Transaction", bank_transaction_name, ["name", "withdrawal", "bank_account", "date", "reference_number", "description"], as_dict=True)

        transaction_account = frappe.get_cached_value("Bank Account", bank_transaction.bank_account, "account")

        is_withdrawal = bank_transaction.withdrawal > 0.0

        if is_withdrawal:
            paid_from = transaction_account
            paid_to = bank_account
        else:
            paid_from = bank_account
            paid_to = transaction_account
        
        reference_no = (bank_transaction.reference_number or bank_transaction.description or '')[:140]
        
        final_transaction = create_internal_transfer(bank_transaction_name=bank_transaction.name,
                                 posting_date=bank_transaction.date,
                                 reference_date=bank_transaction.date,
                                 reference_no=reference_no,
                                 paid_from=paid_from,
                                 paid_to=paid_to,)
        
        output.append(final_transaction)
    
    return output

@frappe.whitelist()
def create_internal_transfer(bank_transaction_name: str|int, 
                             posting_date: str | datetime.date, 
                             reference_date: str | datetime.date, 
                             reference_no: str, 
                             paid_from: str, 
                             paid_to: str,
                             custom_remarks: bool = False,
                             remarks: str = None,
                             mirror_transaction_name: str | int = None,
                             dimensions: dict = None):
    """
    Create an internal transfer payment entry
    """

    bank_transaction = frappe.get_doc("Bank Transaction", bank_transaction_name)

    bank_account = frappe.get_cached_value("Bank Account", bank_transaction.bank_account, "account")
    company = frappe.get_cached_value("Account", bank_account, "company")

    is_withdrawal = bank_transaction.withdrawal > 0.0

    pe = frappe.new_doc("Payment Entry")

    pe.company = company
    pe.payment_type = "Internal Transfer"
    pe.posting_date = posting_date
    pe.reference_date = reference_date
    pe.reference_no = reference_no
    pe.custom_remarks = custom_remarks
    pe.paid_amount = bank_transaction.unallocated_amount
    pe.received_amount = bank_transaction.unallocated_amount

    set_multi_currency_amounts(pe)

    if custom_remarks:
        pe.remarks = remarks
    
    if dimensions:
        pe.update(dimensions)

    if is_withdrawal:
         pe.paid_to = paid_to
         pe.paid_from = bank_account
    else:
         pe.paid_from = paid_from
         pe.paid_to = bank_account
    
    pe.insert()
    pe.submit()

    vouchers = json.dumps(
		[
			{
				"payment_doctype": "Payment Entry",
				"payment_name": pe.name,
				"amount": bank_transaction.unallocated_amount,
			}
		]
	)

    transaction_id = reconcile_vouchers(bank_transaction_name, vouchers, is_new_voucher=True)

    if mirror_transaction_name:
        # Reconcile the mirror transaction
        reconcile_vouchers(mirror_transaction_name, vouchers, is_new_voucher=False)

    return {
        "transaction": transaction_id,
        "payment_entry": pe,
    }

@frappe.whitelist(methods=['POST'])
def create_bulk_bank_entry_and_reconcile(bank_transactions: list[str|int], 
                                         account: str):
    """
     Create bank entries for all transactions and reconcile them
    """

    output = []

    for bank_transaction in bank_transactions:
        transactions_details = frappe.db.get_value("Bank Transaction", bank_transaction, ["name", "deposit", "withdrawal", "bank_account", "currency", "unallocated_amount", "date", "reference_number", "description", "company"], as_dict=True)

        is_credit_card = frappe.get_cached_value("Bank Account", transactions_details.bank_account, "is_credit_card")

        # Check Number will be limited to 140 characters
        cheque_no = (transactions_details.reference_number or transactions_details.description or '')[:140]

        is_withdrawal = transactions_details.withdrawal > 0.0

        entries = []

        gl_account = frappe.get_cached_value("Bank Account", transactions_details.bank_account, "account")

        if is_withdrawal:
            entries.append({
                "account": gl_account,
                "bank_account": transactions_details.bank_account,
                "credit_in_account_currency": transactions_details.unallocated_amount,
                "credit": transactions_details.unallocated_amount,
                "debit_in_account_currency": 0,
                "debit": 0,
            })

            entries.append({
                "account": account,
                "credit": 0,
                "debit": transactions_details.unallocated_amount,
            })
        else:
            entries.append({
                "account": gl_account,
                "bank_account": transactions_details.bank_account,
                "debit_in_account_currency": transactions_details.unallocated_amount,
                "debit": transactions_details.unallocated_amount,
                "credit_in_account_currency": 0,
                "credit": 0,
            })

            entries.append({
                "account": account,
                "debit": 0,
                "credit": transactions_details.unallocated_amount,
            })

        final_transaction = create_bank_entry_and_reconcile(bank_transaction_name=bank_transaction,
                                        cheque_date=transactions_details.date,
                                        posting_date=transactions_details.date,
                                        cheque_no=cheque_no,
                                        user_remark=transactions_details.description,
                                        entries=entries,
                                        voucher_type=("Credit Card Entry" if is_credit_card else "Bank Entry"))
        
        output.append(final_transaction)
    
    return output



@frappe.whitelist(methods=['POST'])
def create_bank_entry_and_reconcile(bank_transaction_name: str | int, 
                                    cheque_date: str | datetime.date,
                                    posting_date: str | datetime.date,
                                    cheque_no: str,
                                    entries: list,
                                    user_remark: str = None,
                                    voucher_type: str = "Bank Entry",
                                    dimensions: dict = None):
    """
        Create a bank entry and reconcile it with the bank transaction
    """
    # Create a new journal entry based on the bank transaction
    bank_transaction = frappe.db.get_values(
        "Bank Transaction",
        bank_transaction_name,
        fieldname=["name", "deposit", "withdrawal", "bank_account", "currency", "unallocated_amount"],
        as_dict=True,
    )[0]

    bank_account = frappe.get_cached_value("Bank Account", bank_transaction.bank_account, "account")
    company = frappe.get_cached_value("Account", bank_account, "company")

    default_cost_center = get_default_cost_center(company)

    bank_entry = frappe.get_doc({
        "doctype": "Journal Entry",
        "voucher_type": voucher_type,
        "company": company,
        "cheque_date": cheque_date,
        "posting_date": posting_date,
        "cheque_no": cheque_no,
        "user_remark": user_remark,
    })
    
    if not dimensions:
        dimensions = {}
    
    for entry in entries:
        # Check if this account is a Income or Expense Account
        # If it is, and no cost center is added, select the company default cost center
        cost_center = entry.get("cost_center")

        if not cost_center:
            report_type = frappe.get_cached_value("Account", entry["account"], "report_type")
            if report_type == "Profit and Loss":
                # Cost center is required
                cost_center = default_cost_center
        
        bank_entry.append("accounts", {
            "account": entry["account"],
            # TODO: Multi currency support
            "debit_in_account_currency": entry.get("debit"),
            "credit_in_account_currency": entry.get("credit"),
            "debit": entry.get("debit"),
            "credit": entry.get("credit"),
            "party_type": entry.get("party_type") if entry.get("party") else None,
            "party": entry.get("party"),
            "user_remark": entry.get("user_remark"),
            **entry,
            "cost_center": cost_center
        })

    bank_entry.insert()
    bank_entry.submit()

    if bank_transaction.deposit > 0.0:
        paid_amount = bank_transaction.deposit
    else:
        paid_amount = bank_transaction.withdrawal

    transaction = reconcile_vouchers(bank_transaction_name, json.dumps([{
        "payment_doctype": "Journal Entry",
        "payment_name": bank_entry.name,
        "amount": paid_amount,
    }]), is_new_voucher=True)

    return {
        "transaction": transaction,
        "journal_entry": bank_entry,
    }

@frappe.whitelist(methods=['POST'])
def create_bulk_payment_entry_and_reconcile(bank_transaction_names: list[str | int], 
                                            party_type: str, 
                                            party: str | int, 
                                            account: str,
                                            mode_of_payment: str | None = None):
    """
        Create a payment entry and reconcile it with the bank transaction
    """

    output = []

    for bank_transaction_name in bank_transaction_names:
        bank_transaction = frappe.db.get_value("Bank Transaction", bank_transaction_name, ["name", "deposit", "withdrawal", "bank_account", "currency", "unallocated_amount", "date", "reference_number", "description", "company"], as_dict=True)

        transaction_account = frappe.get_cached_value("Bank Account", bank_transaction.bank_account, "account")

        is_withdrawal = bank_transaction.withdrawal > 0.0

        if is_withdrawal:
            paid_from = transaction_account
            paid_to = account
        else:
            paid_from = account
            paid_to = transaction_account
        
        validate_party_permission(party_type, party)
        payment_entry_doc = frappe.get_doc({
            "doctype": "Payment Entry",
            "payment_type": "Pay" if is_withdrawal else "Receive",
            "bank_account": bank_transaction.bank_account,
            "company": bank_transaction.company,
            "mode_of_payment": mode_of_payment,
            "party_type": party_type,
            "party": party,
            "paid_from": paid_from,
            "paid_to": paid_to,
            "paid_amount": bank_transaction.unallocated_amount,
            "base_paid_amount": bank_transaction.unallocated_amount,
            "received_amount": bank_transaction.unallocated_amount,
            "base_received_amount": bank_transaction.unallocated_amount,
            "target_exchange_rate": 1,
            "source_exchange_rate": 1,
            "reference_date": bank_transaction.date,
            "posting_date": bank_transaction.date,
            "reference_no": (bank_transaction.reference_number or bank_transaction.description or '')[:140],
        })

        set_multi_currency_amounts(payment_entry_doc)
        payment_entry_doc.insert()
        payment_entry_doc.submit()

        final_transaction = reconcile_vouchers(bank_transaction_name, json.dumps([{
            "payment_doctype": "Payment Entry",
            "payment_name": payment_entry_doc.name,
            "amount": payment_entry_doc.paid_amount,
        }]), is_new_voucher=True)

        output.append({
            "transaction": final_transaction,
            "payment_entry": payment_entry_doc,
        })

    return output

    
@frappe.whitelist(methods=['POST'])
def create_payment_entry_and_reconcile(bank_transaction_name: str | int, 
                                       payment_entry_doc: dict):
    """
        Create a payment entry and reconcile it with the bank transaction
    """
    if payment_entry_doc.get("party") and payment_entry_doc.get("party_type"):
        validate_party_permission(payment_entry_doc.get("party_type"), payment_entry_doc.get("party"))
    payment_entry = frappe.get_doc({
        **payment_entry_doc,
        "doctype": "Payment Entry",
    })
    set_multi_currency_amounts(payment_entry)
    payment_entry.insert()
    payment_entry.submit()
    transaction = reconcile_vouchers(bank_transaction_name, json.dumps([{
        "payment_doctype": "Payment Entry",
        "payment_name": payment_entry.name,
        "amount": payment_entry.paid_amount,
    }]), is_new_voucher=True)

    return {
        "transaction": transaction,
        "payment_entry": payment_entry,
    }


@frappe.whitelist(methods=['GET'])
def get_account_defaults(account: str):
    """
        Get the default cost center and write off account for an account
    """
    company, report_type = frappe.db.get_value("Account", account, ["company", "report_type"])

    return get_default_cost_center(company) if report_type == "Profit and Loss" else  ""


@frappe.whitelist(methods=["GET"])
def get_party_details(company: str, party_type: str, party: str | int):

    if not frappe.db.exists(party_type, party):
        frappe.throw(_("{0} {1} does not exist").format(party_type, party))

    party_account = get_party_account(party_type, party, company)
    _party_name = "title" if party_type == "Shareholder" else party_type.lower() + "_name"
    party_name = frappe.db.get_value(party_type, party, _party_name)

    return {
        "party_account": party_account,
        "party_name": party_name,
    }

@frappe.whitelist(methods=["GET"])
def search_for_transfer_transaction(transaction_id: str | int):
    """
    When users try to create a transfer, we could help them by searching for the mirror transaction.

    So for a withdrawal of 1000, we could search for a deposit of 1000 on the same date.

    If the mirror transaction is found, we return the bank account and account details.
    """
    company, withdrawal, deposit, date, bank_account = frappe.db.get_value("Bank Transaction", transaction_id, ["company", "withdrawal", "deposit", "date", "bank_account"])

    
    days = frappe.db.get_single_value("Mint Settings", "transfer_match_days")

    if not days:
        days = 4

    min_date = frappe.utils.add_days(date, -days)
    max_date = frappe.utils.add_days(date, days)
    mirror_tx = frappe.db.get_list("Bank Transaction", filters={
        "company": company,
        "date": ["between", [min_date, max_date]],
        "withdrawal": deposit,
        "bank_account": ["!=", bank_account],
        "deposit": withdrawal,
        "docstatus": 1,
        "status": "Unreconciled",
    }, fields=["name", "bank_account", "reference_number", "date", "description", "withdrawal", "deposit", "currency"])

    if len(mirror_tx) == 1:
        return {
            "name": mirror_tx[0].name,
            "reference_number": mirror_tx[0].reference_number,
            "description": mirror_tx[0].description,
            "currency": mirror_tx[0].currency,
            "withdrawal": mirror_tx[0].withdrawal,
            "deposit": mirror_tx[0].deposit,
            "date": mirror_tx[0].date,
            "bank_account": mirror_tx[0].bank_account,
            "account": frappe.get_cached_value("Bank Account", mirror_tx[0].bank_account, "account"),
        }

    return None



@frappe.whitelist()
def auto_reconcile_vouchers(
    bank_account: str,
    from_date: str | None = None,
    to_date: str | None = None,
    filter_by_reference_date: bool | None = None,
    from_reference_date: str | None = None,
    to_reference_date: str | None = None,
):
    if not bank_account or not frappe.has_permission("Bank Account", "read", doc=bank_account):
        frappe.throw(_("Permission denied for Bank Account {0}").format(bank_account), frappe.PermissionError)

    from mint.apis.transactions import get_bank_transactions
    listed_transactions = get_bank_transactions(bank_account, from_date=from_date, to_date=to_date, all_transactions=False)

    bank_transactions = []
    for transaction in listed_transactions:
        tx_name = transaction.name if hasattr(transaction, "name") else transaction.get("name")
        if frappe.has_permission("Bank Transaction", "write", doc=tx_name):
            bank_transactions.append(transaction)

    if not bank_transactions:
        return {
            "reconciled": 0,
            "partially_reconciled": 0,
            "message": _("No unreconciled transactions found to match.")
        }

    if len(bank_transactions) > 10:
        frappe.enqueue(
            method="mint.apis.bank_reconciliation.start_auto_reconcile",
            queue="long",
            bank_transactions=bank_transactions,
            from_date=from_date,
            to_date=to_date,
            filter_by_reference_date=filter_by_reference_date,
            from_reference_date=from_reference_date,
            to_reference_date=to_reference_date,
        )
        return {
            "queued": True,
            "count": len(bank_transactions),
            "message": _("Auto-reconciliation started in the background for {0} transactions.").format(len(bank_transactions))
        }
    else:
        return start_auto_reconcile(
            bank_transactions,
            from_date,
            to_date,
            filter_by_reference_date,
            from_reference_date,
            to_reference_date,
        )


def start_auto_reconcile(
    bank_transactions, from_date=None, to_date=None, filter_by_reference_date=None, from_reference_date=None, to_reference_date=None
):
    from erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool import get_linked_payments
    
    frappe.flags.auto_reconcile_vouchers = True
    reconciled = []
    partially_reconciled = []

    for transaction in bank_transactions:
        tx_name = transaction.name if hasattr(transaction, "name") else transaction.get("name")
        tx_doc = frappe.get_doc("Bank Transaction", tx_name)
        if flt(tx_doc.unallocated_amount) <= 0:
            continue

        try:
            linked_payments = get_linked_payments(
                tx_name,
                ["payment_entry", "journal_entry", "sales_invoice"],
                from_date,
                to_date,
                filter_by_reference_date,
                from_reference_date,
                to_reference_date,
            )
        except Exception:
            continue

        if not linked_payments:
            continue

        # Look for exact 1:1 match by amount
        exact_match = None
        for payment in linked_payments:
            paid_amt = flt(payment.get("paid_amount") or payment.get("allocated_amount"))
            if abs(paid_amt - flt(tx_doc.unallocated_amount)) < 0.001:
                doctype = payment.get("doctype")
                pname = payment.get("name")
                if frappe.has_permission(doctype, "write", doc=pname):
                    exact_match = payment
                    break

        if exact_match:
            vouchers = [{
                "payment_doctype": exact_match.get("doctype"),
                "payment_name": exact_match.get("name"),
                "amount": exact_match.get("paid_amount") or exact_match.get("allocated_amount"),
            }]
            try:
                updated_tx = reconcile_vouchers(tx_name, json.dumps(vouchers), is_new_voucher=False)
                if updated_tx.status == "Reconciled":
                    reconciled.append(tx_name)
                elif flt(tx_doc.unallocated_amount) != flt(updated_tx.unallocated_amount):
                    partially_reconciled.append(tx_name)
            except Exception as e:
                frappe.log_error(title=_("Auto Reconcile Failed for {0}").format(tx_name), message=str(e))

    frappe.flags.auto_reconcile_vouchers = False

    message, indicator = get_auto_reconcile_message(partially_reconciled, reconciled)
    frappe.publish_realtime("mint_auto_reconcile_completed", {
        "reconciled": len(reconciled),
        "partially_reconciled": len(partially_reconciled),
        "message": message,
        "indicator": indicator
    }, user=frappe.session.user)

    return {
        "reconciled": len(reconciled),
        "partially_reconciled": len(partially_reconciled),
        "message": message,
        "indicator": indicator
    }


def get_auto_reconcile_message(partially_reconciled, reconciled):
    if not partially_reconciled and not reconciled:
        return _("No exact matches found for auto reconciliation."), "blue"

    msg = []
    if reconciled:
        msg.append(_("{0} transaction(s) fully reconciled.").format(len(reconciled)))
    if partially_reconciled:
        msg.append(_("{0} transaction(s) partially reconciled.").format(len(partially_reconciled)))

    return " ".join(msg), "green"
