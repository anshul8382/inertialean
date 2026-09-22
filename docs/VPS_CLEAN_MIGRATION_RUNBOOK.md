# VPS Clean Migration Runbook

Living record for **clean AlmaLinux VPS** setup and **Inertia** migration (no cPanel).  
**Agent:** `.cursor/rules/vps-migration-agent.mdc` · **Skill:** `.cursor/skills/vps-clean-migration/SKILL.md`  
**Update this file** after every completed phase with real IPs, ports, and commands that worked.

---

## Migration 2 (ACTIVE) — Lean/Bluehost → Hostinger KVM 4

> Everything below the "Deployment package" heading is the **Migration 1** record
> (BigRock `66.116.199.231` → Bluehost `129.121.133.25`). Phase commands there are
> proven on AlmaLinux and reused verbatim here unless noted.

### Target inventory

| Item | Value |
|------|--------|
| Provider / plan | Hostinger **KVM 4** (no control panel) |
| Region | **India – Mumbai 2** — locked at provisioning, cannot be moved later |
| OS | AlmaLinux **10** (match Lean: proven with Airflow 3.0.6 + its Python) |
| Specs | 4 vCPU / **16 GB** / 200 GB NVMe / 16 TB bandwidth |
| Term | ₹1,099/mo on 24-month; renews ₹2,399/mo |
| Public IP | **`187.127.188.97`** (Hostinger KVM 4 / `srv1996303.hstgr.cloud`, 2026-09-21) |
| Mac SSH alias | **`inertia-new`** (keep `inertia-vps` → Lean; source must stay reachable) |
| SSH key | `~/.ssh/inertia_vps` (add `.pub` **during provisioning** — never use a root password) |
| App path | `/opt/Inertia2026v1` (same convention) |
| Gunicorn bind | `127.0.0.1:5004` behind nginx — only 80/443 public |
| Clone source | `anshul@129.121.133.25` — **must stay alive** until smoke passes |

### Why Mumbai is non-negotiable

Client data must stay within India's legal boundary per the SaaS advisory carried in the
IA Master Circular Ch. IV §5 (source circular `SEBI/HO/MIRSD2/DOR/CIR/P/2020/221`), which
also requires a **half-yearly undertaking to SEBI** (§31.1). This applies to **backups and
snapshots too**, not just compute.

CSCRF does **not** apply to an IA-only registrant per `SEBI/HO/ITD-1/ITD_CSC_EXT/P/CIR/2025/60`
§2.3 — so no MeitY empanelment, STQC, dedicated HSM or SOC 2 is required. Do not buy them.
If a second SEBI registration is ever added, this exemption lapses and the highest category applies.

**Audit file:** obtain written confirmation from Hostinger of (a) the physical data-centre
location and (b) where VPS backups/snapshots are stored.

### Phase 0 — ordering the KVM 4 (do this first)

Two fields cannot be changed later without rebuilding the box, so get them right at checkout.

| Field | Choose | Why |
|-------|--------|-----|
| Plan | **KVM 4** — 4 vCPU / 16 GB / 200 GB NVMe | Decided; see Target inventory |
| **Location** | **Mumbai, India** | **Irreversible.** Data-in-India is the compliance basis for the whole setup |
| **OS** | **Plain AlmaLinux 10** — no control panel, no LAMP/WordPress template | Matches Lean exactly, so Migration 1's proven commands and the Airflow 3.0.6 + Python pairing work verbatim |
| SSH key | Paste **`~/.ssh/inertia_vps.pub`** | `provision_new_host.sh` defaults `SSH_KEY=~/.ssh/inertia_vps` and uses the **same** key for Lean and the new host — reusing it means the script runs unmodified |
| Term | 24 months | Cheaper up front; the **renewal** rate is the real long-run cost |
| Hostname | e.g. `inertia-kvm4` | — |
| Backups add-on | Optional — own nightly `.sql.gz` already covers data | If bought, **get written confirmation snapshots are stored in India** (open item 4) — a snapshot abroad breaks data residency |
| IPv6 | Skip unless needed | An IPv6 address with only IPv4 firewall rules is a classic hole; firewalld must cover both |

**Decline** any Hostinger auto-setup that installs a control panel, LAMP stack or WordPress — the
clean box is the point. **AlmaLinux 10 ships no `mysql-server` package**; MySQL 8.4 comes from
Oracle's repo with `mariadb*` excluded (proven on Lean: 8.4.11).

While the sales/support thread is open, request **written confirmation of data-centre location and
backup storage region** — this is the evidence for the half-yearly SEBI undertaking.

### Objectives — what this migration buys beyond "it runs"

Ranked by value, because "the app works on the new box" is the floor, not the goal.

1. **Collapse the public attack surface from a login page to nothing.** Today Lean serves the app
   on public HTTPS, so the login form faces the internet and absorbs credential stuffing and bot
   traffic. After this, the app has **zero publicly reachable endpoints** — any future auth bypass
   in Flask, a dependency, or our own code is unreachable rather than merely unexploited.
2. **Turn the office-only access policy into a technical control.** "2FA lives on the office
   desktop" is currently a social convention; nothing stops a user enrolling a personal phone.
   Tailscale device approval enforces it at the network layer **while keeping per-user TOTP**, so
   `models/audit_log.py` still attributes actions to individuals. Stronger than admin-held backup
   codes, which would have traded away exactly that attribution.
3. **Clear the DB-cutover backlog for free.** `DEFER_DB_FEATURES=audit_log`, the gated financial
   planning tables, and several pending `migrations/add_*.py` have accumulated because prod
   migrations mid-sprint are risky. A fresh box with **Lean live as rollback** is the only moment
   this costs nothing. Result: a working audit trail from day one — which is what a SEBI
   inspection actually asks to see — and feature gates emptied.
4. **A genuinely abortable migration.** Lean stays untouched until DNS moves, so failure costs a
   decision, not an outage. Migration 1 had no such property.
5. **Convert Migration 1's pain into a repeatable procedure.** The pain-point table below records
   *root causes*, not symptoms (bcrypt is `SECRET_KEY`-independent; chrony before 2FA testing;
   pre-install the build toolchain; patch host-delta `.env` keys). This plus
   `.cursor/skills/move-install-to-new-host/` makes the *next* move days rather than weeks —
   real leverage against vendor lock-in.
6. **Fewer moving parts to misconfigure:** no certbot for the app, no public lockdown vhost, no
   `httpd_can_network_connect`, no inbound webhook. Each removal deletes a failure mode.
7. **Consolidate two properties on one box without paying for it in security,** enforced by one
   rule: a public vhost never proxies `/` to Gunicorn.
8. **Headroom for the roadmap** — 16 GB for the internal planning tool, and a defined safe home
   for the public DIY tool (static, no DB, therefore no DPDP obligation).

**Honest accounting of what it does _not_ buy:** no meaningful speed gain (Lean's CPU is barely
used — 16 GB is insurance against an OOM during a report spike, not a measured need); it adds a
third-party dependency on Tailscale; and hosting the public site ourselves makes its patching our
problem. We are paying ~50% more for headroom and posture, not for performance.

### Change delta vs Lean → operational impact

| Area | Lean today | KVM4 | What changes for you day to day |
|------|-----------|------|--------------------------------|
| **App reachability** | Public HTTPS on `inertiainvest.in`; login page internet-facing | **Tailnet-only** `https://<host>.<tailnet>.ts.net` | **The biggest change.** Staff need Tailscale on their machine. Working from a personal laptop stops being possible — that is the intent, but ensure the admin machine is on the tailnet *before* closing anything |
| **Leegality status** | Webhook pushes automatically | Click **Refresh** on the agreement (`POST /agreements/<id>/refresh-leegality`) | Status arrives on a click instead of instantly. Disable the webhook at Leegality's console |
| **Public website** | equities4wealth.com hosted elsewhere | Static docroot on the same box | One box to manage; site content must be copied; its patching becomes ours |
| **Contact / careers forms** | Browser → public API with shared key | Browser → nginx → **loopback** with injected key | No visible change to visitors. Rotate `WEBSITE_CONTACT_API_KEY` |
| **Reverse proxy** | nginx fronts the app publicly | nginx serves static site only; `tailscale serve` fronts the app | Certbot only for equities4wealth.com |
| **SSH** | Public port 22 | Tailnet-only, **after** tailnet access is proven | Break-glass = Hostinger web console + `ssh -L 8443:127.0.0.1:5004` |
| **Audit log** | `DEFER_DB_FEATURES=audit_log` | Enabled | Audit trail populated from first login |
| **Financial planning** | Gated off (tables absent) | Tables present, gate removed | Feature available when built out |
| **Logins / 2FA** | Working | **Unchanged** — bcrypt hashes and TOTP secrets carry over | No password or 2FA resets, unlike Migration 1 — provided chrony is verified and `SECRET_KEY` is not rotated |
| **Zoho Books** | Working | **Unchanged** — `.env` copied verbatim | No reconnect; do not re-run Connect |
| **Airflow** | 24 DAGs, SequentialExecutor | Same, verified before unpausing | Unpause on KVM4 only after Lean is stopped |
| **RAM / swap** | 8 GB | 16 GB **+ swap file** | Report-generation spikes stop risking an OOM kill |
| **Mobile / Capacitor** | Works anywhere | Only on tailnet devices | **Undecided** — see open items |
| **Cost** | Bluehost | Hostinger KVM 4, ~₹1,099/mo; Tailscale ₹0 via tagged devices | ~50% more for headroom and posture |

