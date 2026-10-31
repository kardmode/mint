# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt

import sys
import os
import types
import unittest
from unittest.mock import MagicMock, patch

# Ensure app path is in sys.path
mint_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if mint_path not in sys.path:
	sys.path.insert(0, mint_path)

# Mock frappe module if running outside bench environment
if "frappe" not in sys.modules:
	mock_frappe = types.ModuleType("frappe")
	mock_frappe._ = lambda msg: msg
	mock_frappe.throw = MagicMock(side_effect=lambda msg, *args, **kwargs: Exception(msg))
	mock_frappe.msgprint = MagicMock()
	mock_frappe.log_error = MagicMock()
	mock_frappe.whitelist = lambda *args, **kwargs: (lambda fn: fn)
	mock_frappe.get_single = MagicMock()
	mock_frappe.get_doc = MagicMock()
	mock_frappe.get_all = MagicMock(return_value=[])
	mock_frappe.get_cached_value = MagicMock(return_value="AED")
	mock_frappe.get_installed_apps = MagicMock(return_value=["frappe", "erpnext", "mint"])
	mock_frappe.ValidationError = type("ValidationError", (Exception,), {})

	utils_mod = types.ModuleType("frappe.utils")
	utils_mod.getdate = MagicMock(side_effect=lambda d: str(d) if d else None)
	mock_frappe.utils = utils_mod
	sys.modules["frappe"] = mock_frappe
	sys.modules["frappe.utils"] = utils_mod

	model_doc = types.ModuleType("frappe.model.document")
	class BaseDocument:
		def __init__(self, *args, **kwargs):
			for k, v in kwargs.items():
				setattr(self, k, v)
		def append(self, key, val):
			if not hasattr(self, key):
				setattr(self, key, [])
			getattr(self, key).append(val)
	model_doc.Document = BaseDocument
	sys.modules["frappe.model.document"] = model_doc

import frappe
from mint.apis.ai_client import MintAIClient
from mint.apis.ai_statement import (
	_normalize_date,
	_normalize_amount,
	_clean_and_sort_transactions,
	_extract_from_pdf,
	extract_statement_from_file,
)
from mint.apis.ai_matching import suggest_party_and_account
from mint.mint.doctype.mint_ai_settings.mint_ai_settings import MintAISettings
from mint.mint.doctype.mint_bank_statement_import.mint_bank_statement_import import MintBankStatementImport


