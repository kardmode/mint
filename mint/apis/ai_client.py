# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt

import json
import re
import frappe
from frappe import _
import requests


class MintAIClient:
	"""
	Provider-agnostic AI client for Mint.
	Supports:
	1. ERPNext Copilot Bridge (delegates credentials and model config to erpnext_ai_bots)
	2. Google Gemini REST API (v1beta/models/{model}:generateContent) with Multimodal support
	3. OpenAI-Compatible REST API (/v1/chat/completions - OpenAI, Ollama, DeepSeek, Groq)
	"""

	def __init__(self, settings=None):
		if settings:
			self.settings = settings
		else:
			try:
				self.settings = frappe.get_single("Mint AI Settings")
			except Exception:
				self.settings = None

	def complete(self, system_prompt: str, user_prompt: str, inline_data: dict = None, json_schema: dict = None) -> dict:
		"""
		Execute an AI completion with structured JSON output.

		:param system_prompt: Guidance for role and schema expectations
		:param user_prompt: Document text, extracted rows, or prompt instructions
		:param inline_data: Optional dict with {"mime_type": str, "data": base64_str} for multimodal inputs
		:param json_schema: Optional JSON schema dict for strict adherence
		:return: Parsed Python dictionary
		"""
		mode = getattr(self.settings, "backend_mode", "ERPNext Copilot") if self.settings else "ERPNext Copilot"

		if mode == "ERPNext Copilot":
			return self._call_copilot(system_prompt, user_prompt, inline_data=inline_data)
		elif mode == "Google Gemini":
			return self._call_gemini(
				api_key=self._get_api_key(),
				endpoint=getattr(self.settings, "api_endpoint", "https://generativelanguage.googleapis.com"),
				model=getattr(self.settings, "model_name", "gemini-2.0-flash") or "gemini-2.0-flash",
				temperature=float(getattr(self.settings, "temperature", 0.1) or 0.1),
				max_tokens=int(getattr(self.settings, "max_tokens", 4000) or 4000),
				system_prompt=system_prompt,
				user_prompt=user_prompt,
				inline_data=inline_data
			)
		else:  # OpenAI or Custom (Ollama / Local)
			return self._call_openai_compatible(
				api_key=self._get_api_key(),
				endpoint=getattr(self.settings, "api_endpoint", "https://api.openai.com/v1") or "https://api.openai.com/v1",
				model=getattr(self.settings, "model_name", "gpt-4o-mini") or "gpt-4o-mini",
				temperature=float(getattr(self.settings, "temperature", 0.1) or 0.1),
				max_tokens=int(getattr(self.settings, "max_tokens", 4000) or 4000),
				system_prompt=system_prompt,
				user_prompt=user_prompt,
				inline_data=inline_data
			)

	def _get_api_key(self) -> str:
		if not self.settings:
			return ""
		if hasattr(self.settings, "get_password"):
			try:
				key = self.settings.get_password("api_key")
				if key:
					return key
			except Exception:
				pass
		return getattr(self.settings, "api_key", "") or ""

	def _call_copilot(self, system_prompt: str, user_prompt: str, inline_data: dict = None) -> dict:
		"""
		Bridge to ERPNext Copilot.
		Retrieves Copilot's configured AI Bot Settings credentials and invokes the active model.
		"""
		copilot_settings = None
		try:
			copilot_settings = frappe.get_single("AI Bot Settings")
		except Exception:
			pass

		if not copilot_settings:
			raise frappe.ValidationError(
				_("ERPNext Copilot is selected in Mint AI Settings, but AI Bot Settings was not found. "
				  "Please ensure 'erpnext_ai_bots' is installed or configure a Direct Provider in Mint AI Settings.")
			)

		provider = getattr(copilot_settings, "provider", "OpenAI")
		api_key = ""
		if hasattr(copilot_settings, "get_password"):
			try:
				api_key = copilot_settings.get_password("api_key")
			except Exception:
				api_key = getattr(copilot_settings, "api_key", "")
		else:
			api_key = getattr(copilot_settings, "api_key", "")

		endpoint = getattr(copilot_settings, "api_endpoint", "")
		model = getattr(copilot_settings, "model_name", "")
		temperature = float(getattr(copilot_settings, "temperature", 0.1) or 0.1)
		max_tokens = int(getattr(copilot_settings, "max_tokens_per_request", 4000) or 4000)

		if provider == "Google Gemini":
			if not endpoint:
				endpoint = "https://generativelanguage.googleapis.com"
			if not model:
				model = "gemini-2.0-flash"
			return self._call_gemini(
				api_key=api_key,
				endpoint=endpoint,
				model=model,
				temperature=temperature,
				max_tokens=max_tokens,
				system_prompt=system_prompt,
				user_prompt=user_prompt,
				inline_data=inline_data,
			)
		else:
			if not endpoint:
				endpoint = "https://api.openai.com/v1"
			if not model:
				model = "gpt-4o-mini"
			return self._call_openai_compatible(
				api_key=api_key,
				endpoint=endpoint,
				model=model,
				temperature=temperature,
				max_tokens=max_tokens,
				system_prompt=system_prompt,
				user_prompt=user_prompt,
				inline_data=inline_data,
			)

	def _call_gemini(
		self,
		api_key: str,
		endpoint: str,
		model: str,
		temperature: float,
		max_tokens: int,
		system_prompt: str,
		user_prompt: str,
		inline_data: dict = None
	) -> dict:
		"""Call Google Gemini REST API with JSON response enforcement."""
		if not api_key:
			raise frappe.ValidationError(_("API Key is required for Google Gemini. Configure it in Mint AI Settings."))

		clean_endpoint = endpoint.rstrip("/")
		url = f"{clean_endpoint}/v1beta/models/{model}:generateContent?key={api_key}"

		parts = []
		if inline_data:
			parts.append({
				"inline_data": {
					"mime_type": inline_data.get("mime_type", "application/pdf"),
					"data": inline_data.get("data", "")
				}
			})
		if user_prompt:
			parts.append({"text": user_prompt})

		payload = {
			"contents": [
				{
					"role": "user",
					"parts": parts
				}
			],
			"systemInstruction": {
				"parts": [{"text": system_prompt}]
			},
			"generationConfig": {
				"temperature": temperature,
				"maxOutputTokens": max_tokens,
				"responseMimeType": "application/json"
			}
		}

		headers = {"Content-Type": "application/json"}
		try:
			resp = requests.post(url, json=payload, headers=headers, timeout=60)
		except Exception as e:
			raise frappe.ValidationError(_("Failed to connect to Google Gemini API: {0}").format(str(e)))

		if resp.status_code != 200:
			raise frappe.ValidationError(
				_("Google Gemini API returned error ({0}): {1}").format(resp.status_code, resp.text)
			)

		data = resp.json()
		try:
			candidate = data["candidates"][0]
			text_content = candidate["content"]["parts"][0]["text"]
			return self._extract_json(text_content)
		except (KeyError, IndexError) as e:
			raise frappe.ValidationError(_("Invalid response structure from Gemini API: {0}").format(resp.text))

	def _call_openai_compatible(
		self,
		api_key: str,
		endpoint: str,
		model: str,
		temperature: float,
		max_tokens: int,
		system_prompt: str,
		user_prompt: str,
		inline_data: dict = None
	) -> dict:
		"""Call OpenAI-compatible completions API (OpenAI, Ollama, DeepSeek, Groq)."""
		clean_endpoint = endpoint.rstrip("/")
		if not clean_endpoint.endswith("/v1") and not clean_endpoint.endswith("/chat/completions"):
			url = f"{clean_endpoint}/v1/chat/completions"
		elif clean_endpoint.endswith("/v1"):
			url = f"{clean_endpoint}/chat/completions"
		else:
			url = clean_endpoint

		headers = {
			"Content-Type": "application/json",
		}
		if api_key:
			headers["Authorization"] = f"Bearer {api_key}"

		user_content = user_prompt
		# If multimodal inline_data is present for OpenAI GPT-4o vision
		if inline_data and inline_data.get("mime_type", "").startswith("image/"):
			user_content = [
				{"type": "text", "text": user_prompt},
				{
					"type": "image_url",
					"image_url": {
						"url": f"data:{inline_data['mime_type']};base64,{inline_data['data']}"
					}
				}
			]

		payload = {
			"model": model,
			"messages": [
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": user_content}
			],
			"temperature": temperature,
			"max_tokens": max_tokens,
			"response_format": {"type": "json_object"}
		}

		try:
			resp = requests.post(url, json=payload, headers=headers, timeout=60)
		except Exception as e:
			raise frappe.ValidationError(_("Failed to connect to AI API endpoint {0}: {1}").format(url, str(e)))

		if resp.status_code != 200:
			# If provider doesn't support response_format: {"type": "json_object"}, retry without it
			if "response_format" in resp.text:
				payload.pop("response_format", None)
				try:
					resp = requests.post(url, json=payload, headers=headers, timeout=60)
				except Exception as e:
					raise frappe.ValidationError(_("Retry failed: {0}").format(str(e)))

		if resp.status_code != 200:
			raise frappe.ValidationError(
				_("AI API returned error ({0}): {1}").format(resp.status_code, resp.text)
			)

		data = resp.json()
		try:
			content = data["choices"][0]["message"]["content"]
			return self._extract_json(content)
		except (KeyError, IndexError) as e:
			raise frappe.ValidationError(_("Invalid response structure from AI API: {0}").format(resp.text))

	@staticmethod
	def _extract_json(text: str) -> dict:
		"""Safely extract and parse JSON object/array from LLM string output."""
		text = text.strip()
		# Strip markdown fences if present
		if text.startswith("```"):
			text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
			text = re.sub(r"\s*```$", "", text)
			text = text.strip()

		# Direct parse
		try:
			return json.loads(text)
		except Exception:
			pass

		# Find matching outermost braces
		match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
		if match:
			try:
				return json.loads(match.group(1))
			except Exception:
				pass

		raise frappe.ValidationError(_("Failed to parse JSON response from AI: {0}").format(text[:300]))