**New recurring operational step:** onboarding a staff member now includes installing Tailscale
and approving the device; offboarding includes **removing** it, because key expiry is disabled by
default on tagged devices.

### Decision log

| Date | Decision |
|------|----------|
| 2026-09-21 | **Hostinger KVM 4** over Bluehost NVMe 8/16. Promo pricing is new-purchase-only on both, so a new IP + migration is required either way — migration cost is not a differentiator |
| 2026-09-21 | 16 GB chosen: RAM is the only measured constraint (Lean showed **7.7 GiB total, ~2.3 GiB available at idle**) while load average was 0.28 on 4 vCPU |
| 2026-09-21 | Bluehost NVMe 16 (₹1,676/mo) **rejected** — pays for 8 vCPU at ~7% utilisation and 450 GB for a 192 MB database |
| 2026-09-21 | No control panel; no Ollama on VPS (carried from Migration 1) |
| 2026-09-21 | MySQL bound to `127.0.0.1`; admin access from Mac via **SSH tunnel only**. 3306 never opened in firewalld **or** the hPanel firewall |
| 2026-09-21 | **LUKS on the MySQL datadir rejected** — see rationale below |
| 2026-09-21 | Timezone: **match Lean**, do not set independently (Airflow schedules were authored against Lean's setting) |
| 2026-09-21 | `DEFER_DB_FEATURES=` set **explicitly empty** on the new box — audit trail live from day one |
| 2026-09-21 | Credential rotation: _pending operator decision_ (see Open items) |
| 2026-09-21 | **No shared MySQL and no cross-host DB link.** KVM4 runs a throwaway copy during test; one short cutover window instead — see Parallel running policy |
| 2026-09-21 | **Access model: Tailscale device-bound, tagged office machines.** Per-user TOTP retained (users hold their own); `admin_reset_2fa` is the recovery path. Admin custody of 2FA secrets **rejected** — see Access model |

#### Why LUKS was rejected

The datadir can be moved onto an encrypted volume later with a maintenance window (stop
MySQL → move files → update `datadir` → start); it is not a one-way door, so there is no
penalty for deferring.

On a rented KVM guest LUKS buys little: the volume must be unlocked to be useful, so either
a passphrase is entered at **every** boot — meaning an unattended reboot leaves the app down
until someone intervenes via the provider console — or the key sits on the same disk, which
defeats the purpose. While mounted, the key is in guest RAM, readable by the host operator
regardless.

**Instead:** encrypt the nightly dumps (`age`/`gpg` before they leave the box), keep TLS in
transit, and consider InnoDB tablespace encryption if at-rest protection is later required.
Note that no SEBI circular mandates encryption at rest for an IA-only registrant, and DPDP's
security-safeguard obligations do not commence until **13 May 2027**.

#### Parallel running policy — two DBs are fine, two *writable* DBs are not

**Rejected:** pointing the KVM4 app at Lean's MySQL over the network (via opened 3306,
persistent `autossh` tunnel, or Tailscale). Reasons:

1. **Latency multiplies per query.** Cross-provider RTT inside Mumbai is a few ms, but the
   audit and report endpoints issue hundreds of queries per request. That re-creates the exact
   `WORKER TIMEOUT` class of failure just fixed on `full-from-period` — only this time it
   cannot be resolved with `PORTFOLIO_AUDIT_PRICE_SOURCE=db`.
2. **It adds a new failure mode** (tunnel drop = app-wide DB outage) for a problem measurement
   says does not exist.

**Why it is unnecessary:** the database is ~192 MB logical / 138 tables, so dump → copy →
restore is a **sub-15-minute** window. Traffic analysis showed usage confined to office hours
from one office IP, with nights effectively idle. A night cutover costs approximately nothing.

**Correct sequence (no split-brain):**

| Stage | Lean (old) | KVM4 (new) |
|-------|-----------|------------|
| Test | **Authoritative** — staff keep using it | Throwaway DB copy; DAGs **paused** |
| Cutover window | Stop gunicorn (writes cease) → final dump | Restore final dump → start → verify |
| After | gunicorn **stopped**, box powered on for rollback | Authoritative; DAGs unpaused |

**Rules during the test stage:**

- Do **not** give staff the new IP — writes made there are lost at the final re-dump.
- Keep **all DAGs paused** on KVM4. Note this is **not** a client-email risk: DAGs send
  *internal* mail only (`daily_reports_dag` → `email_on_failure` to `anshul@equities4wealth.com`
  plus workflow / leads / monthly-investment reports). The only client-facing email is the
  **recommendation**, sent manually from `routes/unified_recommendations.py` /
  `services/proposal_email_service.py` — no DAG can trigger it. The actual reasons to pause:
  - **Shared external resources under identical credentials.** `.env` is copied verbatim, so both
    boxes use the same Google service account, Sheets, Drive and Zoho. The price path *writes*
    GOOGLEFINANCE formulas into a shared sheet (`services/google_sheets_historical_price.py`,
    `price_sheet_suppress_cycles_dag`) — two boxes doing that concurrently contend on the same cells.
  - **Backup collisions** — `database_backup_dag` / `codebase_backup_dag` writing to one destination.
  - **Duplicate internal alerts** erode trust in failure mail exactly when it matters.
  - DAG writes land in the throwaway DB anyway, so running them buys nothing.
- `EMAIL_SOURCE_TAG=KVM4 test` is still worth setting so any internal mail that does escape is
  obviously from the test box. It labels only — it does **not** suppress sending
  (`services/email_source_tag.py`).
- Unpause DAGs on KVM4 **only after** they are stopped on Lean — never both at once.

**Not doing:** MySQL replication (Lean source → KVM4 replica) would cut the window to seconds,
but needs binlog + GTID + a cross-provider encrypted link, and is over-engineering for a 192 MB
database with idle nights. If a persistent host-to-host link is ever genuinely needed, use the
existing `scripts/deployment/enable_private_access_tailscale.sh` rather than opening 3306.

### Access model — tailnet-only app (decided 2026-09-21)

No client-facing surface exists today, so the **application** goes entirely off the public
internet. Two layers, deliberately separated:

| Layer | Answers | Mechanism | Audit trail |
|-------|---------|-----------|-------------|
| Network | "Is this an approved office machine?" | Tailscale, office desktops joined as **tagged** devices | Per-machine |
| Application | "Who is this person?" | App login + **per-user** TOTP (users keep their own) | Per-user (`models/audit_log.py`) |

Admin custody of 2FA secrets was **rejected**: it destroys per-user attribution in the audit
log, which is the one control a SEBI inspection will actually ask to see. `admin_reset_2fa`
(`services/two_factor_service.py`) already covers lost-authenticator recovery.

