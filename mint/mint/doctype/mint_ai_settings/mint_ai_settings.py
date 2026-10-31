# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class MintAISettings(Document):
	def validate(self):
		if self.backend_mode == "ERPNext Copilot":
			self.update_copilot_status()
		elif self.backend_mode == "Google Gemini":
			if not self.model_name:
				self.model_name = "gemini-2.0-flash"
			if not self.api_endpoint:
				self.api_endpoint = "https://generativelanguage.googleapis.com"
		elif self.backend_mode == "OpenAI":
			if not self.model_name:
				self.model_name = "gpt-4o-mini"
			if not self.api_endpoint:
				self.api_endpoint = "https://api.openai.com/v1"
		elif self.backend_mode.startswith("Custom"):
			if not self.api_endpoint:
				self.api_endpoint = "http://localhost:11434/v1"
			if not self.model_name:
				self.model_name = "llama3.2"

	def update_copilot_status(self):
		"""Check if ERPNext Copilot (erpnext_ai_bots) is installed and available."""
		installed = False
		try:
			installed_apps = frappe.get_installed_apps()
			installed = "erpnext_ai_bots" in installed_apps
		except Exception:
			# If called outside bench context
			try:
				import erpnext_ai_bots
				installed = True
			except ImportError:
				installed = False

		if installed:
			self.copilot_status = "Connected (erpnext_ai_bots active)"
		else:
			self.copilot_status = "Not Found (erpnext_ai_bots not installed in this bench site)"

	@frappe.whitelist()
	def test_connection(self):
		"""Test connection to the configured AI backend."""
		from mint.apis.ai_client import MintAIClient

		client = MintAIClient(settings=self)
		test_prompt = "Reply with exactly: {\"status\": \"ok\", \"message\": \"Connection successful\"}"
		system_prompt = "You are a test AI responder. Always return valid JSON."
		try:
			res = client.complete(system_prompt=system_prompt, user_prompt=test_prompt)
			return {
				"success": True,
				"message": _("AI Connection Successful!"),
				"response": res,
			}
		except Exception as e:
			frappe.log_error(f"Mint AI Connection Test Failed: {str(e)}", "Mint AI Settings")
			return {
				"success": False,
				"message": str(e),
			}
