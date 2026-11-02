# Agent approval status

Generated: 2026-11-02T11:43:54.150924+00:00

| Agent | Status | Detail |
|-------|--------|--------|
| Engineering | PASS | create_app + agents/registry.py |
| Guideline Manager | PASS | Audit status: pass (1 findings) |
| DB cutover | PARTIAL | registry: 5 feature(s); no deferrals (cutover may be done) |
| SEBI regulatory | PASS | see audit_sebi_compliance.py output |
| Security (VAPT) | PARTIAL | VAPT (total    55) → docs/SECURITY_VAPT_REPORT.md |
| Security (deps) | PASS | deps: 0 Python, 0 Node → docs/DEPENDENCY_AUDIT.md |
| Security (ZAP) | SKIP | set ZAP_TARGET_URL=http://<staging> to enable |
| Mobile Optimiser | PARTIAL | Capacitor shell + mobile-ui stack; Expo scaffold: no; JWT: yes; template audit: 3 high (standalone UIs expected) |
| Delivery quality | PASS | pytest exit 0 |
| Delivery quality (functional) | PASS | pytest functional exit 0 |
| Delivery quality (functional packs) | PASS | exit 0 |
| Delivery quality (docs) | PASS | core docs present |
| Live architecture map | PASS | docs/ARCHITECTURE_LIVE.md (live surfaces map) |

Re-run: `python3 scripts/run_agent_approval_loop.py --write-status`