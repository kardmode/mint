# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt

import base64
import io
import os
import re
from datetime import datetime
import frappe
from frappe import _
from mint.apis.ai_client import MintAIClient


STATEMENT_SYSTEM_PROMPT = """You are an expert forensic accountant and bank statement ingestion engine.
Your task is to parse bank statement text or documents into a clean, structured JSON list of transactions.

Return strictly a JSON object with this structure:
{
  "transactions": [
    {
      "date": "YYYY-MM-DD",
      "description": "Full transaction description / narration",
      "amount": 1234.50,
      "type": "Deposit" or "Withdrawal",
      "reference": "Reference number, check number, or transaction id (or empty string)"
    }
  ]
}

Strict Rules:
1. "date": ISO 8601 string "YYYY-MM-DD". If year is 2 digits, assume 20xx.
2. "amount": A positive floating-point number. Never include negative signs or currency symbols.
3. "type": Must be exactly "Deposit" (for credit, received, inflow) or "Withdrawal" (for debit, paid, outflow).
4. "description": Preserve the full merchant or payer narration, stripping excessive repeated spaces.
5. "reference": Capture cheque/reference/transaction codes if present.
6. Do NOT invent transactions. Do NOT include statement opening/closing summary balances or page headers as transactions.
"""


def extract_statement_from_file(file_identifier: str) -> list[dict]:
	"""
	Extract transactions from an uploaded file (PDF, XLSX, XLS, CSV) using AI.
	:param file_identifier: File URL or absolute file path.
	:return: List of transaction dicts with keys: date, amount, type, description, reference.
	"""
	file_path, file_bytes, filename = _resolve_file_source(file_identifier)
	ext = os.path.splitext(filename)[1].lower()

	if ext == ".pdf":
		raw_txs = _extract_from_pdf(file_path=file_path, file_bytes=file_bytes)
	elif ext in (".xlsx", ".xls", ".csv"):
		raw_txs = _extract_from_tabular(file_path=file_path, file_bytes=file_bytes, ext=ext)
	else:
		raise frappe.ValidationError(
			_("Unsupported file format '{0}'. Supported formats: .pdf, .xlsx, .xls, .csv").format(ext)
		)

	return _clean_and_sort_transactions(raw_txs)


def _resolve_file_source(file_identifier: str) -> tuple[str, bytes, str]:
	"""Resolve file path, bytes, and filename from URL or local path."""
	file_path = ""
	file_bytes = b""
	filename = os.path.basename(file_identifier)

	if os.path.isfile(file_identifier):
		file_path = file_identifier
		with open(file_path, "rb") as f:
			file_bytes = f.read()
		return file_path, file_bytes, filename

	# If it's a Frappe file_url (e.g. /files/my_statement.pdf or /private/files/...)
	try:
		file_doc = frappe.get_doc("File", {"file_url": file_identifier})
		file_path = file_doc.get_full_path()
		filename = file_doc.file_name or os.path.basename(file_path)
		if os.path.isfile(file_path):
			with open(file_path, "rb") as f:
				file_bytes = f.read()
		else:
			file_bytes = file_doc.get_content()
	except Exception:
		# Fallback if get_full_path is not in Frappe site context
		if os.path.exists(file_identifier):
			file_path = file_identifier
			with open(file_path, "rb") as f:
				file_bytes = f.read()

	if not file_bytes:
		raise frappe.ValidationError(_("Could not locate or read file content for: {0}").format(file_identifier))

	return file_path, file_bytes, filename


def _extract_from_pdf(file_path: str, file_bytes: bytes) -> list[dict]:
	"""Extract transactions from PDF using text extraction or visual inline data."""
	import pypdf

	pdf_file = io.BytesIO(file_bytes)
	reader = pypdf.PdfReader(pdf_file)
	total_pages = len(reader.pages)

	pages_text = []
	for idx, page in enumerate(reader.pages):
		text = page.extract_text() or ""
		if text.strip():
			pages_text.append(f"--- PAGE {idx + 1} OF {total_pages} ---\n{text.strip()}")

	full_text = "\n\n".join(pages_text).strip()
	client = MintAIClient()

	# If digital text extraction succeeded (> 60 characters)
	if len(full_text) > 60:
		# If PDF is very long, process in chunks of 5 pages
		chunk_size = 5
		all_txs = []
		if len(pages_text) > chunk_size:
			for i in range(0, len(pages_text), chunk_size):
				chunk = "\n\n".join(pages_text[i : i + chunk_size])
				user_prompt = f"Parse bank transactions from this statement excerpt:\n\n{chunk}"
				res = client.complete(system_prompt=STATEMENT_SYSTEM_PROMPT, user_prompt=user_prompt)
				all_txs.extend(res.get("transactions", []))
		else:
			user_prompt = f"Parse bank transactions from this bank statement:\n\n{full_text}"
			res = client.complete(system_prompt=STATEMENT_SYSTEM_PROMPT, user_prompt=user_prompt)
			all_txs.extend(res.get("transactions", []))
		return all_txs

	# Scanned / Image-based PDF: Pass base64 bytes to multimodal AI (e.g. Gemini)
	base64_pdf = base64.b64encode(file_bytes).decode("utf-8")
	user_prompt = "Parse all bank transactions visible in this attached bank statement document."
	inline_data = {"mime_type": "application/pdf", "data": base64_pdf}
	res = client.complete(
		system_prompt=STATEMENT_SYSTEM_PROMPT,
		user_prompt=user_prompt,
		inline_data=inline_data
	)
	return res.get("transactions", [])


