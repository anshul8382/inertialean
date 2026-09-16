# SEBI Process Instructions — Regulator-Ready Processes and Reports

This document turns **SEBI guidelines and requirements** (from `docs/regulatory/` and `docs/SEBI_COMPLIANCE_CHECKLIST.md`) into **process-related instructions**. Use it so that when developing each feature, the process stays in sync with guidelines and **reports remain ready to be shared with the regulator**.

**For day-to-day development:** The **`.cursor/rules/sebi-process.mdc`** rule contains **distilled process guidelines** (record-keeping, suitability, disclosures, audit trail, regulator reports, per-feature checklist). Use that rule during feature work; **read this doc** when you need the full narrative, regulatory references, or the full checklist in one place.

**Authority:** SEBI (Investment Advisers) Regulations, 2013 (as amended); Master Circular for Investment Advisers; circulars and documents listed in **`docs/regulatory/INDEX.md`**. For any topic, prefer the wording in those documents; use this document as the project’s process translation.

---

## 1. Before Developing Any Feature — Process Gate

When developing or changing a feature that touches **clients, advice, agreements, disclosures, risk profiling, or reporting**:

1. **Read** the relevant part of this document (sections 2–7) and **`docs/SEBI_COMPLIANCE_CHECKLIST.md`** for the area you are touching.
2. **Check** **`docs/regulatory/INDEX.md`** for which circulars apply (e.g. Master Circular, Advertisement Code, timestamp-named PDFs).
3. **Ensure** the implementation supports the **process** below (records, audit trail, reports for regulator); do not only satisfy a technical checklist.
4. **After** implementation, confirm that **regulator-ready reports** (or data needed to generate them) are still available or have been added.

---

## 2. Record-Keeping Process

**SEBI requirement:** Client agreements, risk profiling, suitability assessments, recommendations, and communications must be **storable and auditable**. Records must be maintainable and producible for inspection.

**Process instructions:**

- **No silent overwrites:** Do not replace critical records in place without an audit trail. Prefer **append or version** (e.g. new row, new version, or logged change) so that “what was there before” can be reconstructed.
- **What to store:** For each client: agreements (signed), risk profile and date of update, suitability assessments, recommendations (generated, sent, executed), and key communications (e.g. recommendation emails). Store **who** (user/id) and **when** (timestamp).
- **Retention:** Define and document retention (e.g. 5 years as per regulatory expectations). Implement or schedule retention (archive/delete) so records are kept for the required period and disposal is documented.
- **When adding a feature:** If the feature creates or changes client-facing or advice-related data, add the corresponding **storage and audit trail** (see §5) and ensure it is included in **regulator reports** (see §6).

**Reference:** Master Circular for Investment Advisers; `docs/regulatory/INDEX.md`.

---

## 3. Suitability and Risk Profiling Process

**SEBI requirement:** Recommendations and allocations must be **suitable** for the client’s risk profile. Unsuitable recommendations require a **documented override and rationale**.

**Process instructions:**

- **Risk profile:** Store current risk profile per client and **date of last update**. When adding or changing risk profile, keep history or version if required by policy (or at least “last_updated”).
- **Linkage:** Where recommendations or allocations are generated, **link to the client’s risk profile** (and its date) used for that recommendation. If the system allows an override (recommendation despite mismatch), store **override reason** and **who approved**.
- **When adding a feature:** If the feature involves recommendations, model portfolios, or allocation advice, ensure it **reads** risk profile and (if applicable) **records** override and rationale. Do not recommend clearly unsuitable products without this record.

**Reference:** Master Circular; SEBI IA Regulations; `docs/regulatory/INDEX.md`.

---

## 4. Disclosures Process

**SEBI requirement:** Mandatory disclosures to clients (e.g. agreement templates, fee structures, conflicts of interest). The system must support **what was disclosed and when**.

**Process instructions:**

- **Capture:** For each disclosure type (e.g. agreement, fees, conflicts), store at least: **what** (document type or identifier), **to whom** (client/id), **when** (date/time), and optionally a copy or link (e.g. file in UPLOAD_FOLDER or reference to template version).
- **Agreements:** Client agreements: store signed copy (immutable, no overwrite), version, client_id, and date. Link to audit log (who uploaded, when).
- **When adding a feature:** If the feature involves sending or displaying disclosures to clients, add **disclosure logging** (what, whom, when) and ensure it can be included in **regulator reports** (§6).

**Reference:** Master Circular; `docs/regulatory/INDEX.md` (advertisement/disclosure-related documents).

---

## 5. Audit Trail Process

**SEBI requirement:** Advisor actions (recommendations sent, executed, reviews, access to client data) must be **traceable to a user and timestamp**. Log collection and retention as per policy.

**Process instructions:**

