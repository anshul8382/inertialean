# SEBI Compliance Checklist — Client Portal (RIA)

This document maps **SEBI requirements** for identity, data protection, access control, audit, API security, and related areas to **developer tasks**. Use it to make the portal compliant before launch and for audits.

**Reference:** SEBI IA Regulations and circulars; see `docs/regulatory/INDEX.md` and `.cursor/rules/sebi-regulatory.mdc`.

---

## 1. Client Authentication & Login Security

**SEBI:** Strong identity and access controls.

| Requirement | Developer task | Status |
|-------------|----------------|--------|
| **Secure login** | Username + password (email as login) | ✅ Done — `routes/auth.py`, email + password |
| **OTP / MFA** | Two-factor authentication (TOTP or SMS OTP) | ❌ Not done — add 2FA flow (e.g. pyotp + optional SMS) |
| **Password hashing** | bcrypt or Argon2 | ✅ Done — bcrypt in `models/__init__.py` (User.set_password / check_password) |
| **Password strength** | Minimum complexity policy (length, mix of chars) | ⚠️ Partial — LoginForm has Length(min=8); add on set_password/change-password when implemented |
| **Password reset** | Reset with OTP or email verification | ❌ Not done — add forgot-password + secure token/OTP flow |
| **Session security** | Auto logout after inactivity | ⚠️ Partial — `PERMANENT_SESSION_LIFETIME = 2h` in config; consider shorter + activity-based refresh |
| **Session security** | Prevent multiple simultaneous sessions | ❌ Not done — optional: one session per user or device/session table |
| **Session security** | Device / IP tracking | ✅ Done — IP and User-Agent logged in `AuditLog` at login (auth + audit_service) |
| **Cookie security** | HttpOnly, SameSite | ✅ Done — `SESSION_COOKIE_HTTPONLY`, `SESSION_COOKIE_SAMESITE = Lax` in config |

**Developer actions:**

- [ ] Implement 2FA: TOTP (e.g. pyotp) and/or SMS OTP; store secret per user; challenge after password.
- [ ] Add password policy: min length (e.g. 12), upper/lower/digit/special; enforce on set/change password.
- [ ] Add “Forgot password” with time-limited token or OTP sent to registered email; no password in email.
- [ ] Consider session invalidation on password change and optional “single session per user” or session list in profile.
- [ ] Log or store IP and User-Agent at login (for audit log).

---

## 2. Client Data Protection

**SEBI:** Encryption and protection of financial data.

| Requirement | Developer task | Status |
|-------------|----------------|--------|
| **HTTPS** | TLS everywhere in production | ⚠️ Deploy — enforce HTTPS at reverse proxy / load balancer; no HTTP for app |
| **TLS** | TLS 1.2 or higher | ⚠️ Deploy — configure web server / load balancer (e.g. nginx, AWS ALB) |
| **DB encryption** | Encryption at rest for DB | ⚠️ Infra — use provider feature (e.g. RDS encryption, MySQL TDE) or full-disk encryption |
| **SESSION_COOKIE_SECURE** | Secure cookie in production | ✅ Done — `ProductionConfig.SESSION_COOKIE_SECURE = True` in config.py |
| **Sensitive data** | Encrypt client PII/sensitive fields at rest if required | ❌ Not done — consider field-level encryption for highest-sensitivity fields |
| **Files/reports** | Encrypt stored files and reports | ❌ Not done — encrypt `UPLOAD_FOLDER` and generated reports (e.g. key in env) |

**Developer actions:**

- [x] Ensure production is served only over HTTPS; set `SESSION_COOKIE_SECURE = True` in production (done in ProductionConfig).
- [ ] Document TLS 1.2+ and DB encryption for hosting (see §8).
- [ ] Optionally: encrypt sensitive columns (e.g. specific PII) and encrypt files in `uploads/` and report outputs.

---

## 3. Access Control (Very Important)

**SEBI:** Least-privilege; only correct user sees their data.

