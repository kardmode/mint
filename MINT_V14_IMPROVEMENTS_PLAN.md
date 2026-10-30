# Mint v14 Integration & Enhancement Roadmap

> **Target Environment:** Frappe v14 / ERPNext v14  
> **Repository:** `mint` (Branch: `version-14`)  
> **Objective:** Backport key enterprise improvements from ERPNext v16's `/banking` module, optimize UI/UX and builds, and layer a provider-agnostic AI engine (compatible with ERPNext Copilot and multi-LLMs) directly into Mint v14.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             MINT APP (v14)                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│  Frontend (React 19 / Vite / Tailwind)                                      │
│  - Desk Theme Sync (Dark/Light)       - RTL Language Support (Arabic)       │
│  - Auto-Reconcile Action Button       - Split Entry & Payment Modals        │
│  - Undo Log & Match Visual Cues       - AI Confidence & Match Badges        │
├─────────────────────────────────────────────────────────────────────────────┤
│  Phase 3: Unified AI Adapter Layer (`MintAIClient`)                         │
│  - Mode 1: ERPNext Copilot Bridge (`erpnext-copilot`) [Shared Credentials]  │
│  - Mode 2: Direct Providers (OpenAI, Gemini, Claude, DeepSeek, Ollama)      │
│  - Capabilities: PDF Extractor, Semantic Party Suggester, Copilot Toolset   │
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

## Status of Phases

- [x] **Phase 1: Core Accounting Upgrades** (Multi-currency, permission fencing, desk authentication) — *COMPLETED*
- [x] **Phase 2: High-Volume Automation** (Background auto-reconcile engine & frontend trigger) — *COMPLETED*
- [x] **Phase 4: UI/UX & Build Optimizations** (Theme synchronization, dynamic RTL/LTR, cross-platform build) — *COMPLETED*
- [ ] **Phase 3: Provider-Agnostic AI Layer & ERPNext Copilot Bridge** — *PLANNED*

---

## Phase 3 Specification: Provider-Agnostic AI & ERPNext Copilot Bridge

### 1. New DocType: `Mint AI Settings` (Single DocType)
To avoid hardcoding one vendor, a dedicated settings DocType provides two operating modes:

1. **Mode A: ERPNext Copilot Bridge**:
   - If `erpnext-copilot` (`erpnext_ai_bots`) is installed, Mint hooks directly into Copilot's configured LLM provider and credentials.
   - Zero API key management inside Mint.
2. **Mode B: Direct Bring-Your-Own-Key (BYOK)**:
   - Configure OpenAI, Anthropic Claude, Google Gemini, DeepSeek, or a local Ollama instance (`http://localhost:11434/v1`).
   - Uses an OpenAI-compatible REST adapter (`/chat/completions`) using Python's standard `requests`, requiring no external heavy SDK packages.

### 2. Core Capabilities
1. **Multi-format Statement Extractor**: Extracts tabular statement rows from PDFs or images with a strict JSON output schema.
2. **Semantic Party & Account Suggester**: Maps ambiguous bank narrations (e.g., `POS 4919 AWS LUX`) to clean ERPNext Suppliers/Customers and Expense accounts.
3. **ERPNext Copilot Tool Integration**: Registers Mint reconciliation functions as callable tools for Copilot chat agents, enabling conversational reconciliation.
