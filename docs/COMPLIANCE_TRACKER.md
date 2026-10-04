# Compliance Tracker

Working register of applicable Investment Adviser (IA) compliances and status as of **2026-09-30**.

**Module name (planned):** Compliance Tracker  
**Audience:** Internal ops / compliance (Anshul)  
**Purpose:** Review each obligation, track status, and plan app support (evidence, reminders, filings).

---

## Status legend

| Status | Meaning |
|--------|---------|
| Complied | Obligation applies and is met |
| Not Applicable | Obligation does not apply given current IA structure / activity |
| Not Complied | Obligation applies but not met (or met late / incomplete) |

---

## Summary (as of today)

| Status | Count |
|--------|------:|
| Complied | 39 |
| Not Applicable | 26 |
| Not Complied | 2 |
| **Total** | **67** |

**Open non-compliance (action needed):**

- **#46** MC cl 5 & Circular 2020/221 — SaaS advisory compliance, half-yearly  
- **#65** MC cl 31.1 — Half-yearly SaaS undertaking  

Both: March 2025 SaaS submission not made; September 2025 filed after the due date.

**Priority build themes from review (2026-09-30):**

| Theme | Rows |
|-------|------|
| Corporate transition / ceiling | #1, #11 (alert at **225 clients**), #9, #34 |
| Certification calendar | #5 XC expiry **19 Mar 2029** (remind Dec 2028; renew by Feb 2029) |
| Risk profile / suitability / disclosures | #19–#23 |
| Advisory register | #25 |
| Agreement / invoice gate + MITC template | #29, #30, #32 |
| Monthly complaints / charter | #39 |
| AI disclosure if used in advisory | #43 |
| SaaS Mar/Sep filing | #46, #65 |
| Ads approval | #48, #49 |
| Reading backlog | #50–#57, #65 (MC 31.1) |

---

## Compliance register

For each row: keep **Implementation plan** (what the app / process should do) and **Notes** (evidence, owners, dates) updated as we review.

---

### 1. Reg 2(s) — Principal officer of a non-individual IA

| Field | Value |
|-------|-------|
| Regulation | Reg 2(s) |
| Particulars | Principal officer of a non-individual IA |
| Compliance Status | Not Applicable |
| Reason | IA is registered in individual capacity |

**Implementation plan**

- Keep dormant while IA remains individual.  
- When moving to **corporate / non-individual** setup: designate Principal Officer, update register status to Applicable, capture appointment evidence and SEBI filings.  
- App: flag this row as “deferred — corporate transition checklist”.

**Notes**

- Review 2026-09-30: not required now; **needed for corporate set-up**.  
- Related ceiling / transition: see #11 (plan transition past ~225 clients).

---

### 2. MC 1(ix) — Partnership firm → Principal Officer / LLP by 30 Sep 2025

| Field | Value |
|-------|-------|
| Regulation | MC 1(ix) |
| Particulars | Partnership firm: designation of Principal Officer, move to LLP/body corporate by 30 Sep 2025 |
| Compliance Status | Not Applicable |
| Reason | IA is registered in individual capacity |

**Implementation plan**

- Out of scope for tracker / app work.

**Notes**

- Review 2026-09-30: **Ignore**.

---

### 3. Reg 3 — Certificate of registration

| Field | Value |
|-------|-------|
| Regulation | Reg 3 |
| Particulars | Certificate of registration required to act as IA |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- Treat registration as **perpetual** (ongoing obligation to hold valid certificate).  
- Store registration number / certificate copy on Compliance Tracker; no renewal calendar unless SEBI changes rules.

**Notes**

- Review 2026-09-30: **perpetual**.

---

### 4. Reg 6 — Application and eligibility

| Field | Value |
|-------|-------|
| Regulation | Reg 6 |
| Particulars | Consideration of application and eligibility criteria |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- Out of scope for tracker / app work.

**Notes**

- Review 2026-09-30: **Ignore**.

---

### 5. Reg 7 & MC 1(v) — Qualification and certification

| Field | Value |
|-------|-------|
| Regulation | Reg 7 & MC 1(v) |
| Particulars | Qualification and certification requirements |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- Track **XC (NISM / certification) validity: 19 Mar 2029**.  
- Reminder **3 months in advance** (target notify: **19 Dec 2028**).  
- Ensure **new certification obtained ≥ 1 month before expiry** (target complete by **19 Feb 2029**).  
- App: compliance calendar event + email/ops task to Anshul.

