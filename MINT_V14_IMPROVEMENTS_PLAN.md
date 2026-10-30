# Mint v14 Integration & Enhancement Roadmap

> **Target Environment:** Frappe v14 / ERPNext v14  
> **Repository:** `mint` (Branch: `version-14`)  
> **Objective:** Backport key enterprise improvements from ERPNext v16's `/banking` module and layer targeted AI capabilities (PDF parsing, semantic party matching) directly into the v14 Mint app.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             MINT APP (v14)                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│  Frontend (React 19 / Vite / Tailwind)                                      │
│  - Desk Theme Sync (Dark/Light)       - RTL Language Support (Arabic)       │
│  - Split Entry & Payment Modals       - Undo Log & Match Visual Cues        │
├─────────────────────────────────────────────────────────────────────────────┤
│  AI Enhancement Layer (Optional / Modular)                                  │
│  - PDF Statement Parser (Gemini API)  - Semantic Narration Classifier       │
│  - Intelligent Rule Synthesizer                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│  Backend Services & Controllers (mint.apis.*)                               │
│  - Multi-Currency Handler             - Batch Auto-Reconciler (Queue-based) │
│  - Cost Center & Company Fencing      - MT940 / CSV / XLSX Importer         │
├─────────────────────────────────────────────────────────────────────────────┤
│  ERPNext v14 Core (Untouched)                                               │
│  - Bank Transaction                   - Payment Entry / Journal Entry       │
│  - General Ledger                     - Bank Clearance & Reports            │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Phase 1: Core Accounting Upgrades (Backported from v16)

### 1.1 Multi-Currency Support
* **Source in v16:** `erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.set_multi_currency_amounts`
* **Target in Mint:** `mint/mint/apis/bank_reconciliation.py`
* **Details:**
  * Currently, Mint hardcodes `target_exchange_rate = 1.0` in `create_internal_transfer` and `create_payment_entry_and_reconcile`.
  * **Functions to Implement:**
    ```python
    def set_multi_currency_amounts(pe):
        """Set exchange rates when bank account and party/offset account currencies differ."""
        company_currency = frappe.get_cached_value("Company", pe.company, "default_currency")
        pe.paid_from_account_currency = frappe.get_cached_value("Account", pe.paid_from, "account_currency")
        pe.paid_to_account_currency = frappe.get_cached_value("Account", pe.paid_to, "account_currency")

        from erpnext.setup.utils import get_exchange_rate
        pe.source_exchange_rate = (
            1.0 if pe.paid_from_account_currency == company_currency
            else get_exchange_rate(pe.paid_from_account_currency, company_currency, pe.posting_date)
        )
        pe.target_exchange_rate = (
            1.0 if pe.paid_to_account_currency == company_currency
            else get_exchange_rate(pe.paid_to_account_currency, company_currency, pe.posting_date)
        )

        if pe.payment_type == "Receive" and pe.source_exchange_rate:
            base_amount = flt(pe.received_amount) * pe.target_exchange_rate
        else:
            base_amount = flt(pe.paid_amount) * pe.source_exchange_rate

        pe.base_paid_amount = base_amount
        pe.base_received_amount = base_amount
    ```
  * Integrates seamlessly with ERPNext v14's native `PaymentEntry.set_exchange_gain_loss()`.

---

### 1.2 Enterprise Permission & Cost Center Fencing
* **Source in v16:** `validate_voucher_permissions`, `get_writable_linked_vouchers`
* **Target in Mint:** `mint/mint/apis/bank_reconciliation.py`
* **Details:**
  * Prevent users from reconciling transactions against vouchers belonging to companies or cost centers they do not have permissions for.
  * **Functions to Implement:**
    ```python
    def validate_cost_center_fence(bank_transaction, voucher_type, voucher_name):
        user = frappe.session.user
        if "System Manager" in frappe.get_roles(user):
            return
        
        # Enforce User Permissions on Company & Cost Center
        user_companies = frappe.get_user_permissions(user).get("Company", [])
        if user_companies and bank_transaction.company not in [d.doc for d in user_companies]:
            frappe.throw(_("Permission denied for Company {0}").format(bank_transaction.company), frappe.PermissionError)
    ```

---

### 1.3 User Authentication & Desk Portal Isolation
* **Source in v16:** `erpnext/www/banking.py` (`validate_desk_user`)
* **Target in Mint:** `mint/mint/www/mint.py`
* **Details:**
  * Add immediate validation to prevent portal/website users or unauthenticated guest users from rendering `/mint`:
    ```python
    from frappe.utils.user import is_website_user
    from urllib.parse import urlencode

    def validate_desk_user():
        if frappe.session.user == "Guest":
            frappe.response["status_code"] = 403
            frappe.redirect(f"/login?{urlencode({'redirect-to': '/mint'})}")
        elif is_website_user():
            frappe.throw(_("You are not permitted to access Bank Reconciliation."), frappe.PermissionError)
    ```

---

## Phase 2: High-Volume Automation (Batch Auto-Reconcile)