| Requirement | Developer task | Status |
|-------------|----------------|--------|
| **Role-based access** | Client / Advisor / Admin (and Manager) | ✅ Done — roles in `models/__init__.py`; `require_advisor_or_manager`, `require_manager`, `client_access_required` in `access_control.py` |
| **Client** | Only their own portfolio/data | ⚠️ Partial — no “Client” role login yet; advisor sees only assigned clients |
| **Advisor** | All assigned clients only | ✅ Done — `can_access_client`, `get_accessible_clients`, filters in routes and API |
| **Admin** | System control | ✅ Done — `is_admin`, admin routes protected |
| **Enforce everywhere** | All routes and APIs check role/client | ✅ Done — APIs use `login_required` + `can_access_client`; web routes use decorators |

**Developer actions:**

- [ ] If “Client” portal (end-investor login) is required: add Client role and strict scoping so each user sees only their own portfolio/data.
- [ ] Ensure every new route/API that touches client data uses `client_access_required` or equivalent and never trusts `client_id` from body alone (resolve from URL/context).

---

## 4. Audit Logs (Mandatory)

**SEBI:** Log collection and retention; every material activity logged.

| Requirement | Developer task | Status |
|-------------|----------------|--------|
| **Login attempts** | Log successful logins | ✅ Done — `log_audit("login_success", ...)` in routes/auth.py |
| **Failed logins** | Log failed attempts (e.g. email, IP, time) | ✅ Done — `log_audit("login_failure", details=email, ip, user_agent)` in auth |
| **Password change** | Log when user changes password | ⚠️ Pending — add when password change flow is implemented |
| **Portfolio access** | Log who accessed which client/portfolio | ✅ Done — client_view, client_list in routes/clients.py + services/audit_service.py |
| **Data downloads** | Log report/download actions | ❌ Not done — add on CSV/PDF/report download endpoints when present |
| **Changes to client data** | Log create/update/delete of client data | ✅ Done — client_create, client_update in routes/clients.py |
| **Retention** | Retain logs per policy | ❌ Not done — define retention (e.g. 5 years) and implement (e.g. archive/delete policy) |

**Developer actions:**

- [x] Add `AuditLog` (or similar) model: user_id, action, resource_type, resource_id, ip, user_agent, timestamp, optional details (JSON). See `models/__init__.py` (AuditLog) and `services/audit_service.py`. Ensure the `audit_log` table exists (run `db.create_all()` in app context or add a migration).
- [x] Log: login success/failure, logout, client/portfolio view, client create/update; add password change and report download when those flows exist.
- [ ] Ensure logs are not writable by normal users; restrict log access to admins/auditors (admin-only audit log viewer).
- [ ] Document retention period and implement or schedule retention (e.g. archive to cold storage after N years).

---

## 5. API Security (Mobile / Integrations)

**SEBI:** API authentication, rate limiting, token-based access.

| Requirement | Developer task | Status |
|-------------|----------------|--------|
| **API authentication** | All sensitive endpoints require auth | ✅ Done — `@login_required` on API v1 clients/alerts/tasks |
| **Token-based access** | For mobile/integrations: authenticate with Bearer token | ✅ Done — JWT issued on `POST /api/v1/auth/login`; all `/api/v1/*` accept `Authorization: Bearer <access_token>` (see `services/jwt_service.py`, `main.py` request_loader, `docs/ARCHITECTURE_MODULES_AND_ALIGNMENT.md` §5) |
| **Rate limiting** | Limit requests per IP/user | ✅ Done — Flask-Limiter: 10/min on login, default 200/day and 60/hour on app (extensions.py, routes/auth.py, config) |
| **API firewall** | Block suspicious patterns | ⚠️ Infra — WAF at proxy/cloud (e.g. AWS WAF, Cloudflare) |

**Developer actions:**

