# Dependency vulnerability audit

Generated: 2026-09-25T12:40:20.116507+00:00
Python runtime: 3.11.9
Scanners: `pip-audit` (Python) + `npm audit --omit=dev` (Capacitor shell)

## Summary

| Source | Available | Findings |
|--------|-----------|----------|
| Python (`requirements.txt`) | no | 0 |
| Capacitor shell (`apps/capacitor-shell/`) | yes | 0 |

## Python (`pip-audit`)

_Skipped: pip-audit not installed (run: python3 -m pip install pip-audit)_

## Capacitor shell (`npm audit`)

No known vulnerabilities in production dependencies.

---

**Triage:** upgrade critical/high vulnerabilities in a focused PR. Run the app's full pytest suite before deploy.

## Python version note

This audit ran on Python **3.11.9**. Several upstream advisories ship the fix only in
package versions that require **Python 3.10+** (Pillow 12.x, urllib3 2.7+, requests 2.33+,
python-dotenv 1.2.2+, weasyprint 68+). `requirements.txt` uses conditional pins so a Python
3.10+ environment (recommended for production) installs the clean versions automatically.
Local dev on Python 3.9 will show these as residual findings.

Re-run:
```bash
python3 scripts/run_dependency_audit.py --write-report
```
