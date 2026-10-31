# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from mint.apis.ai_client import MintAIClient


MATCHING_SYSTEM_PROMPT = """You are an expert ERPNext reconciliation assistant.
Given a bank transaction narration and amount, you must identify:
1. The most likely Party (Customer or Supplier) from the candidate party list.
2. The most appropriate Ledger Account from the candidate chart of accounts list.

Respond strictly with a JSON object:
{
  "party_type": "Supplier" or "Customer" or null,
  "party": "Exact name matching one candidate party, or null if no strong match",
  "account": "Exact account name matching one candidate account, or null if no match",
  "confidence": 0.0 to 1.0,
  "reasoning": "A concise explanation for the chosen party and ledger account"
}

Rules:
- Only choose names that actually exist in the candidate lists.
- If unsure or confidence < 0.4, set party and account to null with low confidence.
- For withdrawals / payments, consider suppliers or expense accounts.
- For deposits / inflows, consider customers or income accounts.
"""


@frappe.whitelist()
def suggest_party_and_account(
	bank_transaction_name: str = None,
	description: str = None,
	amount: float = 0.0,
	transaction_type: str = None,
	company: str = None
) -> dict:
	"""
	Suggest the most probable Party (Customer/Supplier) and Ledger Account for a bank transaction.
	Can be called with bank_transaction_name or explicit parameters.
	"""
	if bank_transaction_name:
		tx = frappe.get_doc("Bank Transaction", bank_transaction_name)
		description = tx.description or ""
		amount = float(tx.withdrawal or tx.deposit or 0)
		transaction_type = "Withdrawal" if tx.withdrawal else "Deposit"
		if not company:
			company = tx.company
			if not company and tx.bank_account:
				company = frappe.get_cached_value("Bank Account", tx.bank_account, "company")

	if not description:
		frappe.throw(_("Description / narration is required for party suggestion."))

	if not company:
		try:
			company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value("Global Defaults", "default_company")
		except Exception:
			company = None

	is_withdrawal = (transaction_type == "Withdrawal") or (not transaction_type and amount < 0)

	# Fetch Candidate Parties
	candidate_parties = _fetch_candidate_parties(company, is_withdrawal)
	# Fetch Candidate Accounts
	candidate_accounts = _fetch_candidate_accounts(company, is_withdrawal)

	client = MintAIClient()
	user_prompt = f"""Bank Transaction Details:
- Narration: "{description}"
- Amount: {abs(float(amount or 0.0))}
- Flow: {"Withdrawal (Outflow/Payment)" if is_withdrawal else "Deposit (Inflow/Receipt)"}
- Company: {company or "Default"}

Candidate Parties ({len(candidate_parties)}):
{chr(10).join(f"- {p}" for p in candidate_parties[:100])}

Candidate Accounts ({len(candidate_accounts)}):
{chr(10).join(f"- {a}" for a in candidate_accounts[:100])}

Identify the best matching Party and Ledger Account.
"""

	try:
		res = client.complete(system_prompt=MATCHING_SYSTEM_PROMPT, user_prompt=user_prompt)
		return {
			"success": True,
			"party_type": res.get("party_type"),
			"party": res.get("party"),
			"account": res.get("account"),
			"confidence": float(res.get("confidence") or 0.0),
			"reasoning": res.get("reasoning", "")
		}
	except Exception as e:
		frappe.log_error(f"Mint AI Suggestion Failed: {str(e)}", "Mint AI Matching")
		return {
			"success": False,
			"error": str(e),
			"confidence": 0.0
		}


def _fetch_candidate_parties(company: str = None, is_withdrawal: bool = True) -> list[str]:
	"""Fetch active Suppliers (for withdrawals) or Customers (for deposits)."""
	doctype = "Supplier" if is_withdrawal else "Customer"
	filters = {"disabled": 0}
	parties = []
	try:
		parties = frappe.get_all(doctype, filters=filters, pluck="name", limit=150, order_by="modified desc")
	except Exception:
		pass
	return parties


def _fetch_candidate_accounts(company: str = None, is_withdrawal: bool = True) -> list[str]:
	"""Fetch active Expense / Liability accounts for withdrawals or Income / Asset accounts for deposits."""
	filters = {"is_group": 0}
	if company:
		filters["company"] = company

	if is_withdrawal:
		filters["root_type"] = ["in", ["Expense", "Liability"]]
	else:
		filters["root_type"] = ["in", ["Income", "Asset"]]

	accounts = []
	try:
		accounts = frappe.get_all("Account", filters=filters, pluck="name", limit=150, order_by="modified desc")
	except Exception:
		pass
	return accounts
