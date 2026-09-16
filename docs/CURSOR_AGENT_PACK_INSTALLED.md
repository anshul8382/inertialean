# Cursor agent pack — installed locally

Installed from `~/Downloads/cursor-agent-pack` into this repository.

## What was copied

- `.cursor/rules/*.mdc` — 12 expert-agent rules (merged with 5 existing app rules → **17** total)
- `docs/DEVELOP_WORKFLOW.md`
- `docs/SEBI_COMPLIANCE_CHECKLIST.md`
- `docs/SEBI_PROCESS_INSTRUCTIONS.md`
- `docs/regulatory/INDEX.md`, `README.md`
- `docs/FUNCTIONAL_REQUIREMENTS_DOCUMENT.md` — FRD index stub (created for BA/QA rules)
- Root `AGENTS.md` — merged project map + expert-agent index

## Optional (not installed)

| Item | Notes |
|------|--------|
| `scripts/audit_sebi_compliance.py` | Referenced by SEBI Auditor rule; add from server if available |
| `docs/ARCHITECTURE_MODULES_AND_ALIGNMENT.md` | Referenced by `architect.mdc`; create when using Architect agent heavily |

## Re-install from pack

```bash
PACK=~/Downloads/cursor-agent-pack
APP="/Users/anshulkhare/Downloads/app 2"
cp "$PACK/.cursor/rules/"* "$APP/.cursor/rules/"
cp "$PACK/docs/DEVELOP_WORKFLOW.md" "$PACK/docs/SEBI_"*.md "$APP/docs/"
cp "$PACK/docs/regulatory/"* "$APP/docs/regulatory/"
```

Then merge any changes into `AGENTS.md` manually.

Original pack instructions: `~/Downloads/cursor-agent-pack/INSTALL.md`