def _extract_from_tabular(file_path: str, file_bytes: bytes, ext: str) -> list[dict]:
	"""Extract transactions from CSV/Excel table."""
	tabular_lines = []

	if ext == ".csv":
		content = file_bytes.decode("utf-8", errors="replace")
		tabular_lines = content.splitlines()[:500]  # Cap at 500 rows per batch
	elif ext in (".xlsx", ".xls"):
		import openpyxl

		wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
		sheet = wb.active
		for row in sheet.iter_rows(values_only=True):
			if any(row):
				line = " | ".join(str(c) if c is not None else "" for c in row)
				tabular_lines.append(line)
			if len(tabular_lines) >= 500:
				break

	if not tabular_lines:
		return []

	table_text = "\n".join(tabular_lines)
	client = MintAIClient()
	user_prompt = (
		"Extract all bank transactions from the following spreadsheet rows. "
		"Identify date, description, amounts, and whether each row represents a deposit or withdrawal:\n\n"
		f"{table_text}"
	)
	res = client.complete(system_prompt=STATEMENT_SYSTEM_PROMPT, user_prompt=user_prompt)
	return res.get("transactions", [])


def _clean_and_sort_transactions(raw_txs: list) -> list[dict]:
	"""Clean, format amounts, validate dates, and sort transactions chronologically."""
	cleaned = []
	seen = set()

	for item in raw_txs:
		if not isinstance(item, dict):
			continue

		raw_date = str(item.get("date") or "").strip()
		date_str = _normalize_date(raw_date)
		if not date_str:
			continue

		raw_amt = item.get("amount")
		amount = _normalize_amount(raw_amt)
		if amount <= 0:
			continue

		raw_type = str(item.get("type") or "").strip().capitalize()
		tx_type = "Deposit" if "Dep" in raw_type or "Credit" in raw_type or "Cr" in raw_type else "Withdrawal"

		desc = str(item.get("description") or "").strip()
		ref = str(item.get("reference") or "").strip()

		# Dedup key
		key = (date_str, amount, tx_type, desc[:40])
		if key in seen:
			continue
		seen.add(key)

		cleaned.append({
			"date": date_str,
			"amount": amount,
			"type": tx_type,
			"description": desc,
			"reference": ref
		})

	# Sort by date ascending
	cleaned.sort(key=lambda x: x["date"])
	return cleaned


def _normalize_date(val: str) -> str:
	"""Convert dates from various formats (DD/MM/YYYY, MM/DD/YYYY, YYYY-MM-DD, etc.) to YYYY-MM-DD."""
	if not val:
		return ""
	val = val.strip().split("T")[0]  # Strip ISO time if present

	# Direct YYYY-MM-DD
	if re.match(r"^\d{4}-\d{2}-\d{2}$", val):
		try:
			datetime.strptime(val, "%Y-%m-%d")
			return val
		except ValueError:
			return ""

	formats = [
		"%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y",
		"%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y",
		"%Y/%m/%d", "%d/%m/%y", "%m/%d/%y"
	]
	for fmt in formats:
		try:
			dt = datetime.strptime(val, fmt)
			return dt.strftime("%Y-%m-%d")
		except ValueError:
			pass

	try:
		if hasattr(frappe, "utils") and hasattr(frappe.utils, "getdate"):
			dt = frappe.utils.getdate(val)
			if dt and re.match(r"^\d{4}-\d{2}-\d{2}$", str(dt)):
				return str(dt)
	except Exception:
		pass

	return ""


def _normalize_amount(val) -> float:
	"""Convert amount value to clean positive float."""
	if val is None:
		return 0.0
	if isinstance(val, (int, float)):
		return abs(float(val))

	s = str(val).strip()
	# Remove currency symbols and formatting commas
	s = re.sub(r"[^\d.-]", "", s)
	try:
		return abs(float(s))
	except ValueError:
		return 0.0


@frappe.whitelist()
def extract_statement_api(file_url: str):
	"""Whitelisted API for frontend / Copilot to extract transactions from a file."""
	if not file_url:
		frappe.throw(_("File URL is required."))
	txs = extract_statement_from_file(file_url)
	return {
		"success": True,
		"count": len(txs),
		"transactions": txs
	}
