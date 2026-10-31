# Copyright (c) 2025, The Commit Company (Algocode Technologies Pvt. Ltd.) and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class MintBankStatementImport(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF
		from mint.mint.doctype.mint_bank_statement_import_transactions.mint_bank_statement_import_transactions import MintBankStatementImportTransactions

		amended_from: DF.Link | None
		bank_account: DF.Link
		currency: DF.Link | None
		error: DF.Code | None
		file: DF.Attach | None
		file_type: DF.Literal["PDF", "Excel", "CSV"]
		status: DF.Literal["Not Started", "Completed", "Error"]
		transactions: DF.Table[MintBankStatementImportTransactions]
	# end: auto-generated types

	def before_validate(self):
		account = frappe.get_cached_value("Bank Account", self.bank_account, "account")
		self.currency = frappe.get_cached_value("Account", account, "account_currency")
		# For all string amounts, compute the actual amount and type
		for transaction in self.transactions:
			if transaction.string_amount:
				amount, tx_type = self.parse_string_amount(transaction.string_amount)
				transaction.amount = amount
				transaction.type = tx_type

	def parse_string_amount(self, string_amount: str):
		"""
		Parse the string amount and return the amount and type
		"""
		# If the string has "cr" - then it's a deposit. Else it's a withdrawal
		if "cr" in string_amount.lower():
			return string_amount.lower().replace("cr", "").replace(" ", ""), "Deposit"
		else:
			return string_amount.lower().replace("dr", "").replace(" ", ""), "Withdrawal"

	@frappe.whitelist()
	def process_file(self):
		if not self.file:
			frappe.throw(_("Please upload a file"))

		try:
			# Check if Mint AI Settings has enable_ai turned on
			ai_enabled = False
			try:
				ai_settings = frappe.get_single("Mint AI Settings")
				ai_enabled = bool(getattr(ai_settings, "enable_ai", False))
			except Exception:
				ai_enabled = False

			if ai_enabled:
				self.process_with_ai()
			elif self.file_type == "PDF":
				self.process_pdf_legacy()
			else:
				self.process_tabular_fallback()

			self.status = "Completed"
			self.error = None
		except Exception as e:
			self.status = "Error"
			self.error = str(e)
			frappe.log_error(f"Mint Statement Import Failed: {str(e)}", "Mint Bank Statement Import")
			raise

	def process_with_ai(self):
		"""
		Process file using universal AI statement extractor (PDF, XLSX, XLS, CSV).
		"""
		from mint.apis.ai_statement import extract_statement_from_file

		transactions = extract_statement_from_file(self.file)
		if not transactions:
			frappe.throw(_("No transactions could be extracted from the file."))

		self.transactions = []
		for tx in transactions:
			self.append("transactions", {
				"date": tx.get("date"),
				"amount": tx.get("amount"),
				"type": tx.get("type"),
				"description": tx.get("description"),
				"reference": tx.get("reference") or "",
			})

	def process_pdf_legacy(self):
		"""
		Process PDF using legacy Google Document AI if configured.
		"""
		from mint.apis.google_ai import run_bank_statement_processor

		transactions = run_bank_statement_processor(self.file)
		transactions.sort(key=lambda x: frappe.utils.getdate(x.get("date")))
		self.transactions = []
		for transaction in transactions:
			self.append("transactions", {
				"date": transaction.get("date"),
				"amount": transaction.get("amount"),
				"type": transaction.get("type"),
				"description": transaction.get("description"),
				"reference": transaction.get("reference") or "",
			})

	def process_tabular_fallback(self):
		"""
		Fallback for Excel/CSV when AI is disabled.
		"""
		from mint.apis.statement_import import get_statement_details

		details = get_statement_details(self.file, self.bank_account)
		final_txs = details.get("final_transactions", [])
		self.transactions = []
		for tx in final_txs:
			withdrawal = float(tx.get("withdrawal") or 0)
			deposit = float(tx.get("deposit") or 0)
			tx_type = "Deposit" if deposit > 0 else "Withdrawal"
			amount = deposit if deposit > 0 else withdrawal
			self.append("transactions", {
				"date": tx.get("date"),
				"amount": amount,
				"type": tx_type,
				"description": tx.get("description"),
				"reference": tx.get("reference") or "",
			})

	def before_submit(self):
		# Validate all rows have an amount and a type
		for transaction in self.transactions:
			if not transaction.get("amount") or not transaction.get("type"):
				frappe.throw(_("All rows must have an amount and a type. Missing in row {0}").format(transaction.get("idx")))

	def on_submit(self):
		if not self.transactions:
			frappe.throw(_("No transactions found"))

		for transaction in self.transactions:
			bank_tx = frappe.get_doc({
				"doctype": "Bank Transaction",
				"date": transaction.get("date"),
				"status": "Unreconciled",
				"bank_account": self.bank_account,
				"withdrawal": transaction.get("amount") if transaction.get("type") == "Withdrawal" else 0,
				"deposit": transaction.get("amount") if transaction.get("type") == "Deposit" else 0,
				"description": transaction.get("description"),
				"reference_number": transaction.get("reference"),
				"currency": self.currency,
			})
			bank_tx.insert()
			bank_tx.submit()
			transaction.imported = 1