- **What to log:** Login (success/failure), logout, password change, client/portfolio access (view/list), client/portfolio/create/update/delete, report/download actions, recommendation send/execute, and any action that changes client or advice data. Use **AuditLog** (or equivalent) with user_id, action, resource_type, resource_id, ip, user_agent, timestamp, and optional details.
- **Access to logs:** Logs must not be writable by normal users. Restrict log access to **admins/auditors** only. Ensure an **admin-only audit log viewer** or export so that logs can be produced for the regulator.
- **When adding a feature:** If the feature performs an action that must be auditable (client data change, recommendation, download, disclosure), **add an audit log entry** for that action. Follow existing patterns in `services/audit_service.py` and routes.

**Reference:** `docs/SEBI_COMPLIANCE_CHECKLIST.md` §4; Master Circular.

---

## 6. Reports Ready for the Regulator

**SEBI requirement:** The RIA must maintain records and be able to **produce them for SEBI inspection**. Reports and data must be **ready to be shared with the regulator** when asked.

**Process instructions:**

- **Regulator-ready reports** (or ability to generate them) must cover at least:
  - **Access and auth:** Login attempts (success/failure), IP/device, user; password changes.
  - **Client and advice:** List of clients; per client: agreements, risk profile and date, recommendations (generated/sent/executed) with dates and user; suitability overrides with rationale.
  - **Disclosures:** What was disclosed, to whom, and when.
  - **Audit trail:** Activity log (who did what, when, on which resource) for the retention period.
  - **Incidents:** If applicable, incident log and breach reporting (see §7).
- **Format:** Prefer **exportable** data (e.g. CSV, PDF, or structured export) from the same data that the app uses (AuditLog, Client, Recommendation, agreements, disclosures). Do not rely on “screenshots only”; ensure **data can be extracted** for submission.
- **When adding a feature:** Ask: “If the regulator asked for evidence in this area, can we produce it from stored data and audit log?” If not, add the **storage and/or log** and a way to **include it in regulator reports** (admin export or report script).

**Reference:** Master Circular; SEBI inspection expectations; `docs/SEBI_COMPLIANCE_CHECKLIST.md` §§4, 11.

---

## 7. Incident and Reporting Process

**SEBI requirement:** Cyber incidents and breaches must be **investigated and reported** as per SEBI/CERT-In requirements.

**Process instructions:**

- **Document:** Maintain an **incident response** process (detection, containment, investigation, communication) and **reporting obligations** (when and how to report to SEBI/CERT-In). Keep a **template for breach reporting** and contact points.
- **When adding a feature:** If the feature handles security, auth, or client data, ensure it does not weaken incident detection or reporting (e.g. security-relevant events should be logged and reviewable).

**Reference:** `docs/SEBI_COMPLIANCE_CHECKLIST.md` §§8, 10; CERT-In; SEBI cyber resilience.

---

## 8. Advertisement and Marketing (If Applicable)

**SEBI requirement:** IA advertisement and marketing must comply with the **SEBI IA Advertisement Code of Conduct** and related BASL documents.

**Process instructions:**

- **Before** adding or changing any **advertisement or marketing** feature (e.g. campaigns, client-facing promotional content), refer to **`docs/regulatory/INDEX.md`** and use: **SEBI - IA ADVERTISEMTN CODE OF CODUCT.pdf**, **BASL - IA_Code_of_Advt.pdf**, **BASL_Checklist_of_Advertisement_Approval.xlsx**.
- Ensure content and process align with those documents; keep **approval/checklist** records if required.

**Reference:** `docs/regulatory/INDEX.md` (advertisement documents).

---

## 9. Per-Feature Checklist (Run When Developing a Feature)

Use this when implementing or changing a feature:

- [ ] **Records:** Does this feature create or change client/advice/agreement/disclosure data? If yes, is it stored in an **auditable way** (no silent overwrites; who/when)?
- [ ] **Suitability:** Does it involve recommendations or allocations? If yes, is **risk profile** (and date) used and **override rationale** recorded if applicable?
- [ ] **Disclosures:** Does it involve disclosures to clients? If yes, is **what, to whom, when** captured?
- [ ] **Audit trail:** Are **all critical actions** of this feature **logged** (user, action, resource, timestamp)?
- [ ] **Regulator reports:** Can we **produce evidence** for the regulator from the data and logs we have (or added)? If not, what storage or export do we add?
- [ ] **Circulars:** Have we checked **`docs/regulatory/INDEX.md`** and the relevant circulars for this area (Master Circular, Advertisement Code, etc.)?

---

## 10. Where to Find the Underlying Documents

- **Project regulatory index:** **`docs/regulatory/INDEX.md`** — list of SEBI/IA circulars and documents in this project.
- **Developer checklist:** **`docs/SEBI_COMPLIANCE_CHECKLIST.md`** — technical checklist (auth, audit, API, backup, RIA, accessibility).
- **SEBI agent rules:** **`.cursor/rules/sebi-compliance.mdc`** and **`.cursor/rules/sebi-process.mdc`** — ensure every feature is developed in sync with these process instructions and guidelines.

When in doubt, align with the **Master Circular for Investment Advisers** and the latest SEBI/IA circulars listed in **`docs/regulatory/INDEX.md`**.
