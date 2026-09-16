# Session + codebase change log (recoverable sources)

**Purpose:** Combine what we can reconstruct from **Cursor chat transcripts** and the **current Git working tree** (not from `git log` alone).  
**Generated:** 2026-03-20 (server time when this file was written).

---

## Limitations (read this first)

| Source | What it captures | What it misses |
|--------|------------------|----------------|
| **Agent transcript** | User prompts and assistant replies in that Cursor chat thread | Tool/file diffs inside the transcript, other chats, edits done outside Cursor, terminal-only work |
| **`git status` vs `HEAD`** | All **uncommitted** file changes on disk right now | Exact line-by-line “who changed what when” without commit history; committed work on other branches |

This is a **practical audit aid**, not a legal/compliance-grade change record.

---

## A. From chat history (Cursor agent transcript)

**Primary transcript used for this summary:**  
`agent-transcripts/c23ce2f0-2d89-4a76-814e-debe08854dd2/` (parent chat covering Enhanced Tax Optimiser + follow-on incidents).

Other transcripts exist under `.cursor/projects/.../agent-transcripts/` if you need to mine additional threads.

### A.1 Requirements & product direction (user messages, condensed)

- Delivered a **large specification** for an India **FY-aware capital gains / tax optimisation engine** (multi-FY, rates from config, strategy catalogue, interactive review UI, persistence).
- Clarified scope: **ignore STT**; **scan all clients** with optional single-client recalc; default **30% slab**; **no historical carry-forward** yet; **asset class mapping** from system inventory; **one email per client** after approval; **future tax saved** OK to show; **grandfathering / REIT distributions** deferred or ignored for v1.
- Asked to **reuse** existing tax optimiser / capital gains code and APIs; use a **greedy** ST/LT cost-benefit style optimisation where appropriate.
- Requested **Tools menu** entry for **Tax Optimiser (Enhanced)** after the legacy tax optimiser.
- Raised gap: feature felt like **phase 1**; wanted **full interactive** flow (approve / reject with **alternates**), aligned with `docs/ENHANCED_TAX_OPTIMISER_REQUIREMENTS.md`.
- Operational asks: **run migration**, **restart app**, **smoke test** including email to `anshul@equities4wealth.com`.
- Email: align sending with **unified recommendations** / same SMTP path as working mail.
- **Incidents after deploy:** login **500** (`base.html` missing), post-login **500** (`two_factor` blueprint / `url_for` **BuildError**), dashboard **500** (navbar `main.*` endpoints missing on stub `routes/main.py`), **500 after 2FA**, app **down** / **restart** requests, concern that restored UI looked like a **very old version**.

### A.2 Assistant-side outcomes (as reflected in that thread + codebase)

*(High level — see repo files for ground truth.)*

- **Enhanced Tax Optimiser:** services (`fy_tax_utils`, `enhanced_tax_optimiser_service`, `tax_optimiser_interactive_service`), API (`api/v1/enhanced_tax_optimiser.py`), UI template (`templates/tools/tax_optimiser_enhanced.html`), route blueprint (`routes/tax_optimiser_enhanced.py`), email template (`templates/email/tax_optimiser_client.html`), migration (`migrations/add_tax_optimiser_interactive_tables.py`), models in `models/__init__.py`, docs (`docs/ENHANCED_TAX_OPTIMISER_REQUIREMENTS.md`), Cursor rule (`.cursor/rules/...`).
- **App bootstrap fixes** (when imports failed): compatibility `main.py`, `api/v1/__init__.py`, `models.py` restoration from git, `__init__.py` SQLAlchemy URI property handling, **`two_factor_bp` registration**, **`routes/main.py` stub routes** for navbar endpoints, **direct SMTP** for session email (aligned with `EmailService` pattern).
- **Templates / nav:** `base.html` / `base_clean.html` adjustments; backup copies e.g. `templates/base.html.before_app_test_*`.

---

## B. From codebase (Git working tree vs `HEAD`)

**Recorded against commit:** whatever `HEAD` is on the machine when this file was generated (branch often `backup-2026-01-26-complete`, sometimes **ahead** of remote).

### B.1 Scale (approximate)

| Category | Count |
|----------|------:|
| Modified vs `HEAD` | 94 |
| Untracked | 370 |
| Deleted (tracked) | 4 |

### B.2 Untracked breakdown (top-level)

| Prefix | Files (approx.) | Note |
|--------|----------------:|------|
| `flask_sessions/` | 230 | Runtime session data — usually **not** “feature changes” |
| `templates/` | 36 | New/changed UI |
| `migrations/` | 22 | DB migrations |
| `scripts/` | 20 | One-off / ops scripts |
| `services/` | 12 | Business logic |
| `docs/` | 12 | Documentation |
| `routes/` | 8 | HTTP blueprints |
| `api/` | 7 | REST APIs |
| `.cursor/`, `.cursorrules`, `AGENTS.md` | — | Editor/agent config |
| Other | … | See `git status` |

### B.3 Tracked files deleted vs `HEAD`

- `templates/reviews/enhanced_generate.html`
- `templates/reviews/enhanced_generate_final.html`
- `templates/reviews/enhanced_review_new.html`
- `templates/reviews/enhanced_view.html`  
  *(Legacy “enhanced review” templates; one copy may exist under `_deprecated/` as untracked.)*

### B.4 Modified areas (sample — not exhaustive)

Includes heavy touch on: `__init__.py`, `routes/main.py`, `routes/auth.py`, `routes/unified_recommendations.py`, `templates/base.html`, `templates/base_clean.html`, `templates/dashboard.html`, `services/email_service.py`, `models/__init__.py`, `api/v1/__init__.py`, Airflow DAGs, agents, WhatsApp, review templates, etc. Run:

```bash
git diff --name-only HEAD
git diff --stat HEAD
```

---

## C. How to refresh this document

1. **Transcript:** Open the relevant `.jsonl` under  
   `~/.cursor/projects/home-inertia-app/agent-transcripts/<uuid>/`  
   and search for `<user_query>`.

2. **Codebase snapshot:**

```bash
cd /home/inertia/app
git branch --show-current
git status -sb
git diff --stat HEAD
git ls-files --others --exclude-standard | head
```

3. **Optional:** Export full lists to files:

```bash
git diff --name-only HEAD > /tmp/modified.txt
git ls-files --others --exclude-standard > /tmp/untracked.txt
```

---

## D. Related docs in repo

- `docs/ENHANCED_TAX_OPTIMISER_REQUIREMENTS.md` — feature acceptance / scope reference  
- `AGENTS.md`, `.cursorrules` — agent/onboarding context  

---

*If you want a single “Jan 2026 → date” narrative tied to **commits**, use `git log --since=2026-01-01` on the branch you consider production truth; that is separate from this file.*