- [x] Introduce API tokens: JWT issued at login; accept `Authorization: Bearer <token>`; request_loader validates token and loads user so `@login_required` works with session or token. Config: `JWT_SECRET_KEY`, `JWT_ACCESS_EXPIRES_HOURS` (see config.py, .env.example).
- [x] Add rate limiting: per-IP on login (10/min) and default limits on all routes (60/hour, 200/day).
- [ ] Document WAF/API firewall as part of hosting (see §8).

---

## 6. Vulnerability Testing (Very Important)

**SEBI:** VAPT by CERT-In empanelled auditor before launch.

| Requirement | Developer task | Status |
|-------------|----------------|--------|
| **VAPT** | Vulnerability Assessment & Penetration Testing | ❌ Not done — schedule with CERT-In empanelled auditor |
| **Pre-VAPT hardening** | Fix known issues (OWASP Top 10, dependency scan) | ⚠️ Partial — CSRF, auth, RBAC in place; add audit, 2FA, rate limit; run `pip audit` / Snyk |

**Developer actions:**

- [ ] Run dependency scanner (`pip audit`, `safety`, or Snyk) and fix critical/high.
- [ ] Harden app per this checklist (auth, audit, API, encryption) before VAPT.
- [ ] Engage CERT-In empanelled auditor for VAPT and remediate findings before go-live.

---

## 7. Secure Development Process

**SEBI:** SSDLC; code review; security testing; separate environments.

| Requirement | Developer task | Status |
|-------------|----------------|--------|
| **Code review** | All changes reviewed | Process — use PRs and review policy |
| **Security testing** | Automated + manual security tests | ⚠️ Partial — auth/client access tests exist; add audit/security test cases |
| **Separate dev/prod** | No production credentials in dev | Process — env-based config; separate DBs and secrets |

**Developer actions:**

- [ ] Enforce code review for all production changes.
- [ ] Add tests for access control, audit logging, and (when added) 2FA and password policy.
- [ ] Document separation: dev/staging/production; production uses separate DB, secrets, and TLS.

---

## 8. Hosting Requirements

**SEBI:** Secure cloud; Indian data storage; firewall; DDoS; backups.

| Requirement | Developer task | Status |
|-------------|----------------|--------|
| **Provider** | AWS / Azure / GCP or certified providers | Deploy — choose region with India data residency if required |
| **Data storage** | Indian data storage where required | Deploy — select region (e.g. ap-south-1) and document |
| **Firewall** | Restrict access to app/DB | Deploy — security groups, no public DB; WAF optional |
| **DDoS protection** | Mitigation at network/CDN | Deploy — cloud DDoS protection (e.g. Shield, Cloudflare) |
| **Backups** | Daily backups; backup encryption | See §9 |

**Developer actions:**

- [ ] Document hosting: provider, region (India if required), firewall rules, DDoS, TLS, DB encryption.
- [ ] Ensure DB is not publicly reachable; secrets in env/vault.

---

## 9. Backup & Disaster Recovery

**SEBI:** Recovery and restoration plans for cyber incidents.

| Requirement | Developer task | Status |
|-------------|----------------|--------|
| **Daily backups** | Automated daily DB and critical files | ❌ Not done — implement or use managed DB backups + file backup |
| **Backup encryption** | Encrypt backups | ❌ Not done — use encrypted backups (e.g. RDS encrypted snapshot) |
| **Disaster recovery plan** | Document and test RTO/RPO | ❌ Not done — document restore procedure and test periodically |

**Developer actions:**

- [ ] Enable automated daily backups (DB + uploads/config if needed); retain per policy.
- [ ] Use encrypted backups; document key management.
- [ ] Write DR runbook (restore DB, app, DNS) and test restore at least once.

---

## 10. Cyber Incident Reporting

**SEBI:** Incident response and reporting.

| Requirement | Developer task | Status |
|-------------|----------------|--------|
| **Incident response** | Process to detect, contain, investigate | Process — define owner and playbook |
| **Reporting** | Report to SEBI/authority as required | Process — document when and how to report |

**Developer actions:**

- [ ] Document incident response: detection, containment, investigation, communication.
- [ ] Document reporting obligations (SEBI/CERT-In etc.) and contacts; keep template for breach reporting.