**Tailscale cost: ₹0, permanently.** Per [tailscale.com/pricing](https://tailscale.com/pricing),
billing is per **seat**, and *"a user device is simply anything that is not tagged as a
resource… a tagged resource is a device that is owned by a tag rather than a user identity.
You should tag resources like servers, subnet routers, app connectors, and other shared
infrastructure."* Office desktops are shared infrastructure, so they join with a **tagged auth
key** and consume **tagged-resource** quota (50 free), not user seats. Keep Tailscale *users*
to the admin account only; staff identity lives in the app layer. This sidesteps the
6-user Personal cliff (7 users ≈ $56/month ≈ ₹56k/year — more than 4× the VPS).

Tagging removes prior user ownership of a device, and **key expiry is disabled by default on
tagged devices** — so device offboarding is manual: remove a retired machine in the admin
console or it stays authorized.

#### Hosting equities4wealth.com on the same box (decided 2026-09-21)

"Fully private, no public ports" is **off the table** — the public site moves onto this VPS, and
the app already has a live inbound public dependency: **`api/v1/public_contact.py`** serves
`POST /api/v1/public/contact` and `/api/v1/public/careers`, which the existing
equities4wealth.com site calls to create `Lead` rows. Taking the app tailnet-only without a
pinhole for these **stops all website lead capture**.

**Governing rule: a public vhost must never proxy `/` to Gunicorn.** Public vhosts serve static
files, plus an explicit allowlist of POST paths — the deny-all pattern already used correctly in
`config/apache-inertiainvest-hooks.conf` (`<Location "/">` → `Require all denied`, then one
granted path). Three vhosts:

| Hostname | Exposure | Serves |
|----------|----------|--------|
| `equities4wealth.com` | Public 443 | **Static docroot** + two form-submit locations (below) |
| `inertiainvest.in` | **No public app vhost** | Nothing — DNS may stay parked / redirect to the static site |
| `<host>.<tailnet>.ts.net` | **Tailnet only** | The full staff app via `tailscale serve` → Gunicorn |

**The app has zero publicly reachable endpoints.** See "Leegality webhook dropped" below. No
`hooks.` subdomain, no public lockdown vhost for the app, and therefore **no nginx port of
`config/apache-inertiainvest-public-lockdown.conf` needed**. Login page, client data,
`/financial-planning/*`, Airflow: unreachable from the internet on every hostname.

#### Leegality webhook dropped — poll outbound instead (decided 2026-09-21)

Operator confirmation: volumes are controlled (one agreement pending at decision time) and the
integration is a **convenience to avoid checking the Leegality portal, not a hard dependency**.

Verified in code: **the webhook is only a trigger.** `apply_webhook_payload()` does the metadata
write then calls **`refresh_agreement_from_leegality()`** for the real work, and that function is
fully **outbound** — *"Poll document details; if completed, download signed PDF"* via
`fetch_transaction_status()`, falling back to `fetch_document_details(include_file=True)`,
normalising invitee statuses and pulling the signed PDF.

A manual trigger for it **already exists and needs no new code**:
`POST /agreements/<id>/refresh-leegality` → `refresh_agreement_leegality()`
(`routes/agreements.py`, `@login_required`).

So dropping the public webhook costs only latency and three cosmetic metadata fields
(`last_webhook_at`, `last_webhook_type`, `last_invitee_action`). Signed status and the signed PDF
still land in Inertia — on a click now, on a schedule later.

| | Kept as webhook | Dropped, poll instead |
|---|---|---|
| Public app endpoints | 1 | **0** |
| Leegality retry / source-IP questions | Must be answered | **Moot** |
| nginx lockdown vhost for the app | Required | **Not needed** |
| Cutover split-brain risk | Real (see below) | **Eliminated** |
| Status latency | Seconds | Click, or poll interval |

**Optional later:** a small Airflow DAG calling `refresh_agreement_from_leegality()` for
agreements holding a `leegality_document_id` whose status is not yet terminal
(completed / rejected / expired), every 15–30 min. The one piece of work is the selection query —
document IDs live in `AgreementVariables` (see `_find_agreement_by_document_id`), not a column, so
it needs a helper. Not required for cutover.

**Also do:** disable/ignore the webhook at the Leegality console so it is not left posting to a
dead URL, and treat `api/v1/leegality.py` as unreachable-by-design rather than deleting it.

#### Integration inbound/outbound audit (verified in code 2026-09-21)

| Integration | Status | Needs public inbound? |
|-------------|--------|------------------------|
| **Leegality** e-sign | **Live, keeping** | **Yes** — `POST /api/v1/leegality/webhook`, HMAC-verified via `LEEGALITY_PRIVATE_SALT`, rejects bad signatures (`api/v1/leegality.py`) |
| **Zoho Books** | Live | **No.** Runtime is outbound only via `ZOHO_REFRESH_TOKEN` (India DC: `accounts.zoho.in` / `zohoapis.in`). `/settings/zoho/callback` is `@login_required` **and admin-only** |
| equities4wealth forms | Live | **No** — loopback via nginx header injection (below) |
| WhatsApp | **Not in use**, may adopt later | Not now. Adding it later = one extra allowed `location` on `inertiainvest.in` |
| Gmail SMTP, Sheets/Drive service account, price feeds | Live | No — outbound |

**Zoho needs nothing done at migration.** `provision_new_host.sh` copies `.env` verbatim, so
`ZOHO_REFRESH_TOKEN` carries over and Books keeps working without reconnecting. Do **not** re-run
Connect casually — `routes/settings.py` warns Zoho returns no refresh token if the app is already
authorized, requiring a revoke in Zoho Accounts → Connected Apps first.

If Zoho ever does need re-authorizing, set
`ZOHO_REDIRECT_URI=https://<host>.<tailnet>.ts.net/settings/zoho/callback` (the default is
`http://127.0.0.1:5001/...`) and register it in the Zoho console; the admin's browser reaches it
over the tailnet and `.ts.net` supplies real HTTPS. **Fallback if Zoho rejects a `.ts.net` URI:**
use a Zoho **Self Client** to mint the refresh token with no redirect URI at all.

#### ~~Leegality × DNS cutover — split-brain risk~~ (eliminated by dropping the webhook)

_Retained for reference only — applies **only** if the webhook is ever re-enabled._

Leegality posts to whatever `inertiainvest.in` resolves to. During propagation a signature event
can land on **Lean**, writing e-sign status into the **abandoned** database and being lost
silently — the callback would succeed, so nothing would alert.

Mitigation, in order of preference:
1. **Stop Lean's web tier (nginx + gunicorn) at cutover, not just the DAGs.** Callbacks then fail
   loudly and Leegality retries until DNS points at KVM4. Failing is strictly better than
   succeeding into a dead DB.
2. Send no documents for signature during the cutover window.
3. After cutover, reconcile any document signed during the window against Leegality.

**Confirm with Leegality support:** (a) does it retry webhooks on 5xx/timeout, and with what
backoff; (b) does it publish fixed source IPs? If yes, allowlist them in nginx — that reduces the
one publicly-exposed app endpoint to near-zero risk. Rate-limit this location regardless.

**Reverse proxy is nginx, not Apache.** Lean runs nginx (`config/nginx-inertiainvest.in.conf`);
the `config/apache-*.conf` lockdown and hooks templates were written for the old cPanel/Apache
BigRock host. **nginx equivalents must be written** — this is new work, not a file copy.

#### Co-hosting win: the lead API stops being public

Because the site and the app now share the box, the form submission no longer has to cross the
public internet. The browser POSTs to a path on `equities4wealth.com`; **nginx injects the secret
header server-side** and proxies to loopback:

```nginx
limit_req_zone $binary_remote_addr zone=publicform:10m rate=5r/m;

location = /contact-submit {
    limit_req zone=publicform burst=3 nodelay;
    client_max_body_size 16m;                 # match MAX_CONTENT_LENGTH (résumé uploads)
    proxy_set_header X-Website-Contact-Key "<key>";   # set = overwrites client-supplied value
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_pass http://127.0.0.1:5004/api/v1/public/contact;
}
```

Three properties this buys, all of which the previous "expose the API path" plan lacked:
- The key **never reaches the browser**, closing the leak in open item 8. `proxy_set_header`
  *overwrites* any client-supplied value, so it also cannot be spoofed.
- `/api/v1/public/*` is never a publicly addressable path.
- Abuse is throttled in nginx before it reaches Python, a DB write, a file save and an email.

Repeat for `/careers-submit` → `/api/v1/public/careers`. Keep the nginx config root-owned `0640`
since it holds the key.

**Rate limiting keys correctly behind the proxy** — verified: `__init__.py` applies
`ProxyFix(x_for=1, …)`, and nginx appends the real client IP to `X-Forwarded-For`, so
Flask-Limiter's `get_remote_address` sees real clients rather than `127.0.0.1`. ProxyFix takes
the rightmost value, so a client-supplied `X-Forwarded-For` cannot spoof it. Still tighten the
per-route limit — the 60/hour default is too loose for an unauthenticated form.

**Gap confirmed live:** the only webhook config in the repo
(`config/apache-inertiainvest-hooks.conf`) whitelists **only** `/api/v1/whatsapp/webhook` — which
is **not in use** — while **Leegality, which is live, is absent from it**. Any lockdown config
written for KVM4 must allow `/api/v1/leegality/webhook` or e-sign callbacks break silently.

Clients still need no inbound access to the staff app: Leegality hosts the signing page itself
(`leegality_sign_url`); Gmail SMTP, the Sheets/Drive service account and price feeds are outbound.

#### Public-endpoint hardening (before exposing `/api/v1/public/*`)

Already in place: extension allowlist (`LEAD_DOC_EXTENSIONS`) + 16 MB `MAX_CONTENT_LENGTH` on
uploads, Flask-Limiter defaults (200/day, 60/hour), honeypot field, `html.escape` on
email output. To verify or fix:

1. ~~Is the API key exposed in browser JS?~~ **Resolved by nginx header injection above** — but
   confirm the current site's form is repointed to `/contact-submit`, and **rotate
   `WEBSITE_CONTACT_API_KEY`** if it was ever shipped in client-side JS.
2. Tighten the rate limit on `/api/v1/public/*` well below 60/hour — every call writes DB rows,
   sends mail and may save a file. nginx `limit_req` is the first gate; add a Flask-Limiter
   decorator as the second.
3. `_authorized()` uses `provided == expected`; prefer `hmac.compare_digest`.
4. `_uploaded_file()` falls back to *any* file in `request.files.values()` — fine given the
   extension allowlist, but keep that allowlist tight.
5. `client_max_body_size 16m` must match `MAX_CONTENT_LENGTH` or résumé uploads fail as nginx 413
   before Flask ever sees them.

#### Financial planning: two separate tools (clarified 2026-09-21)

No client logins are involved. Nothing needs to leave the tailnet.

| | Public DIY tool | Full tool inside Inertia |
|---|---|---|
| Where | `equities4wealth.com` static docroot | Existing `routes/financial_planning.py` |
| Who | Anyone | **Existing staff users** (already `@login_required`, client-scoped) |
| Backend | **None** — browser JS only | Flask + MySQL, tailnet-only |
| Exposure added | Zero | Zero |

**Keep the DIY tool strictly backend-free.** Its whole value here is that a calculator storing
nothing adds no attack surface *and* incurs **no DPDP obligation** — no personal data collected,
no notice, no consent, no erasure duty. The moment it grows "email me my plan" or "save my
results" it acquires all three. If lead capture is wanted, reuse the existing `/contact-submit`
pinhole rather than adding a second public endpoint, and treat that as a deliberate DPDP decision.

Do **not** add public routes to `financial_planning_bp` — one missing decorator becomes a
client-data breach, and it would drag the staff app back onto public 443.

**Calculation parity:** if the public JS tool and the internal Python tool project the same
numbers, the formulas must be defined once and kept in step, or a prospect's DIY output will
contradict the advised plan. Per `.cursor/rules/core-logic-expert.mdc`, the internal side keeps
its logic in `services/` with tests; document the shared assumptions (return rate, inflation,
rounding) in one place and add parity fixtures when the internal tool is built.

**SEBI:** a public tool run by a registered IA is subject to the advertisement code. Keep outputs
generic arithmetic (corpus, goal amount, SIP needed), label them illustrative, show assumed return
rates as assumptions and never as promises. Specific scheme or security recommendations without
KYC and risk profiling cross into regulated advice.

#### Migration doubles as the DB-cutover window

The internal planning tool is **gated and not yet on prod**: `docs/DB_CUTOVER_REGISTRY.md` lists
`NAV_FINANCIAL_PLANNING_ENABLED` → `migrations/add_financial_planning_tables.py` +
`FINANCIAL_PLANNING_ENABLED=true`, and `add_financial_planning_tables.py` is still in the pending
list alongside `add_client_secondary_emails.py`, `add_company_industry_fields.py`,
`add_google_calendar_ops_task_columns.py`, `add_hot_stock_rating.py` and others.

A fresh host is the **zero-risk moment to clear that whole backlog**, because Lean stays live and
untouched as the rollback: restore the Lean dump on KVM4 → run every pending migration there →
verify → only then cut over. This avoids the "don't rush prod migrations mid-sprint" constraint
entirely, since KVM4 is not prod until DNS moves. Build the new box with `DEFER_DB_FEATURES`
**empty** (including `audit_log`) rather than carrying the gates forward.

#### SEBI website disclosures
Investor charter, monthly complaints data, compliance-audit status and MITC stay publicly
reachable — serve them from the `equities4wealth.com` static docroot (confirm which entity is
the SEBI-registered IA and publish on that entity's site).

#### Break-glass (mandatory before closing public ports)
Tailscale's data plane is peer-to-peer, but **new** connections need its coordination server, so
an outage can block fresh logins. Document and test *before* cutover: Hostinger web console →
`ssh -L 8443:127.0.0.1:5004` → browse locally. Do not close public SSH until this is proven.

### Migration 1 pain points → root cause → prevention

| # | Symptom (Migration 1) | Root cause (verified in code) | Prevention for Migration 2 |
|---|----------------------|-------------------------------|----------------------------|
| 1 | 2FA codes + passwords rejected; had to reset | **Passwords cannot break:** `models.py` uses **bcrypt** (`set_password`/`check_password`) — hashes are self-contained and independent of `SECRET_KEY`. **TOTP cannot break from restore either:** `two_factor_secret` is a plain `String(32)` (not encrypted). Real causes: (a) `stage_twofa` runs `enforce_mandatory_2fa_all_users.py` → `two_factor_required=True`, forcing **enrollment** for anyone not already enrolled — by design, and BigRock users were not enrolled; (b) TOTP is time-based, so **clock skew breaks valid secrets** | Install + verify **chrony before** testing 2FA. Lean users are already enrolled, so the clone carries their secrets — do **not** clear them. **Do not rotate `SECRET_KEY`.** Acceptance test before cutover: a real user logs in with existing password **and** existing TOTP |
| 2 | Airflow DAGs slow, set up one by one | `AIRFLOW__CORE__EXECUTOR=SequentialExecutor` — triggering 24 DAGs runs them **serially**, so drain is inherently slow (not a setup fault). Plus `stage_dags` waits only `sleep 15` before listing; a cold dag-processor may not have parsed all DAGs yet, so some never get unpaused | Before unpausing: `airflow dags list-import-errors` must be clean and `airflow dags list` must show **all 24**. Re-run the unpause loop if the count is short. Then trigger and walk away — check `bash provision_new_host_remote.sh dag-status` later rather than watching |
| 3 | `requirements.txt` would not install in one go | `stage_install` installs only `git`, `python3`, `python3-pip`, `python3-devel`. Missing: build toolchain and **runtime** system libs. `weasyprint>=66.0` needs **Pango/Cairo at import time**; `lxml`, `Pillow`, `reportlab` need dev headers when no wheel matches | Pre-install system deps **before** the install stage (below). Also confirm `python3 --version` matches Lean — `numpy==1.26.4` / `scipy==1.13.1` have no wheels for newer CPython and would fall back to a source build |
| 4 | `.env` needed manual edits despite identical credentials | `provision_new_host.sh` copies `.env` **verbatim**; the remote script only forces `FORCE_2FA_FOR_ALL_USERS=true`. Every other **host-specific** key stays at Lean's value | Apply the host-delta list (below) as a scripted patch after copy — never hand-edit |

#### Pre-install system packages (run before the `install` stage)

```bash
sudo dnf groupinstall -y "Development Tools"
sudo dnf install -y python3-devel libffi-devel openssl-devel \
  libxml2-devel libxslt-devel libjpeg-turbo-devel zlib-devel freetype-devel \
  pango cairo gdk-pixbuf2 harfbuzz shared-mime-info
# chrony — REQUIRED before 2FA testing (TOTP is time-based)
sudo dnf install -y chrony && sudo systemctl enable --now chronyd
chronyc tracking      # confirm small offset before testing any TOTP code
```

#### MySQL must be **8.4**, not MariaDB

Migration 1 went MariaDB 10.11 (BigRock) → **MySQL 8.4** (Lean). Lean is now the source, so the
new box must also be **MySQL 8.4** — AlmaLinux's default `mariadb-server` would risk failing on
MySQL 8 collations (`utf8mb4_0900_ai_ci`). `requirements.txt` carries `cryptography` specifically
for MySQL 8 `caching_sha2_password`, confirming MySQL 8 is assumed.

**Sequencing trap:** `stage_install` runs `mysql -u root` with **no password**. Either run the
provision script *before* `mysql_secure_installation`, or place credentials in `/root/.my.cnf`
first — otherwise the DB/user creation step fails.

#### `.env` host-delta (apply after copy, before smoke)

| Key | Value on KVM4 | Note |
|-----|---------------|------|
| `SECRET_KEY` | **unchanged** | Rotating invalidates all sessions; no security benefit here |
| `DB_*` | unchanged | New MySQL user must match Lean's `DB_USER` verbatim |
| `DEFER_DB_FEATURES` | **empty** (`DEFER_DB_FEATURES=`) | Must be explicit — code defaults to deferring when unset |
| `EMAIL_SOURCE_TAG` | `KVM4 test` during parallel; final value at cutover | Labels only, does not suppress |
| `SESSION_COOKIE_SECURE` / `REMEMBER_COOKIE_SECURE` | `false` for HTTP IP smoke → **`true`** after certbot | Migration 1 left these; see Cutover cheat sheet |
| `PORTFOLIO_AUDIT_PRICE_SOURCE` | `db` | Confirm present — this is the `full-from-period` timeout fix |
| `FORCE_2FA_FOR_ALL_USERS` | `true` | Script forces this anyway |

### Hardening checklist (reviewed 2026-09-21)

Ordered by phase. Reuse Migration 1 command blocks under "Commands that worked" unless flagged.

- [ ] **0** `dnf update -y`; reboot if the kernel changed
- [ ] **1** Sudo user `anshul` + `authorized_keys` (Migration 1 Phase 1 block)
- [ ] **2** Second SSH session as `anshul` + `sudo -v` — **leave it open**
- [ ] **3** Harden sshd via drop-in `/etc/ssh/sshd_config.d/99-hardening.conf`, not `sshd_config`
      directly (survives package upgrades). **Gate:** requires a confirmed working session first
- [ ] **4** firewalld: ssh / http / https only. SSH **rate-limiting** preferred over
      source-IP restriction — the office Tata line may change and lock you out
- [ ] **4b** Remove cockpit: `systemctl disable --now cockpit.socket` +
      `firewall-cmd --permanent --remove-service=cockpit`
- [ ] **4c** hPanel firewall as a second layer — **do not** open 3306 there either
- [ ] **4d** `fail2ban` for sshd — with `PasswordAuthentication no` this is log-noise
      reduction, not a real control. Install, but do not count it as defence
- [ ] **4e** `dnf-automatic` limited to security updates (solo admin — this matters)
- [ ] **5** Base packages + MySQL. Keep **SELinux enforcing**; if nginx cannot reach
      gunicorn use `setsebool -P httpd_can_network_connect 1` — never disable SELinux
- [ ] **5b** Swapfile (2–4 GB) even at 16 GB, plus `vm.swappiness=10` so it stays
      emergency-only — insurance against an OOM kill during a report-generation spike
- [x] **5c** Timezone — Lean is **UTC**; keep KVM4 on **UTC** (resolved 2026-09-21)
- [ ] **6** MySQL: bind `127.0.0.1`, `mysql_secure_installation`, app user matching Lean's
      `DB_USER` **verbatim** (see Open items — `provision_new_host.sh` copies `.env` as-is)
- [ ] **7** App clone: `NEW_HOST=anshul@NEW_IP ./scripts/deployment/provision_new_host.sh`
- [ ] **7b** Set `DEFER_DB_FEATURES=` (empty) in `.env`, restart, verify audit rows appear
- [ ] **8** nginx + smoke on `http://NEW_IP`; close the direct gunicorn port before cutover
- [ ] **9** Nightly `.sql.gz` backup verified **before** cutover — and encrypted at rest
- [ ] **10** Airflow full cycle green (daily backup DAG + price jobs) before cutover
- [ ] **11** DNS + certbot; then flip `SESSION_COOKIE_SECURE=true` / `REMEMBER_COOKIE_SECURE=true`
- [ ] **12** Repo IP update (see below) — **only after** smoke passes
- [ ] **13** Retain Bluehost box N days for rollback, then cancel

### Sequencing constraints

1. **Lean must stay alive.** `provision_new_host.sh` hard-fails unless `SRC_HOST` contains
   `129.121.133.25`; it is the dump + `.env` + uploads source.
2. **Do not update the repo's pinned IP until after cutover.** 15 files reference
   `129.121.133.25`, including `.cursorrules`, `AGENTS.md` and
   `.cursor/rules/development-gate.mdc`, where it encodes the "Lean only, never BigRock"
   guardrail — and the `SRC_HOST` guard inside `provision_new_host.sh` itself. Changing these
   early breaks the migration in progress. When updating, keep the guardrail explicit rather
   than only swapping the address.
3. `.env` is copied **verbatim** from Lean — do not hand-edit secrets mid-flow.

### Open items (operator input needed)

| # | Item | Why it blocks |
|---|------|---------------|
| 1 | Lean's `DB_USER` name (not password) | New box's MySQL user must match exactly or the app cannot connect |
| 2 | ~~Lean `timedatectl` + Airflow timezone~~ **Resolved: both UTC** | — |
| 3 | ~~Rotate `DB_PASSWORD` / `SECRET_KEY`?~~ **Resolved 2026-09-21: do not rotate.** | `SECRET_KEY` rotation invalidates every session and adds login friction — the exact class of problem hit in Migration 1 — for no security gain on a fresh host. bcrypt password hashes are `SECRET_KEY`-independent either way |
| 4 | Hostinger written confirmation: DC location + backup storage region | Evidence for the half-yearly SEBI undertaking |
| 5 | ~~Which integrations are live?~~ **Resolved: Leegality live (needs inbound), Zoho live (outbound only), WhatsApp unused.** | — |
| 11 | ~~Leegality webhook URL / retry policy / source IPs?~~ **Moot — webhook dropped, polling outbound instead.** Just disable it in the Leegality console | — |
| 12 | Should `inertiainvest.in` DNS park, redirect to the static site, or stay unused? | It no longer serves the app; decide before cutover so nothing points at a dead vhost |
| 6 | ~~Where do the SEBI public disclosures live?~~ **Resolved: `equities4wealth.com` static docroot.** | — |
| 7 | ~~equities4wealth source?~~ **On git (not Lean).** Need clone URL + static vs build; deploy to `/var/www/equities4wealth` on KVM4 before BigRock expiry | Clone URL from operator |
| 8 | ~~Is `X-Website-Contact-Key` sent from browser JS?~~ **Resolved: nginx injects it server-side.** Rotate the key if it was ever client-side | — |
| 10 | Does the equities4wealth site need any server-side runtime (PHP/Node), or is it fully static? | A static docroot keeps public exposure to nginx alone; a runtime adds a second attack surface on the same box |
| 9 | Is the SEBI-registered IA `equities4wealth` or another entity? | Determines which domain must carry the mandatory disclosures |

### Commands that worked — Migration 2

- **2026-09-21:** Hostinger KVM 4 provisioned — IP **`187.127.188.97`**, hostname `srv1996303.hstgr.cloud`, Mumbai 2, Plain OS AlmaLinux, panel firewall: accept 22/80/443 + drop rest. Malware scanner (free) on. Daily auto-backup off.
- **2026-09-21:** Panel firewall caused `kex_exchange_identification: Not allowed at this time`. **Turned panel firewall Off** → `ssh root@187.127.188.97` OK. Use on-box `firewalld` for hardening; re-enable panel FW only after careful Accept-before-Drop test.
- **2026-09-21:** Phase 1 — user `anshul` created (wheel), key copied from root, `ssh anshul@187.127.188.97` + `sudo` OK.
- **2026-09-21:** OS confirmed **AlmaLinux 10.2** (Lavender Lion). Lean `timedatectl` = **UTC** (NTP active). **Keep KVM4 on UTC** to match Lean DAG/cron fire times — do not set Asia/Kolkata on the server for this migration.
- **2026-09-21:** `dnf update` (kernel **6.12.0-211.56.1.el10_2**), reboot OK; chrony synced; **firewalld** on with ssh/http/https (panel FW stays Off).
- **2026-09-21:** EPEL + **fail2ban** `sshd` jail OK. Root pubkey SSH → `Permission denied` (good). Confirm `99-hardening.conf` sets `PasswordAuthentication no` so password is not offered.
- **2026-09-21:** SSH effective config: `permitrootlogin no`, `passwordauthentication no`, `allowusers anshul`. Cloud-init had `PasswordAuthentication yes` in `50-cloud-init.conf` (first-wins) — set to `no`; hardening in `00-inertia-hardening.conf`.
- **2026-09-21:** Mac got `Connection refused` — **fail2ban** had banned `182.156.141.167` after root password tries. Unbanned + `addignoreip` for build. sshd was healthy the whole time.
- **2026-09-21:** Swap **8G** `/swapfile` on + fstab. First-hour SSH hardening complete.
- **2026-09-21:** Build packages + nginx; Python **3.12.14**. MySQL **8.4.11** installed (Oracle el9 RPMs). Blocked first by **expired GPG key** `RPM-GPG-KEY-mysql-2023` (Alma 10 rpm-sequoia); fixed with `RPM-GPG-KEY-mysql-2025` / keyring refresh.
- **2026-09-21:** `mysqld` active; `mysql_secure_installation` done; `/root/.my.cnf` → `SELECT VERSION()` = **8.4.11**. BigRock expiry (~6h) — triage equities4wealth DNS vs full `provision_new_host.sh`.
- **2026-09-21:** equities4wealth.com rsynced to `/var/www/equities4wealth` (git ZIP). Site is **PHP+HTML** (`api/*.php`). nginx + php-fpm; `curl http://127.0.0.1/` → **200**. Next: `api/config.php` + DNS + certbot. Docroot is **separate** from Inertia (`/opt/Inertia2026v1`).
- **2026-09-21:** DNS A for apex+www set to `187.127.188.97` in BigRock; Google `8.8.8.8` still cached old `66.116.199.231` for a while (Cloudflare already new). Auth NS (`dns1/2.bigrock.in`) showed only KVM IP while Google lagged on `www`.
- **2026-09-21:** `provision_new_host.sh` dump OK (16M; PROCESS/tablespaces warning → added `--no-tablespaces`). `remote_install` git clone OK; DB import failed as app user: `ERROR 1227 SET_ANY_DEFINER` at DEFINER line. Fix: import as **root** + strip `DEFINER=` in `provision_new_host_remote.sh`. Resume `--from remote_install` (re-scp remote script). DAGs left running on KVM (operator choice; also still on Lean/BigRock).
- **2026-09-21:** Host-delta on KVM: `EMAIL_SOURCE_TAG=KVM4 test`, `SESSION_COOKIE_SECURE=false`, `REMEMBER_COOKIE_SECURE=false`, `DEFER_DB_FEATURES=`. Login OK via SSH tunnel `127.0.0.1:5004`.
- **2026-09-21:** **Certbot OK** on KVM — `equities4wealth.com` + `www` → `/etc/letsencrypt/live/equities4wealth.com/`; deployed into `/etc/nginx/conf.d/equities4wealth.conf`; auto-renew scheduled. Expires 2026-12-20.
- **2026-09-21:** Public contact route bug: `_safe_register(..., url_prefix="")` stripped Blueprint `/public` → routes were `/api/v1/contact` while auth allowlist expected `/public/contact`. Fixed: `url_prefix="/public"`. `WEBSITE_CONTACT_API_KEY` was missing from Lean `.env`; generated on KVM, set in app `.env` + `/var/www/equities4wealth/api/config.php` (`inertia_public_api_base` → `http://127.0.0.1:5004/api/v1/public`). Loopback POST → `success`, `lead_id=627`, `email_sent=true`. php-fpm user `apache`; `config.php` `0640` root:apache.
- **2026-09-21:** Google Calendar on KVM: copied refresh token failed (`oauth2 token` 400). Added Console redirect `http://127.0.0.1:5004/settings/google-calendar/oauth2callback` + JS origin `http://127.0.0.1:5004`. `OAUTHLIB_INSECURE_TRANSPORT` for HTTP tunnel in `routes/google_calendar.py`. Settings template bug `settings.service_readiness` → `main.service_readiness`. Disconnect/Connect → auto-sync working.
- **2026-09-21:** Book-online intake failed — PHP posts to `/api/v1/public/booking-intake` which did not exist in Inertia. Added `booking-intake` + `booking-prep-email` on `public_contact` + auth allowlist.
- **2026-09-21:** Tailscale: KVM `srv1996303` + Mac on Personal free (equities4wealth.com tailnet). Serve enabled → `https://srv1996303.tail394fa8.ts.net/` → `127.0.0.1:5004`. **Browser login OK from Mac.** Optional later: `SESSION_COOKIE_SECURE=true`; GCal redirect for `.ts.net`; tag office devices.
- **2026-09-22:** BigRock VPS archive → Mac `~/Backups/bigrock-20260922/`: dump 16M `dump_ok`; `.env`; SA; uploads 2.6M; agreements 51M; static_uploads 23M. Domain stays; VPS only retiring. Export assemble `/home/inertia/EXPORT_for_archive` 7.4G → Windows D:. Factory Reset available in BigRock VPS panel for wipe before expiry.

_(Append real SSH / phase outputs below.)_

---

## Deployment package (ready for this + next deploy)

| Item | Path / command |
|------|----------------|
| Build on Mac | `bash scripts/deployment/build_clean_vps_package.sh` |
| Output | `dist/inertia-clean-vps-latest.tar.gz` |
| Docs | `deployment/clean-vps-package/README.md` |
| First install on server | `clean_vps_first_install.sh` inside package |
| Later code deploys | `deploy_via_bundle.sh` / `deploy_via_archive.sh` |

Rebuild the tarball after meaningful commits so the next VPS/month cutover has a fresh artifact.

## Goals

| Goal | Status |
|------|--------|
| No cPanel on app server | Planned |
| No password root SSH; Mac SSH key → sudo user | In progress (user logged in as root on new VPS) |
| Add more hardware keys later via `authorized_keys` | Planned |
| MySQL localhost only; SSH tunnel from Mac | Planned |
| Parallel test on IP; DNS cutover last | Planned |
| Repeatable setup (~1 month) via this runbook | Active |
| Deployment package in `dist/` | Built via `build_clean_vps_package.sh` |

---

## Inventory

### Old production (BigRock / webhostbox)

| Item | Value |
|------|--------|
| Host | `66.116.199.231` / `66-116-199-231.webhostbox.net` |
| OS | AlmaLinux 9 (kernel 5.14 el9) |
| Specs | 4 vCPU / 12 GB RAM / ~300 GB NVMe |
| Live app | **`/opt/Inertia2026v1`** |
| Live service | `inertia-2026v1.service` (Gunicorn) |
| Legacy app (not primary) | `/home/inertia/app` |
| SSH today | **root + password** |
| Panel | cPanel present (not needed on new box) |
| Ollama | Present on old box — **do not** install on new VPS |
| Client docs | ~80 MB under live uploads/agreements |
| Mail | Gmail / Workspace SMTP |

### New VPS

| Item | Value |
|------|--------|
| Provider | **Bluehost** — confirmed. Panel reports data centre **Mumbai, India** (verified 2026-09-21) |
| OS | AlmaLinux **10** |
| Specs | 4 vCPU / **8 GB** — `free -h` showed 7.7 GiB total, ~2.3 GiB available at idle |
| Role now | **Migration 2 source** — keep alive until Hostinger smoke passes |
| Public IP | **`129.121.133.25`** |
| Domain at signup | `inertiainvest.in` (label only — **DNS not flipped yet**) |
| SSH key on Mac | `~/.ssh/inertia_vps` (+ `.pub`) |
| Current login | **root** (temporary) |
| Sudo user | `anshul` _(planned)_ |
| SSH port | `22` default _(change only if documented)_ |
| App path on **this** box | `/opt/Inertia2026v1` (same folder **name** as prod; **different machine** — not BigRock) |
| Gunicorn bind (planned) | `127.0.0.1:5004` or document actual |
| Public test URL | `http://129.121.133.25` until DNS |

**Path note:** Old host also uses `/opt/Inertia2026v1`. That is only a directory convention so scripts/systemd stay identical. You are installing on **`129.121.133.25`**, not copying into the old server.

---

## Decision log (from planning chat)

| Date | Decision |
|------|----------|
| 2026-09-15 | No pain on BigRock; migrate for **clean/secure** ops + optional cost, not outage |
| 2026-09-15 | **No cPanel** on new app server |
| 2026-09-15 | LLM/Ollama **off** VPS |
| 2026-09-15 | Source of truth app: `/opt/Inertia2026v1` |
| 2026-09-15 | Prefer 8 GB / 200 GB; 4 GB acceptable if lean + user accepts swap |
| 2026-09-15 | DNS only after IP smoke test; keep old VPS for rollback |
| 2026-09-15 | equities4wealth.com / Gmail MX **out of scope** for this cutover |
| 2026-09-15 | Enforce app 2FA on new deploy |
| 2026-09-15 | New VPS public IP = **129.121.133.25**; empty localhost DB first, prod dump later; menu reorg deferred |
| 2026-09-16 | HTTP IP smoke: `SESSION_COOKIE_SECURE=false` temporary. **At DNS + certbot:** set Secure cookies true + CSRF SSL strict — see Cutover cheat sheet |

---

## Phase checklist

- [x] **0** Inventory filled (IPs, specs) — IP `129.121.133.25`
- [x] **1** Sudo user + `authorized_keys` from root
- [x] **2** Second terminal: SSH as sudo user + `sudo -v`
- [x] **3** Harden sshd (`PermitRootLogin no`, `PasswordAuthentication no`)
- [x] **4** firewalld: ssh, http, https only
- [x] **5** Base packages + MySQL/MariaDB — MariaDB 10.11; nginx/python as needed
- [x] **6** DB created — MySQL 8.4 + prod dump restored (138 tables); health 200
- [x] **7** App code + venv + `.env` — Gunicorn active on `127.0.0.1:5004`; `/api/v1/health` → 200
- [ ] **7b** **Portable paths before copy** — lean scripts/DAGs use `from main` + `INERTIA_APP_DIR`/`_ROOT`; grep-clean of `from run` / `/home/inertia` for Airflow entrypoints (agent rule §8; Phase 10 §F)
- [x] **8** nginx + smoke — SELinux `httpd_can_network_connect`; SECRET_KEY set; SESSION_COOKIE_SECURE=false; `/` → 302
- [ ] **9** rsync uploads / static/agreements / static/uploads
- [x] **10a** Airflow 3 on Lean — scheduler + api-server + dag-processor active; **29 DAGs unpaused** (BigRock untouched)
- [ ] **10b** Lean polish — `EMAIL_SOURCE_TAG=Lean server`; confirm daily_reports emails; Zoho OAuth; `FORCE_2FA` optional
- [ ] **10c** DAG Hub green — `failed_latest=0` after portable deploy + re-trigger (Phase 10 §F)
- [ ] **11** DNS A → new IP; certbot SSL; stop old Gunicorn (**deferred / cutover**)
- [ ] **12** Old VPS retained ~N days; then decommission (**deferred / cutover**)

---

## Phase 10 — Airflow on Lean (BigRock untouched)

**Policy:** Same mail recipients / SMTP / Zoho / Sheets as BigRock. Only difference: `EMAIL_SOURCE_TAG=Lean server` on Lean mail. Do **not** pause DAGs on BigRock.

### A) Deploy via git (Mac → Lean VPS)

**Preferred:**

```bash
# On Mac — commit, then:
cd /Users/anshulkhare/Downloads/Inertia2026-lean
./scripts/deployment/deploy_lean_vps.sh                 # push + remote pull + restart
./scripts/deployment/deploy_lean_vps.sh --restart-airflow
./scripts/deployment/deploy_lean_vps.sh --skip-push     # pull-only if already pushed
```

**First time on VPS** (convert `/opt/Inertia2026v1` to a git checkout; keeps `.env` / `venv`):

```bash
# On VPS — HTTPS or SSH clone URL for https://github.com/anshul8382/inertialean
export ORIGIN_URL='git@github.com:anshul8382/inertialean.git'
# or: export ORIGIN_URL='https://github.com/anshul8382/inertialean.git'
bash /opt/Inertia2026v1/scripts/deployment/bootstrap_lean_git.sh
# If script not on server yet, scp it once or run from Mac:
# ssh -i ~/.ssh/inertia_vps anshul@129.121.133.25 \
#   "ORIGIN_URL='git@github.com:anshul8382/inertialean.git' bash -s" < scripts/deployment/bootstrap_lean_git.sh
```

Manual equivalent (after remote is set):

```bash
ssh -i ~/.ssh/inertia_vps anshul@129.121.133.25
cd /opt/Inertia2026v1 && git pull --ff-only origin main
sudo systemctl restart inertia-2026v1
```

```bash
# ON VPS
cd /opt/Inertia2026v1
grep -q '^EMAIL_SOURCE_TAG=' .env || echo 'EMAIL_SOURCE_TAG=Lean server' >> .env
# If line exists empty, set it:
sed -i 's/^EMAIL_SOURCE_TAG=.*/EMAIL_SOURCE_TAG=Lean server/' .env
grep EMAIL_SOURCE_TAG .env
grep -q '^DI_CREATE_OPS_TASKS=' .env || echo 'DI_CREATE_OPS_TASKS=false' >> .env
sed -i 's/^DI_CREATE_OPS_TASKS=.*/DI_CREATE_OPS_TASKS=false/' .env
# After deploy of advisor digests: restart app + trigger task_assignment_daily so open
# reviews/issues reassign to Client.advisor_id; then unpause review_workflow_daily_report.
sudo systemctl restart inertia-2026v1   # or your Gunicorn unit name
```

### B) Install Airflow 3 (VPS as anshul)

```bash
cd /opt/Inertia2026v1
python3 -m venv airflow_venv
# Prefer Python matching constraints (3.9–3.12). Check: python3 --version
source airflow_venv/bin/activate
pip install -U pip
# Adjust constraints file to match `python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'`
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
pip install 'apache-airflow==3.0.6' apache-airflow-providers-fab \
  --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-3.0.6/constraints-${PYVER}.txt"

export INERTIA_APP_DIR=/opt/Inertia2026v1
export AIRFLOW_HOME=/opt/Inertia2026v1/airflow
export AIRFLOW_CMD=/opt/Inertia2026v1/airflow_venv/bin/airflow
export AIRFLOW__CORE__EXECUTOR=SequentialExecutor
export AIRFLOW__CORE__DAGS_FOLDER=/opt/Inertia2026v1/airflow/dags
# Same SMTP as app (from .env) — failure mails to same users
set -a; source /opt/Inertia2026v1/.env; set +a
export AIRFLOW__SMTP__SMTP_HOST="${MAIL_SERVER:-smtp.gmail.com}"
export AIRFLOW__SMTP__SMTP_PORT="${MAIL_PORT:-587}"
export AIRFLOW__SMTP__SMTP_STARTTLS=True
export AIRFLOW__SMTP__SMTP_USER="${MAIL_USERNAME}"
export AIRFLOW__SMTP__SMTP_PASSWORD="${MAIL_PASSWORD}"
export AIRFLOW__SMTP__SMTP_MAIL_FROM="${MAIL_DEFAULT_SENDER:-$MAIL_USERNAME}"
export AIRFLOW__EMAIL__SUBJECT_TEMPLATE=/opt/Inertia2026v1/airflow/email_templates/failure_subject.jinja2
export EMAIL_SOURCE_TAG="${EMAIL_SOURCE_TAG:-Lean server}"

cd /tmp
python3 /opt/Inertia2026v1/scripts/setup_airflow.py
```

### C) systemd (Airflow 3 api-server + scheduler + dag-processor)

```bash
sudo cp /opt/Inertia2026v1/config/airflow-scheduler-lean.service /etc/systemd/system/airflow-scheduler.service
sudo cp /opt/Inertia2026v1/config/airflow-api-server-lean.service /etc/systemd/system/airflow-api-server.service
sudo cp /opt/Inertia2026v1/config/airflow-dag-processor-lean.service /etc/systemd/system/airflow-dag-processor.service
sudo systemctl daemon-reload
sudo systemctl reset-failed airflow-scheduler airflow-api-server airflow-dag-processor
sudo systemctl enable --now airflow-scheduler airflow-api-server airflow-dag-processor
sudo systemctl is-active airflow-scheduler airflow-api-server airflow-dag-processor
```

**If `status=209/STDOUT` / “Failed to set up standard output: Permission denied”:** the unit is logging to a file `anshul` cannot write. Re-copy the lean units above (they use `StandardOutput=journal`) or set journal explicitly:

```bash
sudo systemctl edit --full airflow-scheduler   # ensure StandardOutput=journal / StandardError=journal
# or simply re-cp from config/*-lean.service as above
```

SSH tunnel for UI (Mac):

```bash
ssh -i ~/.ssh/inertia_vps -L 8080:127.0.0.1:8080 anshul@129.121.133.25
# Browser: http://127.0.0.1:8080  (admin / inertia2025 — change password after first login)
```

### D) Unpause all DAGs on Lean only

```bash
export AIRFLOW_HOME=/opt/Inertia2026v1/airflow
export PATH=/opt/Inertia2026v1/airflow_venv/bin:$PATH
cd /tmp
airflow dags list
airflow dags unpause --all
# Or Airflow 3 equivalent if flag differs:
# airflow dags list -o plain | awk 'NR>1 {print $1}' | while read d; do airflow dags unpause "$d"; done
```

**Do not run unpause/pause on BigRock.**

### E) Integration smoke

```bash
cd /opt/Inertia2026v1
./venv/bin/python scripts/check_external_integrations.py
./venv/bin/python scripts/check_external_integrations.py --send-smtp anshul@equities4wealth.com
# Confirm subject starts with [Lean server]
```

Also check Settings in the app for Zoho diagnose + Google Calendar connect for the same users.

### F) Portable scripts **before** migrate + DAG health after Airflow

**Policy (agent checklist — do on Mac/lean first):** During migration we must **rewrite paths for the new host layout before moving code**. Do not copy BigRock scripts and patch on the VPS afterward.

### Path model: portable app code vs host install path

Git deploy does **not** bake “this Lean IP” into business scripts. Two layers:

| Layer | What | Portable? |
|-------|------|-----------|
| **App / DAGs / scripts** | `from main`, `_ROOT = dirname(...)`, `inertia_dag_utils.app_root()` → `INERTIA_APP_DIR` or auto from file location | Yes — works under any install dir |
| **Host wiring** | systemd units (`*-lean.service`), runbook `cd` paths, Gunicorn unit | Convention: **`/opt/Inertia2026v1`** on every prod box |

**Convention (preferred):** Keep the same directory name on every new server (`/opt/Inertia2026v1`). Then git-pulled systemd units work without editing. Same as BigRock live path — different machine, same folder name.

**If a future box uses another path** (e.g. `/opt/Inertia2027`):
1. Still git-deploy the app tree there.
2. Set `INERTIA_APP_DIR=<that path>` in systemd / env (scripts/DAGs already honour it).
3. Sed or template the `*-lean.service` files for that path (or generate units at install time) — **do not** hardcode machine-specific paths into Python entrypoints.
4. `.env` stays **out of git** (`EMAIL_SOURCE_TAG`, DB URL, Drive IDs) — set per host.

So: git syncs **portable code**; each host gets **local** `.env` + systemd path. Agent must not ship `/home/inertia` BigRock paths; it may ship `/opt/Inertia2026v1` only in install units as the agreed default.

| Do on lean (Mac) first | Do not ship to VPS |
|------------------------|--------------------|
| `from main import create_app` | `from run import create_app` |
| `_ROOT` / `os.path` / `INERTIA_APP_DIR` | `/home/inertia/app`, `/home/inertia/...` |
| DAGs via `inertia_dag_utils` → app `venv` | Imports that need Flask inside `airflow_venv` |

**Pre-deploy grep (must be clean for Airflow/cron entrypoints):**

```bash
cd /Users/anshulkhare/Downloads/Inertia2026-lean
rg -n "from run |/home/inertia" scripts/ airflow/dags/ refresh_all_holdings.py \
  daily_*.py monthly_investments.py nifty_price_update.py price_update_sheets.py 2>/dev/null || true
```

**Holdings scripts (known 2026-09-16 failure):** fix on lean, then git deploy (or `/tmp` + `sudo cp`) — `cycle_status_monitor`, `daily_holdings_processor`, `start_monthly_cycle`, `send_holdings_notifications`, `refresh_all_holdings.py`, `airflow/dags/holdings_cycle_dag.py`.

**Lesson (2026-09-16):** Piecemeal Mac→VPS `scp` left VPS with BigRock-era runners while DAGs were already loaded. Hub stays red until Airflow has a **new success** run (manual script OK ≠ Hub green).

**After Airflow is up:**

1. Prefer one **git** deploy: `bootstrap_lean_git.sh` once, then `./scripts/deployment/deploy_lean_vps.sh` (+ `--restart-airflow` when DAGs/scripts change).
2. Never scp straight into `/opt/Inertia2026v1/...` — use `/tmp` then `sudo cp`.
3. Re-trigger fixed DAGs; Hub reads latest Airflow run.
4. List DAGs whose latest run is still failed:
   ```bash
   export AIRFLOW_HOME=/opt/Inertia2026v1/airflow
   python3 <<'PY'
   import sqlite3
   con = sqlite3.connect("/opt/Inertia2026v1/airflow/airflow.db")
   rows = con.execute("""
   WITH latest AS (
     SELECT dag_id, run_id, state,
            ROW_NUMBER() OVER (PARTITION BY dag_id ORDER BY COALESCE(end_date, start_date) DESC) rn
     FROM dag_run
   )
   SELECT dag_id, state, run_id FROM latest
   WHERE rn = 1 AND lower(state) = 'failed' ORDER BY dag_id
   """).fetchall()
   print(f"failed_latest={len(rows)}")
   for r in rows: print("\t".join(r))
   PY
   ```
5. Optional bulk smoke: `bash scripts/airflow_trigger_all_dags.sh`
6. systemd: lean units with `StandardOutput=journal` (avoid `209/STDOUT`).
7. Drive backup: Shared drive + service account (My Drive quota → 403).
   - Folder/drive ID: `CODEBASE_BACKUP_DRIVE_FOLDER_ID` (Shared drive root or subfolder).
   - DB latest object: `KVM_inertia_app2025_latest.sql.gz` (`BACKUP_DRIVE_LATEST_NAME`).
   - Code latest object: `KVM_inertia_code_latest.tar.gz` (`BACKUP_DRIVE_CODE_LATEST_NAME`).
   - Local code archives: `/opt/inertia_codebase_backups/daily/` via `scripts/codebase_backup.py` (Airflow `codebase_backup_daily` Sundays).
   - First-time KVM (after Shared drive works for DB):

```bash
# ON MAC — copy backup scripts (user SSHs as anshul@187.127.188.97)
scp scripts/codebase_backup.py scripts/daily_db_backup.py \
  services/codebase_backup_service.py services/google_drive_backup_service.py \
  airflow/dags/codebase_backup_dag.py \
  anshul@187.127.188.97:/tmp/
# ON KVM
sudo cp /tmp/codebase_backup.py /tmp/daily_db_backup.py /opt/Inertia2026v1/scripts/
sudo cp /tmp/codebase_backup_service.py /tmp/google_drive_backup_service.py /opt/Inertia2026v1/services/
sudo cp /tmp/codebase_backup_dag.py /opt/Inertia2026v1/airflow/dags/
sudo mkdir -p /opt/inertia_codebase_backups && sudo chown anshul:anshul /opt/inertia_codebase_backups
cd /opt/Inertia2026v1
grep CODEBASE_BACKUP_DRIVE .env
# expect ENABLED=true and FOLDER_ID=0AAjQAmT85oT-Uk9PVA (Shared drive)
./venv/bin/python scripts/codebase_backup.py
# expect: OK inertia-code-YYYY-MM-DD.tar.gz … Drive True KVM_inertia_code_latest.tar.gz
```

**Do not** treat “manual script succeeded” as Hub-green until `airflow dags list-runs <dag_id>` shows latest `success`.

---

## Commands that worked

### Phase 1 — sudo user (template; replace after success)

```bash
# ON VPS as root
adduser anshul
passwd anshul
usermod -aG wheel anshul
mkdir -p /home/anshul/.ssh
cp /root/.ssh/authorized_keys /home/anshul/.ssh/
chown -R anshul:anshul /home/anshul/.ssh
chmod 700 /home/anshul/.ssh
chmod 600 /home/anshul/.ssh/authorized_keys
```

```bash
# ON MAC
ssh -i ~/.ssh/inertia_vps anshul@129.121.133.25
```

### Phase 3 — sshd hardening (worked 2026-09-15)

```bash
sudo tee /etc/ssh/sshd_config.d/99-hardening.conf >/dev/null <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AllowUsers anshul
EOF
sudo sshd -t && sudo systemctl reload sshd
# Mac: anshul key login OK; root password SSH → Permission denied (expected)
```

### Phase 4 — firewall (worked 2026-09-15)

```bash
sudo dnf install -y firewalld
sudo systemctl enable --now firewalld
sudo firewall-cmd --permanent --add-service=ssh
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
sudo firewall-cmd --list-all
# Result: services: cockpit dhcpv6-client http https ssh  (no mysql)
# Optional next: remove cockpit from firewall / disable cockpit.socket
```

_(Append real outputs / fixes below as you execute.)_

---

## Cutover cheat sheet

1. Smoke on `http://NEW_IP` (login, one client, one upload) — HTTP may use `SESSION_COOKIE_SECURE=false` temporarily
2. Lower DNS TTL beforehand if possible
3. A record `inertiainvest.in` → **`129.121.133.25`**
4. **`certbot` HTTPS on new VPS** (required for prod cookies)
5. **Then set prod cookie flags** (see “At DNS cutover” below) + restart `inertia-2026v1`
6. Stop `inertia-2026v1` (or old gunicorn) on BigRock
7. If failure: DNS A → old IP `66.116.199.231`

### At DNS cutover — must fix (do not leave HTTP smoke settings)

After DNS points at `129.121.133.25` and certbot succeeds:

```bash
# On new VPS /opt/Inertia2026v1/.env
SESSION_COOKIE_SECURE=true
REMEMBER_COOKIE_SECURE=true
WTF_CSRF_SSL_STRICT=true
# Optional: SERVER_NAME=inertiainvest.in

sudo systemctl restart inertia-2026v1
```

Verify `Set-Cookie` includes `Secure` on `https://inertiainvest.in/auth/login`.

**Why:** `ProductionConfig` reads these from env (lean fix). During IP-only HTTP smoke they were `false` so CSRF/login worked without TLS. Final prod must use HTTPS + Secure cookies.

Related: `config.py` `ProductionConfig`; agent skill `.cursor/skills/vps-clean-migration/SKILL.md`.

---

## Rollback

| Symptom | Action |
|---------|--------|
| New app broken after DNS | Point A record back to `66.116.199.231`, start old Gunicorn |
| Locked out of SSH | Provider web console; fix `authorized_keys` / sshd |
| DB mismatch | Keep old DB authoritative until restore verified |

---

## Session notes

### 2026-09-15

- New AlmaLinux 10 VPS created; user logged in as **root**.
- Mac key: `~/.ssh/inertia_vps` (passphrase set).
- **Public IP recorded: `129.121.133.25`.** Old prod remains `66.116.199.231` until DNS cutover.
- Next: Phase 1 — create `anshul`, copy keys, test SSH before hardening.
- **Lean app tree** created at `/Users/anshulkhare/Downloads/Inertia2026-lean` (~36 MB + ~84 MB docs): no Ollama-by-default, no secrets, junk excluded. See `LEAN_MANIFEST.md`, `RUN_FIRST.md`, **`docs/PROD_CUTOVER_COPY_LIST.md`** (what to rsync/dump from old `/opt/Inertia2026v1`).
- **Run commands (Mac + new VPS DB):** `docs/RUN_COMMANDS_LOCAL_AND_SERVER.md`. Mac agent: `.local/deployment-agent/RUN_COMMANDS.md` / `deploy_agent.py run-commands`. Local points at **existing** `inertia_app2025_dev`; new VPS gets a **new** localhost DB, prod dump later.
- Menu reorg deferred to a later phase.
- **Phase 5 note:** AlmaLinux 10 has no `mysql-server` package — use **MariaDB** (`mariadb-server` / `systemctl enable --now mariadb`). App still uses PyMySQL to `127.0.0.1`.
- Next after MariaDB active: install `python3-pip python3-devel nginx`, create empty `inertia_app2025` + `inertia_app` user.
- **2026-09-15 late:** Gunicorn `inertia-2026v1` **running**; health 200. Fix was `.env` DB URL — avoid `#`/`@` in unquoted `DB_PASSWORD`. Package via rsync/tar (no git in lean yet).
- **2026-09-16:** Replaced MariaDB with **MySQL 8.4.11** (Oracle repo; exclude mariadb*). Prod cPanel dump imported as **root** (DEFINER). 138 tables; health 200.
- **2026-09-16 night:** Login works on `http://129.121.133.25` after patching `ProductionConfig` to honour `SESSION_COOKIE_SECURE` from `.env`. Prod DB restored; nginx OK. Next: rsync uploads; at DNS+certbot flip Secure cookies to true.
- **2026-09-16 late:** Lean email tag (`EMAIL_SOURCE_TAG`), portable holdings/tax DAG paths, Airflow 3 lean systemd units, `scripts/check_external_integrations.py`. Phase 10 Airflow install commands in this runbook. BigRock DAGs left running; Lean unpauses all with same recipients.
- **2026-09-16 morning:** DAG rationalization — `data_integrity_daily` ~05:00 IST (before `daily_reports` 05:30); SLA twice daily ~05:30+15:30 IST; `daily_alert_report` aligned to 05:30; `codebase_backup_daily` → weekly Sunday. App-venv task runner for DAGs. Test: `scripts/airflow_trigger_all_dags.sh`.
- **2026-09-16 mid:** Client-wise advisor assignment for DI issues + open reviews (`Client.advisor_id`). `DI_CREATE_OPS_TASKS=false` (default) — no OpsTasks from DI; morning digests instead. `daily_alert_report` + new `review_workflow_daily_report` send per-advisor + consolidated manager/admin emails. Deploy: rsync `services/`, `agents/`, `scripts/airflow_task_runner.py`, `airflow/dags/`, `config.py`; ensure `.env` has `DI_CREATE_OPS_TASKS=false` and `EMAIL_SOURCE_TAG=Lean server`; trigger `task_assignment_daily` to reassign open reviews to client advisors.
- **2026-09-16 evening (DAG Hub):** Piecemeal scp left holdings scripts on VPS with BigRock imports; Hub showed failure until lean scripts + Airflow re-trigger. `cycle_status_monitor` + `daily_holdings_processor` → success after `/tmp`+`sudo cp` and trigger. **Next migration:** Phase 10 §F — full git deploy first; never direct scp to `/opt`; list `failed_latest` then fix/retrigger. Remaining ~11 red DAGs need per-DAG diagnosis (not assumed same as holdings).
- **SLA schedule:** `hourly_sla_check` is **twice daily** only (`0 0,10 * * *` UTC ≈ 05:30 + 15:30 IST) — not hourly. If Hub shows “02:30”, VPS still has stale `monitoring_dag.py`; redeploy that file.
- **Clone Lean → new host (same credentials):** `scripts/deployment/provision_new_host.sh` + skill `.cursor/skills/move-install-to-new-host/`. Source **Lean only** (`129.121.133.25`); copies full `.env` as-is (DB + Gmail/SMTP + Zoho + Drive + etc. — no re-keying), DB dump, uploads, Airflow, FORCE_2FA, DAG smoke. Needs `NEW_HOST` + OS/MySQL already up.
- **2026-09-22 (KVM4 Drive off-box):** Shared drive `0AAjQAmT85oT-Uk9PVA` (SA as Content manager). DB upload OK → `KVM_inertia_app2025_latest.sql.gz`. Code: `scripts/codebase_backup.py` → local `/opt/inertia_codebase_backups` + Drive `KVM_inertia_code_latest.tar.gz`. `daily_db_backup.py` now auto-uploads Drive after each dump when `CODEBASE_BACKUP_DRIVE_*` set.
- **2026-09-22 (KVM ship path):** `scripts/deployment/ship_kvm.sh` + `deploy_kvm_vps.sh` (approvals → commit → push → git pull on `187.127.188.97`). One-time: `bootstrap_lean_git.sh` on KVM with `ORIGIN_URL` → `inertialean`. See `docs/DEPLOY_WORKFLOW.md`. Never BigRock from this tree.

### Phase 1–2 — commands that worked (2026-09-15)

```bash
# as root
adduser anshul && passwd anshul && usermod -aG wheel anshul
mkdir -p /home/anshul/.ssh && cp /root/.ssh/authorized_keys /home/anshul/.ssh/
chown -R anshul:anshul /home/anshul/.ssh && chmod 700 /home/anshul/.ssh && chmod 600 /home/anshul/.ssh/authorized_keys

# Mac
ssh -i ~/.ssh/inertia_vps anshul@129.121.133.25
sudo -v
```
