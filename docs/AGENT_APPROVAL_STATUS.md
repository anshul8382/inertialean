# Agent approval status

Generated: 2026-09-15T17:26:08.588881+00:00

| Agent | Status | Detail |
|-------|--------|--------|
| Engineering | FAIL | see logs |
| Guideline Manager | PASS | Audit status: pass (0 findings) |
| DB cutover | PASS | registry: 1 feature(s); DEFER_DB_FEATURES=audit_log |
| SEBI regulatory | PASS | see audit_sebi_compliance.py output |
| Security (VAPT) | PARTIAL | VAPT (total    23) → docs/SECURITY_VAPT_REPORT.md |
| Security (deps) | PARTIAL | deps: 14 Python, 0 Node → docs/DEPENDENCY_AUDIT.md |
| Security (ZAP) | SKIP | set ZAP_TARGET_URL=http://<staging> to enable |
| Mobile Optimiser | PARTIAL | Capacitor shell + mobile-ui stack; Expo scaffold: yes; JWT: yes; template audit: 3 high (standalone UIs expected) |
| Delivery quality | SKIP | —skip-tests |
| Delivery quality (docs) | PASS | core docs present |
| Live architecture map | PASS | docs/ARCHITECTURE_LIVE.md (live surfaces map) |

Re-run: `python3 scripts/run_agent_approval_loop.py --write-status`