**Notes**

- Review 2026-09-30: XC valid until **19 March 2029**.  
- Evidence: store current certificate PDF / credential id when available.

---

### 6. SEBI Circular 2020/182, cl 2(iv) — Age 50+ exemption

| Field | Value |
|-------|-------|
| Regulation | SEBI Circular 2020/182, cl 2(iv) |
| Particulars | Exemption for existing individual IAs above 50 years |
| Compliance Status | Not Applicable |
| Reason | IA is not 50 years of age |

**Implementation plan**

- 

**Notes**

- 

---

### 7. Reg 8 & MC 1(iv) — Deposit based on client count

| Field | Value |
|-------|-------|
| Regulation | Reg 8 & MC 1(iv) |
| Particulars | Deposit based on client count |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 8. Reg 13(b) — Inform SEBI of false/misleading info or material change

| Field | Value |
|-------|-------|
| Regulation | Reg 13(b) |
| Particulars | Inform SEBI of false/misleading info or material change |
| Compliance Status | Not Applicable |
| Reason | No material-change / false-info event currently tracked as applicable for day-to-day ops (review: grouped with #9 as NA for now) |

**Implementation plan**

- No active app work while marked NA.  
- Revisit if material change (address, control, capacity, etc.) occurs — flip to Applicable and log SEBI intimation.

**Notes**

- Review 2026-09-30: **#8 & #9 NA**.

---

### 9. Reg 13(c) — Non-individual IA name includes "investment adviser"

| Field | Value |
|-------|-------|
| Regulation | Reg 13(c) |
| Particulars | Non-individual IA to include "investment adviser" in name |
| Compliance Status | Not Applicable |
| Reason | IA is registered in individual capacity |

**Implementation plan**

- Activate on corporate transition (with #1).

**Notes**

- Review 2026-09-30: **#8 & #9 NA**.

---

### 10. Reg 13(d) — Individual IA uses "investment adviser" in correspondence

| Field | Value |
|-------|-------|
| Regulation | Reg 13(d) |
| Particulars | Individual IA to use "investment adviser" in all client correspondence |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- Ensure all outbound client email templates use the approved signature that includes “Investment Adviser”.  
- Spot-check WhatsApp / letterheads if used.

**Notes**

- Review 2026-09-30: **included in email signature**.

---

### 11. Reg 13(e–g) & MC 1(vi) — Transition above 300 clients or ₹3 Cr fees

| Field | Value |
|-------|-------|
| Regulation | Reg 13(e–g) & MC 1(vi) |
| Particulars | Transition to non-individual IA above 300 clients or ₹3 Cr fees |
| Compliance Status | Not Applicable |
| Reason | IA has not reached the ceiling limit |

**Implementation plan**

- **Early-warning threshold at 225 clients** (not wait for 300).  
- App: dashboard / alert when active client count ≥ 225 (and optional fee AUA tracker toward ₹3 Cr).  
- At trigger: open corporate transition plan (#1 Principal Officer, entity registration, name, compliance officer #34).  
- Revisit status from NA → planning → Applicable as we approach ceiling.

**Notes**

- Review 2026-09-30: **keep track; plan transition as we move past 225 clients**.  
- Links: #1, #9, #34.

---

### 12. Reg 13(h) — Part-time IA limited to 75 clients

| Field | Value |
|-------|-------|
| Regulation | Reg 13(h) |
| Particulars | Part-time IA limited to 75 clients |
| Compliance Status | Not Applicable |
| Reason | IA is registered in full-time capacity |

**Implementation plan**

- None (full-time IA).

**Notes**

- Review 2026-09-30: **not applicable**.

---

### 13. MC 1(viii) — Part-time IA regulatory compliance

| Field | Value |
|-------|-------|
| Regulation | MC 1(viii) |
| Particulars | Part-time IA regulatory compliance |
| Compliance Status | Not Applicable |
| Reason | IA is registered in full-time capacity |

**Implementation plan**

- None (full-time IA).

**Notes**

- Review 2026-09-30: **not applicable**.

---

### 14. Reg 15(7) — No own-account trades contrary to advice within 15 days

| Field | Value |
|-------|-------|
| Regulation | Reg 15(7) |
| Particulars | No own-account trades contrary to advice within 15 days |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 15. Reg 15 (other than 7) — General responsibilities

| Field | Value |
|-------|-------|
| Regulation | Reg 15 (other than 7) |
| Particulars | General responsibilities |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 16. Reg 15A & MC 1(iii) — Fees only in AUA or fixed fee mode

| Field | Value |
|-------|-------|
| Regulation | Reg 15A & MC 1(iii) |
| Particulars | Fees charged only in AUA mode or fixed fee mode |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 17. MC 2.1 — No free trials

| Field | Value |
|-------|-------|
| Regulation | MC 2.1 |
| Particulars | No free trials |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- Keep product / onboarding flows free of “trial” offerings.  
- Periodic review of marketing copy and agreement templates.

**Notes**

- Review 2026-09-30: **we do not offer free trials**.

---

### 18. MC 2.1 — No part payments

| Field | Value |
|-------|-------|
| Regulation | MC 2.1 |
| Particulars | No part payments |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- Billing / invoice UX must not allow instalment or partial fee schedules contrary to MC.  
- Policy: full fee only per permitted AUA / fixed modes (#16).

**Notes**

- Review 2026-09-30: **we do not take part payments**.

---

### 19. Reg 16 — Risk profiling

| Field | Value |
|-------|-------|
| Regulation | Reg 16 |
| Particulars | Risk profiling |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- Track each client’s risk-profile **completion / assessment date**.  
- **Flag when profile is more than 1 year old** (ops task / client health / Compliance Tracker alert).  
- Drive refresh workflow before advice / review meetings.

**Notes**

- Review 2026-09-30: track and flag when risk profile is **> 1 year old**.

---

### 20. SEBI MC 2024/50, cl 2(2.2) — Client consent on completed risk profile

| Field | Value |
|-------|-------|
| Regulation | SEBI MC 2024/50, cl 2(2.2) |
| Particulars | Client consent on completed risk profile |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- When risk profile is filled / assessed: **automatically send** the client a communication of their assessed profile (and capture consent / acknowledgement).  
- Store send date + client response against the client record.

**Notes**

- Review 2026-09-30: once risk profile is filled, **must send information to client sharing assessed profile**.

---

### 21. Reg 17 — Suitability of advice

| Field | Value |
|-------|-------|
| Regulation | Reg 17 |
| Particulars | Suitability of advice |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- Maintain a **suitability record per client**, updated when recommendations are shared.  
- Link suitability artefact to recommendation / review workflow (Google Doc / Drive suitability already exists — keep in sync).  
- Flag clients with recommendations but missing / stale suitability.

**Notes**

- Review 2026-09-30: **create suitability for each client; keep updated as per recommendations shared**.

---

### 22. Circular 2020/182 cl 2(viii) & Circular 2025/003 cl 1.2(viii)(b)–(c) — Non-individual clients

| Field | Value |
|-------|-------|
| Regulation | Circular 2020/182 cl 2(viii) & Circular 2025/003 cl 1.2(viii)(b)–(c) |
| Particulars | Risk profiling and suitability for non-individual clients |
| Compliance Status | Not Applicable |
| Reason | IA has no non-individual clients |

**Implementation plan**

- Onboarding gate: if client type = **non-individual**, **flag this obligation** (status → Applicable) and require enhanced risk profiling / suitability pack before advice.  
- Block or warn until checklist complete.

**Notes**

- Review 2026-09-30: **flag when we onboard a non-individual client**.

---

### 23. Reg 18 — Disclosure to clients

| Field | Value |
|-------|-------|
| Regulation | Reg 18 |
| Particulars | Disclosure to clients |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- Package disclosures as **yearly communication**, preferably **bundled with risk-profile refresh / assessed-profile mail (#20)**.  
- Template + send log + year stamp per client.

**Notes**

- Review 2026-09-30: need a way to share disclosures — **yearly communication with risk profile**.

---

### 24. Reg 19 — Maintenance of records

| Field | Value |
|-------|-------|
| Regulation | Reg 19 |
| Particulars | Maintenance of records |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 25. MC 1(xiii A) — Records of advice-related interactions (incl. prospects)

| Field | Value |
|-------|-------|
| Regulation | MC 1(xiii A) |
| Particulars | Records of advice-related interactions, including with prospects |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- Maintain **Advisory Register** (SEBI advisory register already in app).  
- Evaluate whether **Recommendation** table (+ register export) fully covers advice-related interactions including prospects; fill gaps (prospect-only advice, verbal notes).  
- Retention aligned with #27 (5 years / disputes).

**Notes**

- Review 2026-09-30: need advisory register — **recommendation table can work for this** (confirm coverage vs SEBI Advisory Register module).

---

### 26. MC 1(xiii B) — Call recording of implementation/execution consent

| Field | Value |
|-------|-------|
| Regulation | MC 1(xiii B) |
| Particulars | Call recording of implementation/execution consent |
| Compliance Status | Not Applicable |
| Reason | IA does not provide execution or implementation of advice |

**Implementation plan**

- 

**Notes**

- 

---

### 27. MC 1(xiii C) — Records retention 5 years / until disputes resolved

| Field | Value |
|-------|-------|
| Regulation | MC 1(xiii C) |
| Particulars | Records kept for 5 years, or until disputes are resolved |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 28. MC 1(xiv) & 31.2 — Annual audit, ATR, segregation certificate, disclosure

| Field | Value |
|-------|-------|
| Regulation | MC 1(xiv) & 31.2 |
| Particulars | Annual audit, ATR, client-level segregation certificate, disclosure |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 29. Circular 2020/182, cl 2(ii) — Signed agreement before advice or fee

| Field | Value |
|-------|-------|
| Regulation | Circular 2020/182, cl 2(ii) |
| Particulars | Signed agreement before any advice or fee |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- **Block invoice creation** until agreement signing is confirmed (signed / completed status).  
- Same gate should cover fee collection and advice send where feasible.  
- Related: #32 (no advice/fee until signed + copy shared).

**Notes**

- Review 2026-09-30: **block invoice creation before confirmation of signing of agreement**.

---

### 30. MC 1(ii)(a–d) — Agreement includes MITC; e-sign permitted

| Field | Value |
|-------|-------|
| Regulation | MC 1(ii)(a–d) |
| Particulars | Agreement includes MITC; e-sign permitted |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- **Update agreement copy / template** to the latest MITC-aligned template.  
- Version stamp templates; migrate new agreements to latest; backlog older signed packs as historical.

**Notes**

- Review 2026-09-30: **need to update agreement copy with latest template**.

---

### 31. MC 1(ii)(e–f) — CeFCoM guidance; additional terms

| Field | Value |
|-------|-------|
| Regulation | MC 1(ii)(e–f) |
| Particulars | CeFCoM guidance; additional terms that don't dilute the regulations |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 32. MC 1(ii)(g) — No advice/fee until agreement signed and copy shared

| Field | Value |
|-------|-------|
| Regulation | MC 1(ii)(g) |
| Particulars | No advice or fee until agreement is signed and a copy is shared |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 33. Reg 19A & MC 1(xvi) — Functional website with prescribed details

| Field | Value |
|-------|-------|
| Regulation | Reg 19A & MC 1(xvi) |
| Particulars | Functional website with prescribed details |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 34. Reg 20 & MC 1(x) — Appointment of compliance officer

| Field | Value |
|-------|-------|
| Regulation | Reg 20 & MC 1(x) |
| Particulars | Appointment of compliance officer |
| Compliance Status | Not Applicable |
| Reason | IA is registered in individual capacity |

**Implementation plan**

- 

**Notes**

- 

---

### 35. Reg 21 & MC cl 6 — Grievance redressal / SCORES

| Field | Value |
|-------|-------|
| Regulation | Reg 21 & MC cl 6 |
| Particulars | Grievance redressal / SCORES |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 36. Reg 22 & Circular 2020/182 cl 1(i) — Client-level segregation

| Field | Value |
|-------|-------|
| Regulation | Reg 22 & Circular 2020/182 cl 1(i) |
| Particulars | Client-level segregation of advisory and distribution |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 37. Reg 22A — Implementation of advice or execution

| Field | Value |
|-------|-------|
| Regulation | Reg 22A |
| Particulars | Implementation of advice or execution |
| Compliance Status | Not Applicable |
| Reason | IA does not provide execution or implementation of advice |

**Implementation plan**

- 

**Notes**

- 

---

### 38. MC 1(xvii) — Display of details on website and communications

| Field | Value |
|-------|-------|
| Regulation | MC 1(xvii) |
| Particulars | Display of details on website and communications |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 39. Circular 2025/80 & MC cl 7 — Investor charter and monthly complaints disclosure

| Field | Value |
|-------|-------|
| Regulation | Circular 2025/80 & MC cl 7 |
| Particulars | Investor charter and monthly complaints disclosure |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- Create a **recurring monthly compliance entry** (calendar / Compliance Tracker task) for investor charter + complaints disclosure.  
- Checklist: website Annexure update (#45), SCORES / complaint stats, publish date.  
- Reminder before month-end.

**Notes**

- Review 2026-09-30: **create entry for monthly compliance**.

---

### 40. TRAI guidelines — Curbing spam SMS / header misuse

| Field | Value |
|-------|-------|
| Regulation | TRAI guidelines (SEBI letter 16.03.2023, BASL 20230329-1, BSE circulars) |
| Particulars | Curbing spam SMS and header misuse |
| Compliance Status | Not Applicable |
| Reason | IA does not use SMS as a service |

**Implementation plan**

- 

**Notes**

- 

---

### 41. Circular 2023/52, BASL 20230411-1 & MC 10.2 — Brand / trade name

| Field | Value |
|-------|-------|
| Regulation | Circular 2023/52, BASL 20230411-1 & MC 10.2 |
| Particulars | Usage of brand name/trade name |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 42. SEBI / BSE Inspections — Last inspection and observations

| Field | Value |
|-------|-------|
| Regulation | SEBI / BSE Inspections |
| Particulars | Last inspection and compliance with observations |
| Compliance Status | Not Applicable |
| Reason | No inspections conducted |

**Implementation plan**

- 

**Notes**

- 

---

### 43. MC 1(xii) — Use of AI tools (Reg 15(14), 18(9))

| Field | Value |
|-------|-------|
| Regulation | MC 1(xii) |
| Particulars | Use of AI tools (Reg 15(14), 18(9)) |
| Compliance Status | Not Applicable |
| Reason | IA has not used AI tools in IA services |

**Implementation plan**

- If AI is used in **advisory** services: flip to Applicable and **disclose** to clients (Reg 15(14), 18(9)).  
- Gate: product features that generate client-facing advice via AI must attach disclosure text / consent.  
- Internal ops AI (non-advisory) may remain out of scope — document the boundary.

**Notes**

- Review 2026-09-30: **if we use AI in advisory we need to disclose that**.

---

### 44. MC 2.3 — Fees through banking channels only

| Field | Value |
|-------|-------|
| Regulation | MC 2.3 |
| Particulars | Fees received through banking channels only |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 45. MC 2.4 — Complaint status on homepage (Annexure C)

| Field | Value |
|-------|-------|
| Regulation | MC 2.4 |
| Particulars | Complaint status shown on homepage in Annexure C format |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 46. MC cl 5 & Circular 2020/221 — SaaS advisory compliance (half-yearly) — OPEN

| Field | Value |
|-------|-------|
| Regulation | MC cl 5 & Circular 2020/221 |
| Particulars | SaaS advisory compliance, half-yearly |
| Compliance Status | **Not Complied** |
| Reason | March 2025 SaaS submission not made; September 2025 filed after the due date |

**Implementation plan**

- **File SaaS declaration every March and September** (on-time).  
- Calendar reminders: T−30 / T−7 / due day.  
- Evidence store: submission receipt / acknowledgement; status → Complied only after on-time filing.  
- Pair with #65 (undertaking).

**Notes**

- Priority review item for Compliance Tracker module.  
- Review 2026-09-30: ensure Mar & Sep filing discipline.  

---

### 47. MC cl 8 — Prior SEBI approval for change in control

| Field | Value |
|-------|-------|
| Regulation | MC cl 8 |
| Particulars | Prior SEBI approval for change in control |
| Compliance Status | Not Applicable |
| Reason | No such changes |

**Implementation plan**

- 

**Notes**

- 

---

### 48. Circular 2023/51 VI(9) & MC 10(1) — Advertisement code

| Field | Value |
|-------|-------|
| Regulation | Circular 2023/51 VI(9) & MC 10(1) |
| Particulars | Advertisement code |
| Compliance Status | Not Applicable |
| Reason | No advertisements during the year |

**Implementation plan**

- Before any public post / advertisement: **prior approval + compliance check** (#49 exchange approval where required).  
- Log: content, channel, approver, date.  
- If advertising starts, flip status to Applicable / Complied with evidence.

**Notes**

- Review 2026-09-30: **make sure all posts and advertisements are approved and compliant**.

---

### 49. MC 10(1)(d)(i) — Advertisements need prior exchange approval

| Field | Value |
|-------|-------|
| Regulation | MC 10(1)(d)(i) |
| Particulars | Advertisements need prior exchange approval |
| Compliance Status | Not Applicable |
| Reason | No advertisements during the year |

**Implementation plan**

- Same gate as #48 — no publish without exchange approval when rules require it.

**Notes**

- Review 2026-09-30: tied to #48 approval flow.

---

### 50. MC cl 11 — MF transactions through stock exchange infrastructure

| Field | Value |
|-------|-------|
| Regulation | MC cl 11 |
| Particulars | MF transactions through stock exchange infrastructure |
| Compliance Status | Not Applicable |
| Reason | IA does not facilitate MF transactions |

**Implementation plan**

- Keep NA while we do not facilitate MF.  
- If facilitation starts: flip Applicable and implement exchange-infra path.

**Notes**

- Review 2026-09-30: confirmed NA.  
- **Read / deepen understanding** of MC cl 11–19 and MC 31.1 as a pack (see reading list below).

---

### 51. MC cl 12 — Unauthenticated news

| Field | Value |
|-------|-------|
| Regulation | MC cl 12 |
| Particulars | Unauthenticated news |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- Policy: no circulation of unauthenticated market news to clients.  
- **Pending proper read** of clause text for checklist items.

**Notes**

- Review 2026-09-30: Complied; **need to read properly** (with #50–57 & #65).

---

### 52. MC cl 13 — Outsourcing guidelines

| Field | Value |
|-------|-------|
| Regulation | MC cl 13 |
| Particulars | Outsourcing guidelines |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- Inventory outsourced vendors; ensure contracts / oversight match MC cl 13.  
- **Pending proper read**.

**Notes**

- Review 2026-09-30: Complied; **need to read properly**.

---

### 53. MC cl 14 — Regulatory sandbox

| Field | Value |
|-------|-------|
| Regulation | MC cl 14 |
| Particulars | Regulatory sandbox |
| Compliance Status | Not Applicable |
| Reason | IA has not used the regulatory sandbox |

**Implementation plan**

- None unless sandbox participation begins.

**Notes**

- Review 2026-09-30: NA; include in MC reading pack.

---

### 54. MC cl 16 — Conflicts of interest

| Field | Value |
|-------|-------|
| Regulation | MC cl 16 |
| Particulars | Conflicts of interest |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- Maintain COI policy + disclosure path (#23 yearly pack).  
- **Pending proper read**.

**Notes**

- Review 2026-09-30: Complied; **need to read properly**.

---

### 55. MC cl 17 — Securities market data access and usage terms

| Field | Value |
|-------|-------|
| Regulation | MC cl 17 |
| Particulars | Securities market data access and usage terms |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- Ensure data vendor / feed terms match usage in app (prices, charts).  
- **Pending proper read**.

**Notes**

- Review 2026-09-30: Complied; **need to read properly**.

---

### 56. MC cl 18 — AML / CFT under PMLA

| Field | Value |
|-------|-------|
| Regulation | MC cl 18 |
| Particulars | AML / CFT under PMLA |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- KYC / AML process evidence; periodic refresher.  
- **Pending proper read**.

**Notes**

- Review 2026-09-30: Complied; **need to read properly**.

---

### 57. MC cl 19 — Sharing real-time price data with third parties

| Field | Value |
|-------|-------|
| Regulation | MC cl 19 |
| Particulars | Sharing real-time price data with third parties |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- Audit any third-party sharing of live prices (APIs, partners).  
- **Pending proper read** with MC 31.1.

**Notes**

- Review 2026-09-30: Complied; **#57 and MC 31.1 need to be read properly**.

---

### 58. MC cl 20 — KYC norms

| Field | Value |
|-------|-------|
| Regulation | MC cl 20 |
| Particulars | KYC norms |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 59. MC cl 22 — Association with certain persons

| Field | Value |
|-------|-------|
| Regulation | MC cl 22 |
| Particulars | Association with certain persons |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 60. MC cl 23 — Accredited investors

| Field | Value |
|-------|-------|
| Regulation | MC cl 23 |
| Particulars | Accredited investors |
| Compliance Status | Not Applicable |
| Reason | No accredited investor clients |

**Implementation plan**

- 

**Notes**

- 

---

### 61. MC cl 25 — Certified past performance communication

| Field | Value |
|-------|-------|
| Regulation | MC cl 25 |
| Particulars | Certified past performance communication |
| Compliance Status | Not Applicable |
| Reason | IA has not communicated past performance |

**Implementation plan**

- 

**Notes**

- 

---

### 62. MC cl 28 — Standardised, validated UPI IDs

| Field | Value |
|-------|-------|
| Regulation | MC cl 28 |
| Particulars | Standardised, validated UPI IDs |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 63. MC cl 29 — Rights of Persons with Disabilities Act

| Field | Value |
|-------|-------|
| Regulation | MC cl 29 |
| Particulars | Rights of Persons with Disabilities Act |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 64. MC cl 30 — Periodic reporting to IAASB (Mar 2025, Sep 2025)

| Field | Value |
|-------|-------|
| Regulation | MC cl 30 |
| Particulars | Periodic reporting to IAASB (Mar 2025, Sep 2025) |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 65. MC cl 31.1 — Half-yearly SaaS undertaking — OPEN

| Field | Value |
|-------|-------|
| Regulation | MC cl 31.1 |
| Particulars | Half-yearly SaaS undertaking |
| Compliance Status | **Not Complied** |
| Reason | March 2025 SaaS submission not made; September 2025 filed after the due date |

**Implementation plan**

- Same calendar / evidence flow as #46 — **file Mar & Sep**.  
- Track due date, filed date, on-time vs late.  
- **Read MC 31.1 properly** together with #57 (and #50–56 pack) before locking checklist fields.

**Notes**

- Priority; pair with #46.  
- Review 2026-09-30: SaaS half-yearly + **read MC 31.1 properly**.

---

### 66. MC cl VIII — Annexures to the master circular

| Field | Value |
|-------|-------|
| Regulation | MC cl VIII |
| Particulars | Annexures to the master circular |
| Compliance Status | Complied |
| Reason | NA |

**Implementation plan**

- 

**Notes**

- 

---

### 67. MC cl 27 & Circular 2025/60 — CSCRF (cybersecurity)

| Field | Value |
|-------|-------|
| Regulation | MC cl 27 & Circular 2025/60 |
| Particulars | Cybersecurity and Cyber Resilience Framework (CSCRF) |
| Compliance Status | Not Applicable |
| Reason | IA is exempted under the circular |

**Implementation plan**

- 

**Notes**

- 

---

## Module implementation backlog (app)

High-level placeholders for when we build **Compliance Tracker** in-app:

1. Seed these 67 rows as a register (status + reason editable; history of status changes).  
2. Filters: Complied / Not Applicable / Not Complied; free-text search on regulation.  
3. Attach evidence (PDF / link / filing date) per row.  
4. Due-date reminders for recurring items:  
   - SaaS half-yearly #46/#65 (Mar & Sep)  
   - XC certification #5 (19 Mar 2029; −3 months / −1 month)  
   - Monthly complaints / charter #39  
   - Risk profile age > 365 days #19  
   - Client count ≥ 225 → corporate transition #11  
5. Operational gates: block invoice without signed agreement (#29); send assessed risk profile (#20); annual disclosures with RP (#23); AI disclosure if advisory AI (#43); ad approval log (#48/#49).  
6. Suitability artefact per client linked to recommendations (#21); advisory register via recommendations / SEBI register (#25).  
7. Snapshot “as of” date for reviews / SEBI inspection packs.  
8. Reading session: MC cl 11–19 + MC 31.1 — then refine checklist fields.

---

## Change log

| Date | Change |
|------|--------|
| 2026-09-30 | Initial register from status pack (67 items); open NCs: #46, #65 |
| 2026-09-30 | Review pass: implementation plans/notes for #1–5, #8–13, #17–23, #25, #29–30, #39, #43, #46, #48–57, #65; #8 → NA per review; #2/#4 ignore |