class TestMintAIFeatures(unittest.TestCase):

	def test_normalize_date(self):
		"""Test date normalization across various international formats."""
		self.assertEqual(_normalize_date("2026-10-31"), "2026-10-31")
		self.assertEqual(_normalize_date("31/10/2026"), "2026-10-31")
		self.assertEqual(_normalize_date("31-10-2026"), "2026-10-31")
		self.assertEqual(_normalize_date("31.10.2026"), "2026-10-31")
		self.assertEqual(_normalize_date("31 Oct 2026"), "2026-10-31")
		self.assertEqual(_normalize_date("2026-10-31T14:30:00"), "2026-10-31")
		self.assertEqual(_normalize_date("invalid-date"), "")
		self.assertEqual(_normalize_date(""), "")

	def test_normalize_amount(self):
		"""Test amount normalization with currency symbols, negatives, and whitespace."""
		self.assertEqual(_normalize_amount(150.50), 150.50)
		self.assertEqual(_normalize_amount(-450.00), 450.00)
		self.assertEqual(_normalize_amount("$1,234.56"), 1234.56)
		self.assertEqual(_normalize_amount("AED 5,000.00"), 5000.00)
		self.assertEqual(_normalize_amount("€ 99.90"), 99.90)
		self.assertEqual(_normalize_amount(None), 0.0)
		self.assertEqual(_normalize_amount(""), 0.0)

	def test_clean_and_sort_transactions(self):
		"""Test transaction deduplication and chronological ordering."""
		raw = [
			{"date": "2026-10-20", "amount": "500.00", "type": "Withdrawal", "description": "Supplier Payment A"},
			{"date": "2026-10-05", "amount": "1,200.00", "type": "Deposit", "description": "Customer Receipt B"},
			{"date": "2026-10-20", "amount": 500.00, "type": "Withdrawal", "description": "Supplier Payment A"},  # Duplicate
			{"date": "invalid", "amount": 100.0, "type": "Deposit"},  # Invalid date
			{"date": "2026-10-15", "amount": 0, "type": "Withdrawal"},  # Zero amount
			"not a dict"  # Garbage entry
		]
		cleaned = _clean_and_sort_transactions(raw)
		self.assertEqual(len(cleaned), 2)
		self.assertEqual(cleaned[0]["date"], "2026-10-05")
		self.assertEqual(cleaned[0]["amount"], 1200.00)
		self.assertEqual(cleaned[0]["type"], "Deposit")
		self.assertEqual(cleaned[1]["date"], "2026-10-20")
		self.assertEqual(cleaned[1]["amount"], 500.00)
		self.assertEqual(cleaned[1]["type"], "Withdrawal")

	def test_extract_json(self):
		"""Test robust JSON extraction from pure JSON, markdown fences, and embedded text."""
		raw_json = '{"transactions": [{"date": "2026-10-01", "amount": 100}]}'
		res = MintAIClient._extract_json(raw_json)
		self.assertIn("transactions", res)

		fenced_json = '```json\n{"status": "success", "count": 1}\n```'
		res = MintAIClient._extract_json(fenced_json)
		self.assertEqual(res.get("status"), "success")

		embedded = 'Here is the parsed data:\n{"party": "DEWA", "confidence": 0.95}\nHope this helps!'
		res = MintAIClient._extract_json(embedded)
		self.assertEqual(res.get("party"), "DEWA")

	@patch("mint.apis.ai_client.requests.post")
	def test_openai_compatible_call(self, mock_post):
		"""Test OpenAI-compatible HTTP payload formulation and response handling."""
		mock_response = MagicMock()
		mock_response.status_code = 200
		mock_response.json.return_value = {
			"choices": [
				{"message": {"content": '{"transactions": [{"date": "2026-10-01", "amount": 250.0, "type": "Deposit"}]}'}}
			]
		}
		mock_post.return_value = mock_response

		settings = MintAISettings()
		settings.backend_mode = "OpenAI"
		settings.api_key = "sk-test-key"
		settings.api_endpoint = "https://api.openai.com/v1"
		settings.model_name = "gpt-4o-mini"
		settings.temperature = 0.1
		settings.max_tokens = 2000

		client = MintAIClient(settings=settings)
		res = client.complete(system_prompt="Test sys", user_prompt="Test user")

		self.assertTrue(mock_post.called)
		args, kwargs = mock_post.call_args
		self.assertIn("https://api.openai.com/v1/chat/completions", args[0])
		self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sk-test-key")
		self.assertEqual(kwargs["json"]["model"], "gpt-4o-mini")
		self.assertIn("transactions", res)

	@patch("mint.apis.ai_client.requests.post")
	def test_gemini_call(self, mock_post):
		"""Test Google Gemini REST API formulation with JSON generationConfig."""
		mock_response = MagicMock()
		mock_response.status_code = 200
		mock_response.json.return_value = {
			"candidates": [
				{
					"content": {
						"parts": [
							{"text": '{"transactions": [{"date": "2026-10-10", "amount": 80.0, "type": "Withdrawal"}]}'}
						]
					}
				}
			]
		}
		mock_post.return_value = mock_response

		settings = MintAISettings()
		settings.backend_mode = "Google Gemini"
		settings.api_key = "gemini-test-key"
		settings.api_endpoint = "https://generativelanguage.googleapis.com"
		settings.model_name = "gemini-2.0-flash"
		settings.temperature = 0.1
		settings.max_tokens = 2000

		client = MintAIClient(settings=settings)
		res = client.complete(system_prompt="Test sys", user_prompt="Test user")

		self.assertTrue(mock_post.called)
		args, kwargs = mock_post.call_args
		self.assertIn("key=gemini-test-key", args[0])
		self.assertEqual(kwargs["json"]["generationConfig"]["responseMimeType"], "application/json")
		self.assertIn("transactions", res)

	@patch("mint.apis.ai_client.requests.post")
	def test_copilot_bridge(self, mock_post):
		"""Test ERPNext Copilot bridge delegating to Copilot's AI Bot Settings."""
		mock_response = MagicMock()
		mock_response.status_code = 200
		mock_response.json.return_value = {
			"choices": [
				{"message": {"content": '{"status": "connected"}'}}
			]
		}
		mock_post.return_value = mock_response

		copilot_settings = MagicMock()
		copilot_settings.provider = "OpenAI"
		copilot_settings.api_key = "copilot-inherited-key"
		copilot_settings.api_endpoint = "https://api.openai.com/v1"
		copilot_settings.model_name = "gpt-4o-mini"
		copilot_settings.temperature = 0.1
		copilot_settings.max_tokens_per_request = 4000
		copilot_settings.get_password.return_value = "copilot-inherited-key"

		frappe.get_single.return_value = copilot_settings

		settings = MintAISettings()
		settings.backend_mode = "ERPNext Copilot"

		client = MintAIClient(settings=settings)
		res = client.complete(system_prompt="Sys", user_prompt="User")

		self.assertTrue(mock_post.called)
		args, kwargs = mock_post.call_args
		self.assertEqual(kwargs["headers"]["Authorization"], "Bearer copilot-inherited-key")
		self.assertEqual(res.get("status"), "connected")

	@patch.object(MintAIClient, "complete")
	def test_ai_matching_suggester(self, mock_complete):
		"""Test semantic party and ledger account suggester."""
		mock_complete.return_value = {
			"party_type": "Supplier",
			"party": "DEWA",
			"account": "5110 - Electricity & Water Expenses - SCL",
			"confidence": 0.95,
			"reasoning": "Matched Dubai Electricity and Water Authority utility payment"
		}

		res = suggest_party_and_account(
			description="POS 48192 DUBAI WATER ELECT AUT",
			amount=1250.0,
			transaction_type="Withdrawal",
			company="SCL"
		)

		self.assertTrue(res.get("success"))
		self.assertEqual(res.get("party"), "DEWA")
		self.assertEqual(res.get("account"), "5110 - Electricity & Water Expenses - SCL")
		self.assertGreaterEqual(res.get("confidence"), 0.9)

	def test_mint_bank_statement_import_parse_amount(self):
		"""Test MintBankStatementImport string parsing for CR/DR amounts."""
		importer = MintBankStatementImport()
		amt, tx_type = importer.parse_string_amount("1,250.00 Cr")
		self.assertEqual(amt, "1,250.00")
		self.assertEqual(tx_type, "Deposit")

		amt, tx_type = importer.parse_string_amount("450.00 Dr")
		self.assertEqual(amt, "450.00")
		self.assertEqual(tx_type, "Withdrawal")

	def test_settings_validation_defaults(self):
		"""Test MintAISettings validate sets proper defaults for each backend provider."""
		s = MintAISettings()
		s.backend_mode = "Google Gemini"
		s.model_name = ""
		s.api_endpoint = ""
		s.validate()
		self.assertEqual(s.model_name, "gemini-2.0-flash")
		self.assertEqual(s.api_endpoint, "https://generativelanguage.googleapis.com")

		s.backend_mode = "OpenAI"
		s.model_name = ""
		s.api_endpoint = ""
		s.validate()
		self.assertEqual(s.model_name, "gpt-4o-mini")
		self.assertEqual(s.api_endpoint, "https://api.openai.com/v1")

		s.backend_mode = "Custom (OpenAI-Compatible / Ollama)"
		s.model_name = ""
		s.api_endpoint = ""
		s.validate()
		self.assertEqual(s.model_name, "llama3.2")
		self.assertEqual(s.api_endpoint, "http://localhost:11434/v1")

	@patch.object(MintAIClient, "complete")
	def test_test_connection_method(self, mock_complete):
		"""Test MintAISettings.test_connection whitelist method."""
		mock_complete.return_value = {"status": "ok", "message": "Connection successful"}
		s = MintAISettings()
		s.backend_mode = "OpenAI"
		s.api_key = "valid-key"
		res = s.test_connection()
		self.assertTrue(res["success"])
		self.assertEqual(res["response"]["status"], "ok")


if __name__ == "__main__":
	unittest.main()