---

## 11. Additional RIA Compliance (Important for RIAs)

**SEBI:** Client agreement, risk profiling, reports, disclosures, activity trail.

| Requirement | Developer task | Status |
|-------------|----------------|--------|
| **Client agreement upload** | Store signed agreements (immutable, auditable) | ✅ Done — ClientDocument has document_type (agreement/kyc/other), document_version, signed_at; no overwrite; audit on upload |
| **Risk profiling questionnaire** | Store risk profile and date of update | ✅ Done — Client.risk_profile, risk_profile_updated_at; set on convert and client edit |
| **Investment advice reports** | Store and version advice/recommendations | ✅ Partial — Recommendation / Workflow models; ensure immutable trail |
| **Disclosure documents** | Store what was disclosed and when | ✅ Done — ClientDisclosure (client_id, disclosure_type, disclosed_at, disclosed_by, notes); created when agreement uploaded at convert |
| **Activity audit trail** | Trace advisor actions to user and timestamp | ❌ Not done — covered by §4 Audit Logs; implement and link to recommendations/approvals |

**Developer actions:**

- [ ] Add “client agreement” upload flow: type, version, client_id, file (encrypted at rest), no overwrite; link to audit log.
- [ ] Ensure risk profile has “last_updated” and history if required; link to recommendations.
- [ ] Add disclosure module: document type, client, date, optional file; auditable.
- [ ] Ensure all critical actions (recommendations sent, executed, reviews) are in audit log with user and timestamp.

---

## Summary: Priority Order for Developer

1. **Audit logging (§4)** — Add `AuditLog` and log login (success/fail), password change, client/portfolio access and changes, data downloads.
2. **Authentication (§1)** — Add 2FA, password strength policy, password reset with OTP/email, and log IP/device at login.
3. **API security (§5)** — Rate limiting (login + API) and token-based API access (JWT/Bearer) are implemented; see §5 for WAF documentation.
4. **Data protection (§2)** — Enforce HTTPS and `SESSION_COOKIE_SECURE` in production; document TLS and DB encryption.
5. **Backup & DR (§9)** — Daily encrypted backups; DR runbook.
6. **RIA extras (§11)** — Client agreement upload (auditable), risk profile versioning, disclosure storage.
7. **VAPT (§6)** — Fix dependencies, then CERT-In empanelled VAPT before launch.
8. **Incident & hosting (§8, §10)** — Document hosting, DR, and incident reporting.

Use this checklist in code reviews and sprint planning until all “Not done” and “Partial” items are closed and VAPT is passed.

---

## 12. Accessibility (WCAG 2.1 AA)

**Requirement:** Client login and dashboard must be usable by visually impaired, hearing impaired, and motor disability users. Comply with **WCAG 2.1 Level AA**.

| Requirement | Developer task | Status |
|-------------|----------------|--------|
| **Screen reader** | NVDA/JAWS compatible; semantic HTML; ARIA labels | Done in base, login, dashboard, clients |
| **Keyboard navigation** | Full site usable without mouse; logical tab order; skip link | Skip link, focus visible |
| **Color & contrast** | WCAG contrast; do not rely on color alone (e.g. profit/loss use text/icon) | Bootstrap; add text for status/values |
| **Text alternatives** | All images/icons have alt or aria-label | Icons/buttons labeled |
| **Forms** | Labels linked to fields; errors announced (role="alert") | Login and client forms |
| **Resizable text** | Support up to 200% zoom | rem-based; no fixed text-only px |
| **Session timeout** | Warning before expiry; allow extension | Optional when needed |
| **Accessible PDFs** | Portfolio/advisory reports tagged for screen readers | When reports are generated |

**Full checklist:** See **`docs/ACCESSIBILITY_CHECKLIST.md`**. **Developer instruction:** Build the platform compliant with WCAG 2.1 AA including screen reader compatibility, keyboard navigation, accessible forms, proper color contrast, and accessible PDFs for all client reports.