* **Source in v16:** `auto_reconcile_vouchers`, `start_auto_reconcile`
* **Target in Mint:** `mint/mint/apis/auto_reconciliation.py`

### 2.1 Queue-Based Auto-Reconciler
* When importing bank statements with hundreds or thousands of rows:
  * For lists with more than 10 transactions, batch them into chunks of 500 or 1,000 and enqueue them into background RQ workers (`queue="long"`).
  * Automatically matches rows where:
    1. Amount matches exactly (deposit/withdrawal).
    2. Date is within the configured window (e.g. ± 3 days).
    3. Reference number matches (or fuzzy match score >= 90%).
  * Triggers realtime progress via `frappe.publish_realtime("mint_auto_reconcile_progress")`.

---

## Phase 3: AI Extensions Layer (Targeted AI Upgrades)

Instead of fragile black-box agents, introduce modular AI utilities that feed structured, clean data directly into Mint's deterministic accounting tables.

### 3.1 Gemini-Powered PDF Bank Statement Parser
* **Module:** `mint/mint/apis/ai_parser.py`
* **Problem Solved:** Bank statements in PDF format frequently have varying multi-column layouts, wrapped descriptions, or scanned pages that break traditional regex and CSV parsers.
* **Architecture:**
  1. User uploads a PDF in the Mint Statement Importer.
  2. A lightweight background job sends the PDF bytes to Gemini with a strict JSON schema:
     ```json
     {
       "type": "object",
       "properties": {
         "statement_period": { "type": "string" },
         "closing_balance": { "type": "number" },
         "transactions": {
           "type": "array",
           "items": {
             "type": "object",
             "properties": {
               "date": { "type": "string" },
               "description": { "type": "string" },
               "reference": { "type": "string" },
               "withdrawal": { "type": "number" },
               "deposit": { "type": "number" }
             },
             "required": ["date", "description"]
           }
         }
       }
     }
     ```
  3. Inserts extracted rows into standard `Bank Transaction` records with status `Unreconciled`.

---

### 3.2 Semantic Party & Account Suggester
* **Module:** `mint/mint/apis/ai_matcher.py`
* **Problem Solved:** Cryptic bank descriptions (e.g. `POS PURCHASE 89201 AMZN CLOUD`) fail standard substring matching unless exact rules are written.
* **Implementation:**
  * Whitelisted API: `mint.apis.ai_matcher.suggest_party_match(description, company)`
  * Matches against existing Suppliers / Customers using token embeddings or fuzzy LLM prompt:
    > "Given narration 'DEBIT - AWS EMEA 92831', match to the best party in: [Amazon Web Services, Apple Inc, Adobe Systems]."
  * Surfaces a confidence score badge in the Mint UI alongside deterministic matches.

---

### 3.3 Rule Auto-Synthesizer
* **Module:** `mint/mint/apis/ai_rules.py`
* **Details:**
  * Analyzes the last 500 reconciled `Bank Transaction Payments` records in the database.
  * Identifies repetitive narrations linked to specific accounts (e.g., monthly bank maintenance charges, recurring software subscriptions).
  * Prompts the user: *"We detected 12 transactions from 'Twilio' booked to 'Software Expenses'. Click to create rule."*
  * Automatically creates a draft `Mint Bank Transaction Rule`.

---

## Phase 4: UI/UX & Build Optimizations

### 4.1 Theme & Localization Sync
* **Target:** `mint/frontend/src/App.tsx` and `mint/frontend/src/index.html`
* **Changes:**
  * Add `<ThemeProvider>` matching `window.frappe.boot.desk_theme` (Dark, Light, System).
  * Set `dir="rtl"` dynamically when `window.frappe.boot.lang` is Arabic or Hebrew.

### 4.2 Production Asset Pipeline
* **Target:** `mint/frontend/package.json`
* **Cross-Platform Build Script:**
  Replace Unix-only `cp` commands with cross-platform node scripts:
  ```json
  "scripts": {
    "dev": "vite",
    "build": "vite build --base=/assets/mint/mint/ && node -e \"require('fs').copyFileSync('../mint/public/mint/index.html', '../mint/www/mint.html')\""
  }
  ```

---

## Deliverables & File Checklist

| Component | File Path | Phase |
| :--- | :--- | :--- |
| **Multi-Currency** | `mint/mint/apis/bank_reconciliation.py` | Phase 1 |
| **Permission Fencing** | `mint/mint/apis/bank_reconciliation.py` | Phase 1 |
| **Desk User Validation** | `mint/mint/www/mint.py` | Phase 1 |
| **Batch Auto-Reconcile** | `mint/mint/apis/auto_reconcile.py` (New) | Phase 2 |
| **PDF AI Parser** | `mint/mint/apis/ai_parser.py` (New) | Phase 3 |
| **Semantic Matcher** | `mint/mint/apis/ai_matcher.py` (New) | Phase 3 |
| **Theme / RTL Sync** | `mint/frontend/src/App.tsx` | Phase 4 |
| **Cross-Platform Build** | `mint/frontend/package.json` | Phase 4 |
