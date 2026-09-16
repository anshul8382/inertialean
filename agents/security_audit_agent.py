"""
Security Audit Agent (VAPT)
===========================

Repeatable, codebase-wide vulnerability scanner aligned with four threat themes
relevant to a SEBI-regulated investment-management platform:

  1. EXTERNAL  — attacks from the public internet (SQLi, XSS, CSRF bypass,
                 hardcoded secrets, weak crypto, open redirect, SSRF, path
                 traversal, command injection, insecure session/cookie config).
  2. INTERNAL  — risks of employee-initiated data theft (SQL query browser,
                 bulk exports without audit, missing access logging, weak
                 client-scoped access, plaintext PII columns, secrets in logs).
  3. COMPLIANCE — gaps vs. financial-data standards (encryption-at-rest for
                 PII, mandatory 2FA, audit log retention, secure cookies,
                 password policy, data masking).
  4. CONFIG    — risky runtime / config defaults (DEBUG=True paths,
                 unauthenticated webhooks, missing CSRF on state-changing
                 routes, JWT secret weakness, default credentials).

Design goals
------------
- **Standalone**: does not require a database connection or a running Flask app.
  Pure static analysis (regex + AST + targeted file inspection) so it can run
  in CI, locally, or as part of `scripts/run_agent_approval_loop.py`.
- **Repeatable**: every check has a stable `check_id` so suppression files
  (`.security_audit_ignore`) can mark accepted risks.
- **Actionable**: every finding carries a `fix` block with concrete steps,
  not just a description.
- **Preserves new features**: read-only scan; never modifies code.

Run from CLI:
    python3 scripts/run_security_audit.py
    python3 scripts/run_security_audit.py --json reports/security_latest.json
    python3 scripts/run_security_audit.py --severity high
"""
from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

THREAT_THEMES = {
    "EXTERNAL": "External attackers / public internet exposure",
    "INTERNAL": "Insider misuse / employee data theft",
    "COMPLIANCE": "SEBI / financial-data regulatory standards",
    "CONFIG": "Misconfiguration & operational hardening",
}


@dataclass
class Finding:
    check_id: str
    title: str
    severity: str           # critical | high | medium | low | info
    theme: str              # EXTERNAL | INTERNAL | COMPLIANCE | CONFIG
    category: str           # short tag (e.g. "sql_injection")
    file: Optional[str]     # repo-relative path
    line: Optional[int]
    evidence: Optional[str]
    description: str
    fix: str
    references: List[str] = field(default_factory=list)
    cwe: Optional[str] = None  # e.g. CWE-89

    def to_dict(self) -> Dict:
        return asdict(self)

    @property
    def sort_key(self):
        return (
            SEVERITY_ORDER.get(self.severity, 99),
            self.theme,
            self.check_id,
            self.file or "",
            self.line or 0,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_EXCLUDES = (
    "_deprecated",
    ".venv",
    "venv",
    "node_modules",
    "apps/mobile/node_modules",
    "backups",
    "instance",
    "logs",
    "tmp",
    "static/agreements",
    ".git",
    "airflow/logs",
)

PY_GLOBS = ("*.py",)
TEMPLATE_GLOBS = ("*.html",)


def _iter_files(root: Path, patterns: Iterable[str], excludes: Iterable[str] = DEFAULT_EXCLUDES) -> Iterable[Path]:
    excludes = tuple(excludes)
    for pat in patterns:
        for path in root.rglob(pat):
            rel = path.relative_to(root).as_posix()
            if any(rel.startswith(ex) or f"/{ex}/" in f"/{rel}" for ex in excludes):
                continue
            if path.is_file():
                yield path


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _line_of(text: str, char_index: int) -> int:
    return text.count("\n", 0, char_index) + 1


def _evidence(line_text: str, max_len: int = 160) -> str:
    s = line_text.strip()
    if len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return s


# ---------------------------------------------------------------------------
# Check registry
# ---------------------------------------------------------------------------

CheckFn = Callable[["SecurityAuditAgent"], List[Finding]]
_CHECKS: List[Tuple[str, CheckFn]] = []


def register(check_id: str):
    def deco(fn: CheckFn):
        _CHECKS.append((check_id, fn))
        return fn
    return deco


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class SecurityAuditAgent:
    """
    Static VAPT scanner. Stateless except for the configured repo root.

    Usage:
        agent = SecurityAuditAgent(root)
        findings = agent.run_audit()
        agent.write_markdown(findings, Path("docs/SECURITY_VAPT_REPORT.md"))
    """

    agent_name = "security_audit_agent"
    agent_version = "1.0.0"

    def __init__(self, root: Path, ignore_ids: Optional[Iterable[str]] = None):
        self.root = root
        self.ignore_ids = set(ignore_ids or [])
        # Lazy caches populated by helpers
        self._py_files: Optional[List[Path]] = None
        self._template_files: Optional[List[Path]] = None
        self._config_text: Optional[str] = None
        self._models_text: Optional[str] = None
        self._init_text: Optional[str] = None

    # ----- file helpers -----

    def py_files(self) -> List[Path]:
        if self._py_files is None:
            self._py_files = list(_iter_files(self.root, PY_GLOBS))
        return self._py_files

    def template_files(self) -> List[Path]:
        if self._template_files is None:
            self._template_files = list(_iter_files(self.root, TEMPLATE_GLOBS))
        return self._template_files

    def config_text(self) -> str:
        if self._config_text is None:
            self._config_text = _safe_read(self.root / "config.py")
        return self._config_text

    def models_text(self) -> str:
        if self._models_text is None:
            self._models_text = _safe_read(self.root / "models.py")
        return self._models_text

    def init_text(self) -> str:
        if self._init_text is None:
            self._init_text = _safe_read(self.root / "__init__.py")
        return self._init_text

    def relpath(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    # ----- main run -----

    def run_audit(self) -> List[Finding]:
        findings: List[Finding] = []
        for check_id, fn in _CHECKS:
            try:
                for f in fn(self) or []:
                    if f.check_id in self.ignore_ids:
                        continue
                    findings.append(f)
            except Exception as exc:  # never let one check break the whole audit
                findings.append(
                    Finding(
                        check_id=f"SEC-INTERNAL-{check_id}",
                        title=f"Check '{check_id}' raised an internal error",
                        severity="low",
                        theme="CONFIG",
                        category="agent_internal_error",
                        file=None,
                        line=None,
                        evidence=str(exc)[:200],
                        description="A security check raised an unexpected exception. The remaining checks still ran.",
                        fix="Open an issue against agents/security_audit_agent.py to harden the check.",
                    )
                )
        findings.sort(key=lambda f: f.sort_key)
        return findings

    # ----- reporting -----

    def summary(self, findings: List[Finding]) -> Dict[str, int]:
        out: Dict[str, int] = {s: 0 for s in SEVERITY_ORDER}
        for f in findings:
            out[f.severity] = out.get(f.severity, 0) + 1
        out["total"] = len(findings)
        return out

    def write_markdown(self, findings: List[Finding], path: Path) -> None:
        sev_counts = self.summary(findings)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines: List[str] = []
        lines.append("# Security audit (VAPT) report")
        lines.append("")
        lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
        lines.append(f"Agent: `{self.agent_name}` v{self.agent_version}")
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        lines.append("| Severity | Count |")
        lines.append("|----------|-------|")
        for sev in ("critical", "high", "medium", "low", "info"):
            lines.append(f"| {sev} | {sev_counts.get(sev, 0)} |")
        lines.append(f"| **Total** | **{sev_counts['total']}** |")
        lines.append("")
        lines.append("## Threat themes")
        lines.append("")
        for code, desc in THREAT_THEMES.items():
            n = sum(1 for f in findings if f.theme == code)
            lines.append(f"- **{code}** — {desc} ({n} findings)")
        lines.append("")
        if not findings:
            lines.append("No findings. Re-run after every code change to keep clean.")
        else:
            lines.append("## Findings")
            for f in findings:
                lines.append("")
                lines.append(f"### `{f.check_id}` — {f.title}")
                lines.append("")
                meta = [
                    f"**Severity:** {f.severity}",
                    f"**Theme:** {f.theme}",
                    f"**Category:** {f.category}",
                ]
                if f.cwe:
                    meta.append(f"**CWE:** {f.cwe}")
                if f.file:
                    loc = f.file + (f":{f.line}" if f.line else "")
                    meta.append(f"**Location:** `{loc}`")
                lines.append(" • ".join(meta))
                lines.append("")
                lines.append(f.description.strip())
                if f.evidence:
                    lines.append("")
                    lines.append("```")
                    lines.append(f.evidence)
                    lines.append("```")
                lines.append("")
                lines.append("**Fix:** " + f.fix.strip())
                if f.references:
                    lines.append("")
                    lines.append("References:")
                    for ref in f.references:
                        lines.append(f"- {ref}")
        lines.append("")
        lines.append("---")
        lines.append("Run again: `python3 scripts/run_security_audit.py --write-report`")
        path.write_text("\n".join(lines))

    def write_json(self, findings: List[Finding], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "agent": self.agent_name,
            "version": self.agent_version,
            "summary": self.summary(findings),
            "findings": [f.to_dict() for f in findings],
        }
        path.write_text(json.dumps(payload, indent=2))


# ===========================================================================
# Checks
# ===========================================================================

# ---- 1) EXTERNAL: hardcoded secrets ----

_SECRET_PATTERNS = [
    (re.compile(r"!Nert!a2025\$?"), "Production DB password literal"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "OpenAI-style API key"),
    (re.compile(r"\bpplx-[A-Za-z0-9]{20,}"), "Perplexity API key"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}"), "AWS access key"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}"), "GitHub personal access token"),
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"), "Private key material"),
]


@register("SEC-EXT-001")
def check_hardcoded_secrets(agent: SecurityAuditAgent) -> List[Finding]:
    out: List[Finding] = []
    skip_files = {"scripts/audit_sebi_compliance.py", "scripts/run_full_codebase_audit.py",
                  "agents/security_audit_agent.py", "scripts/run_security_audit.py"}
    for path in agent.py_files():
        rel = agent.relpath(path)
        if rel in skip_files or rel.startswith("tests/"):
            continue
        text = _safe_read(path)
        for rx, label in _SECRET_PATTERNS:
            for m in rx.finditer(text):
                line = _line_of(text, m.start())
                line_text = text.splitlines()[line - 1] if line - 1 < len(text.splitlines()) else ""
                out.append(Finding(
                    check_id="SEC-EXT-001",
                    title=f"Hardcoded credential found ({label})",
                    severity="critical",
                    theme="EXTERNAL",
                    category="hardcoded_secret",
                    file=rel,
                    line=line,
                    evidence=_evidence(line_text),
                    description=(
                        "A literal credential is checked into source. Anyone with repo "
                        "or backup access (employees, contractors, CI logs) can read it."
                    ),
                    fix=(
                        "Move the value to `.env` and read via `os.environ`. Rotate the "
                        "credential immediately since it has been exposed in git history. "
                        "Add the file to `.gitignore` if it was a secrets file."
                    ),
                    cwe="CWE-798",
                ))
    return out


# ---- 2) EXTERNAL: SQL injection via f-string in execute() ----

_SQL_FSTRING = re.compile(
    r'\.execute\s*\(\s*(?:text\s*\(\s*)?'
    r'f(?:'
    r'"[^"\n]*\{[^}\n]+\}[^"\n]*"'       # double-quoted f-string with interpolation
    r"|'[^'\n]*\{[^}\n]+\}[^'\n]*'"     # single-quoted f-string with interpolation
    r")",
    re.IGNORECASE,
)


_SELF_FILES = {"agents/security_audit_agent.py", "scripts/run_security_audit.py"}


@register("SEC-EXT-002")
def check_sql_injection(agent: SecurityAuditAgent) -> List[Finding]:
    out: List[Finding] = []
    for path in agent.py_files():
        rel = agent.relpath(path)
        if rel in _SELF_FILES:
            continue
        # Migrations and one-off scripts run by admins, but still risky
        text = _safe_read(path)
        for m in _SQL_FSTRING.finditer(text):
            line = _line_of(text, m.start())
            lines = text.splitlines()
            line_text = lines[line - 1] if line - 1 < len(lines) else ""
            out.append(Finding(
                check_id="SEC-EXT-002",
                title="Possible SQL injection: f-string passed to execute()",
                severity="high",
                theme="EXTERNAL",
                category="sql_injection",
                file=rel,
                line=line,
                evidence=_evidence(line_text),
                description=(
                    "Constructing SQL with an f-string interpolates Python expressions "
                    "directly into the query. If any value comes from user input, this is "
                    "a SQL injection."
                ),
                fix=(
                    "Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), "
                    "{'id': value})`. Never concatenate or f-string user data into SQL."
                ),
                cwe="CWE-89",
            ))
    return out


# ---- 3) EXTERNAL: dangerous eval / exec / shell=True ----

_DANGEROUS_CALLS = (
    (re.compile(r"\beval\s*\("),  "eval()", "Python expression evaluation"),
    (re.compile(r"\bexec\s*\("),  "exec()", "Dynamic code execution"),
    (re.compile(r"\bpickle\.loads?\s*\("), "pickle.loads", "Insecure deserialization"),
    (re.compile(r"\byaml\.load\s*\((?![^)]*Loader\s*=)"), "yaml.load (without SafeLoader)", "Unsafe YAML load"),
    (re.compile(r"shell\s*=\s*True"), "subprocess shell=True", "Shell command injection risk"),
    (re.compile(r"\bos\.system\s*\("), "os.system()", "Shell command injection risk"),
)


@register("SEC-EXT-003")
def check_dangerous_calls(agent: SecurityAuditAgent) -> List[Finding]:
    out: List[Finding] = []
    for path in agent.py_files():
        rel = agent.relpath(path)
        if rel in _SELF_FILES or rel.startswith("tests/"):
            continue
        text = _safe_read(path)
        for rx, label, desc in _DANGEROUS_CALLS:
            for m in rx.finditer(text):
                line = _line_of(text, m.start())
                lines = text.splitlines()
                line_text = lines[line - 1] if line - 1 < len(lines) else ""
                out.append(Finding(
                    check_id="SEC-EXT-003",
                    title=f"Dangerous call: {label}",
                    severity="high",
                    theme="EXTERNAL",
                    category="dangerous_call",
                    file=rel,
                    line=line,
                    evidence=_evidence(line_text),
                    description=f"{desc}. If any input reaches this call, remote code execution is possible.",
                    fix=(
                        "Replace with safe alternatives: use `ast.literal_eval` instead of `eval`, "
                        "`json.loads` / `yaml.safe_load` instead of `pickle`/`yaml.load`, and pass "
                        "argument lists to `subprocess.run([...], shell=False)` instead of shell strings."
                    ),
                    cwe="CWE-94",
                ))
    return out


# ---- 4) EXTERNAL: XSS — |safe and innerHTML with dynamic data ----

_SAFE_FILTER = re.compile(r"\{\{\s*[^}]+\|\s*safe\s*\}\}")
_INNER_HTML = re.compile(r"\.innerHTML\s*=\s*[^;]*\$\{|\.innerHTML\s*=\s*[a-zA-Z_]")


@register("SEC-EXT-004")
def check_xss_safe_filter(agent: SecurityAuditAgent) -> List[Finding]:
    """Aggregate per-file: report ONE finding per template, with up to 3 example lines."""
    out: List[Finding] = []
    for path in agent.template_files():
        rel = agent.relpath(path)
        text = _safe_read(path)
        matches = list(_SAFE_FILTER.finditer(text))
        if not matches:
            continue
        json_only = True
        examples: List[str] = []
        first_line = None
        flagged_count = 0
        for m in matches:
            line = _line_of(text, m.start())
            line_text = text.splitlines()[line - 1] if line - 1 < len(text.splitlines()) else ""
            prev_lines = "\n".join(text.splitlines()[max(0, line - 4): line - 1])
            if "{# SAFE:" in prev_lines:
                continue
            flagged_count += 1
            if first_line is None:
                first_line = line
            if "tojson" not in line_text and "tojson_safe" not in line_text:
                json_only = False
            if len(examples) < 3:
                examples.append(f"L{line}: {_evidence(line_text, 120)}")
        if flagged_count == 0 or first_line is None:
            continue
        sev = "low" if json_only else "medium"
        extra = f" (+{flagged_count - len(examples)} more in this file)" if flagged_count > len(examples) else ""
        out.append(Finding(
            check_id="SEC-EXT-004",
            title=f"Jinja `|safe` used in `{rel}` ({flagged_count} occurrence{'s' if flagged_count != 1 else ''})",
            severity=sev,
            theme="EXTERNAL",
            category="xss",
            file=rel,
            line=first_line,
            evidence=" | ".join(examples) + extra,
            description=(
                "`|safe` disables Jinja auto-escaping. If any rendered value originates from "
                "user-controlled input (client name, lead note, free-text fields), an attacker "
                "can inject HTML/JS. JSON-only `tojson|safe` patterns are generally fine."
            ),
            fix=(
                "Drop `|safe` and let Jinja auto-escape; for JSON-to-JS contexts use the existing "
                "`tojson_safe` filter; for trusted CMS/markdown, sanitize with "
                "`bleach.clean(value, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS)` before rendering. "
                "Document each remaining `|safe` use in this file with a `{# SAFE: reason #}` comment."
            ),
            cwe="CWE-79",
        ))
    return out


# ---- 5) EXTERNAL: CSRF exempt on state-changing routes ----

_CSRF_EXEMPT_LINE = re.compile(r"@csrf\.exempt|csrf\.exempt\(")


@register("SEC-EXT-005")
def check_csrf_exempts(agent: SecurityAuditAgent) -> List[Finding]:
    out: List[Finding] = []
    # The auth API + public webhooks are intentionally exempt; document & enforce JWT instead.
    intentional_exempts = {
        ("__init__.py", "enhanced_tax_optimiser_bp"),
        ("__init__.py", "public_contact_bp"),
        ("__init__.py", "auth_api_bp"),
        ("__init__.py", "leegality_bp"),  # HMAC mac on payload; no browser session
        ("api/v2/transactions.py", "transactions_v2_bp"),  # JWT + auth_guard guards it
    }
    for path in agent.py_files():
        rel = agent.relpath(path)
        if rel.startswith(("scripts/", "tests/", "_deprecated/")):
            continue
        text = _safe_read(path)
        for m in _CSRF_EXEMPT_LINE.finditer(text):
            line = _line_of(text, m.start())
            lines = text.splitlines()
            line_text = lines[line - 1] if line - 1 < len(lines) else ""
            # Compose a key to match intentional_exempts
            tag = (rel, line_text)
            sev = "low"
            note_intentional = False
            for irel, fragment in intentional_exempts:
                if rel == irel and fragment in line_text:
                    note_intentional = True
                    break
            if note_intentional:
                continue
            description = (
                "`csrf.exempt` removes CSRF protection. Browsers will send the user's "
                "session cookie automatically, so a forged form on another site can perform "
                "this action."
            )
            sev = "medium"
            out.append(Finding(
                check_id="SEC-EXT-005",
                title="CSRF protection disabled on a route/blueprint",
                severity=sev,
                theme="EXTERNAL",
                category="csrf_exempt",
                file=rel,
                line=line,
                evidence=_evidence(line_text),
                description=description,
                fix=(
                    "Re-enable CSRF for any session-authenticated endpoint. For API endpoints "
                    "used by mobile/JWT clients, leave exempt but ensure the route is registered "
                    "under `/api/*` and protected by `api/v1/auth_guard.enforce_v1_api_auth` (or "
                    "v2 equivalent). Add an entry to the documented allowlist."
                ),
                cwe="CWE-352",
            ))
    return out


# ---- 6) EXTERNAL: open redirect ----

_OPEN_REDIRECT = re.compile(
    r"redirect\s*\(\s*(?:request\.(?:args\.get\([^)]+\)|form\.get\([^)]+\)|referrer)"
    r"|request\.url)",
)


@register("SEC-EXT-006")
def check_open_redirect(agent: SecurityAuditAgent) -> List[Finding]:
    out: List[Finding] = []
    for path in agent.py_files():
        rel = agent.relpath(path)
        if not rel.startswith("routes/") and not rel.startswith("api/"):
            continue
        text = _safe_read(path)
        for m in _OPEN_REDIRECT.finditer(text):
            line = _line_of(text, m.start())
            lines = text.splitlines()
            line_text = lines[line - 1] if line - 1 < len(lines) else ""
            # If wrapped in normalize_internal_next, it is safe
            window = "\n".join(lines[max(0, line - 3): line + 1])
            if (
                "normalize_internal_next" in window
                or "safe_referrer_path" in window
                or "redirect_same_endpoint" in window
            ):
                continue
            out.append(Finding(
                check_id="SEC-EXT-006",
                title="Possible open redirect using request data",
                severity="medium",
                theme="EXTERNAL",
                category="open_redirect",
                file=rel,
                line=line,
                evidence=_evidence(line_text),
                description=(
                    "Redirecting to a URL taken from the request (referrer / next / args) "
                    "lets attackers craft phishing links that land on the trusted domain and "
                    "then forward victims to a malicious site."
                ),
                fix=(
                    "Wrap the value with `utils.internal_next.normalize_internal_next(...)` "
                    "(already used in `routes/auth.py`) so only relative paths within this app "
                    "are honoured."
                ),
                cwe="CWE-601",
            ))
    return out


# ---- 7) EXTERNAL: SECRET_KEY / debug / SSL hardening ----

@register("SEC-EXT-007")
def check_secret_key_and_debug(agent: SecurityAuditAgent) -> List[Finding]:
    out: List[Finding] = []
    cfg = agent.config_text()
    if "dev-key-please-change-in-production" in cfg:
        for i, line in enumerate(cfg.splitlines(), 1):
            if "dev-key-please-change-in-production" not in line:
                continue
            window = "\n".join(cfg.splitlines()[max(0, i - 20): i])
            if "class DevelopmentConfig" in window or "class TestConfig" in window:
                continue
            out.append(Finding(
                check_id="SEC-EXT-007a",
                title="SECRET_KEY falls back to a known dev string",
                severity="high",
                theme="EXTERNAL",
                category="weak_secret",
                file="config.py",
                line=i,
                evidence=_evidence(line),
                description=(
                    "If `SECRET_KEY` is not set in the environment, Flask uses a literal "
                    "string committed to the repo. An attacker who knows this string can "
                    "forge session cookies and JWTs and impersonate any user."
                ),
                fix=(
                    "Set a strong random `SECRET_KEY` in `.env` (>=32 bytes, e.g. "
                    "`python -c 'import secrets; print(secrets.token_urlsafe(48))'`) and remove "
                    "the fallback so the app refuses to boot in production without it."
                ),
                cwe="CWE-798",
            ))
            break

    # SSL verify=False
    for path in agent.py_files():
        rel = agent.relpath(path)
        if rel in _SELF_FILES or rel.startswith(("tests/",)):
            continue
        text = _safe_read(path)
        if "verify=False" in text:
            for m in re.finditer(r"verify\s*=\s*False", text):
                line = _line_of(text, m.start())
                out.append(Finding(
                    check_id="SEC-EXT-007b",
                    title="TLS certificate verification disabled",
                    severity="high",
                    theme="EXTERNAL",
                    category="tls_verify_off",
                    file=rel,
                    line=line,
                    evidence=_evidence(text.splitlines()[line - 1]),
                    description="`verify=False` allows man-in-the-middle attacks on outbound HTTPS calls.",
                    fix="Remove `verify=False`. If the target uses a private CA, pass `verify='/path/to/ca.pem'`.",
                    cwe="CWE-295",
                ))

    # debug=True in production-style entry points
    for entry in ("run.py", "run_app.py", "wsgi.py"):
        p = agent.root / entry
        if not p.exists():
            continue
        text = _safe_read(p)
        if "debug=True" not in text:
            continue
        if "127.0.0.1" in text and "development" in text:
            continue
        line = _line_of(text, text.index("debug=True"))
        out.append(Finding(
            check_id="SEC-EXT-007c",
            title=f"Flask `debug=True` in entry point `{entry}`",
            severity="medium",
            theme="CONFIG",
            category="debug_enabled",
            file=entry,
            line=line,
            evidence=_evidence(text.splitlines()[line - 1]),
            description=(
                "`debug=True` enables the Werkzeug debugger which can execute arbitrary "
                "Python via the browser if exposed."
            ),
            fix=(
                "Gate behind `if os.environ.get('FLASK_ENV') == 'development'` or run via "
                "gunicorn in production. Never deploy a `debug=True` Flask app."
            ),
            cwe="CWE-489",
        ))
    return out


# ---- 8) EXTERNAL: cookies / production HTTPS ----

@register("SEC-EXT-008")
def check_cookie_security(agent: SecurityAuditAgent) -> List[Finding]:
    out: List[Finding] = []
    cfg = agent.config_text()
    if (
        "SESSION_COOKIE_SECURE = True" in cfg
        or "_secure_cookies_default" in cfg
        or "ProductionConfig" in cfg and "SESSION_COOKIE_SECURE = True" in cfg
    ):
        return out
    if "SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'False')" in cfg:
        line = _line_of(cfg, cfg.index("SESSION_COOKIE_SECURE = os.environ.get"))
        out.append(Finding(
            check_id="SEC-EXT-008",
            title="SESSION_COOKIE_SECURE defaults to False",
            severity="high",
            theme="COMPLIANCE",
            category="insecure_cookie",
            file="config.py",
            line=line,
            evidence=_evidence(cfg.splitlines()[line - 1]),
            description=(
                "Without `Secure`, session cookies are sent over plain HTTP. On a hostile "
                "network (cafe Wi-Fi, mobile carrier proxy) an attacker can steal them and "
                "log in as the user."
            ),
            fix=(
                "In production, set `SESSION_COOKIE_SECURE=True` and `REMEMBER_COOKIE_SECURE=True` "
                "(via `.env`). Add `SESSION_COOKIE_SAMESITE='Lax'` (already set) and consider "
                "`'Strict'` for the admin app. Terminate TLS at the load balancer."
            ),
            cwe="CWE-614",
        ))
    return out


# ---- 9) EXTERNAL: file upload — secure_filename + extension allowlist ----

_UPLOAD_PATTERN = re.compile(r"request\.files")


@register("SEC-EXT-009")
def check_file_upload_hardening(agent: SecurityAuditAgent) -> List[Finding]:
    out: List[Finding] = []
    for path in agent.py_files():
        rel = agent.relpath(path)
        if not rel.startswith(("routes/", "api/")):
            continue
        text = _safe_read(path)
        if "request.files" not in text:
            continue
        if (
            "secure_filename" not in text
            and "sanitize_upload_filename" not in text
            and "save_upload_to_directory" not in text
        ):
            line = _line_of(text, text.index("request.files"))
            out.append(Finding(
                check_id="SEC-EXT-009",
                title="File upload without `secure_filename` / path normalization",
                severity="high",
                theme="EXTERNAL",
                category="path_traversal",
                file=rel,
                line=line,
                evidence=_evidence(text.splitlines()[line - 1]),
                description=(
                    "Saving an uploaded file using its original filename (`document.filename`) "
                    "allows path traversal (`../../etc/passwd`) and overwrite of arbitrary files "
                    "the process can write."
                ),
                fix=(
                    "Always wrap with `werkzeug.utils.secure_filename(name)`, validate the "
                    "extension against an allowlist (e.g. `{'.pdf', '.png', '.jpg', '.csv'}`), "
                    "and store under a UUID-based path inside a dedicated `instance/uploads/` "
                    "directory that is **not** served as static content."
                ),
                cwe="CWE-22",
            ))
    return out


# ---- 10) INTERNAL: SQL query browser exposes raw access ----

@register("SEC-INT-001")
def check_admin_sql_console(agent: SecurityAuditAgent) -> List[Finding]:
    out: List[Finding] = []
    route_paths = [
        agent.root / "routes/main.py",
        agent.root / "routes/main_sections/admin_tools.py",
    ]
    text = "\n".join(
        _safe_read(p) for p in route_paths if p.exists()
    )
    if not text.strip():
        return out
    dts = _safe_read(agent.root / "services/database_tool_service.py")
    sql_hardened = (
        "validate_readonly_select" in text
        and "log_audit_event" in dts
        and "_PII_COLUMN" in dts
        and "database_browser_preview" in dts
    )
    report_file = "routes/main_sections/admin_tools.py"
    if not (agent.root / report_file).exists():
        report_file = "routes/main.py"
    for keyword in ("sql_query", "database_browser"):
        if keyword not in text:
            continue
        if sql_hardened:
            continue
        m = re.search(rf"def\s+{re.escape(keyword)}\b", text)
        line = _line_of(text, m.start()) if m else None
        out.append(Finding(
            check_id="SEC-INT-001",
            title=f"Admin route `{keyword}` allows raw SQL / DB browsing",
            severity="high" if keyword == "database_browser" else "medium",
            theme="INTERNAL",
            category="raw_sql_console",
            file=report_file,
            line=line,
            evidence=keyword,
            description=(
                "A signed-in admin can read or modify ANY row in the database — "
                "including all client PII, transactions, and recommendations — "
                "without any per-record audit trail beyond a query log."
            ),
            fix=(
                "1) Restrict to a small named group (config `SQL_CONSOLE_USERS`). "
                "2) Force read-only (reject `INSERT/UPDATE/DELETE/DROP` server-side). "
                "3) Log every executed query to `audit_log` via `services.audit_service.log_audit_event('sql_query_run', details={'sql': ...})`. "
                "4) Consider gating behind IP allowlist + MFA step-up. "
                "5) Mask known PII columns (pan, aadhaar, bank_account) in result rendering."
            ),
            cwe="CWE-269",
        ))
        if keyword == "database_browser":
            break
    return out


# ---- 11) INTERNAL: bulk export without audit logging ----

_DOWNLOAD_FUNCS = re.compile(r"def\s+(\w*(?:export|download|csv|excel|backup)\w*)\s*\(")


@register("SEC-INT-002")
def check_bulk_export_audit(agent: SecurityAuditAgent) -> List[Finding]:
    out: List[Finding] = []
    for path in agent.py_files():
        rel = agent.relpath(path)
        if not rel.startswith(("routes/", "api/")):
            continue
        text = _safe_read(path)
        for m in _DOWNLOAD_FUNCS.finditer(text):
            func_name = m.group(1)
            if func_name.startswith("_"):
                continue
            line = _line_of(text, m.start())
            lines = text.splitlines()
            window = "\n".join(lines[line - 1: line + 80])
            if (
                "log_audit_event" in window
                or "log_data_export" in window
                or "audit_logger" in window
            ):
                continue
            out.append(Finding(
                check_id="SEC-INT-002",
                title=f"Export/download endpoint `{func_name}` is not audit-logged",
                severity="medium",
                theme="INTERNAL",
                category="missing_audit_on_export",
                file=rel,
                line=line,
                evidence=f"def {func_name}(",
                description=(
                    "Functions named export/download/csv/excel/backup typically return "
                    "client data in bulk. Without an audit log entry, an internal user "
                    "downloading the entire client base is invisible."
                ),
                fix=(
                    "Call `services.audit_service.log_audit_event('bulk_export', "
                    "resource_type='<entity>', details={'rows': n, 'filters': ...})` "
                    "before returning the response. Include `client_id` when scope is single-client."
                ),
                cwe="CWE-778",
            ))
    return out


# ---- 12) INTERNAL: PII columns stored in plaintext ----

_PII_PATTERNS = (
    ("pan_number", "PAN (Permanent Account Number)"),
    ("pan_no", "PAN"),
    ("aadhaar", "Aadhaar number"),
    ("aadhar", "Aadhaar number"),
    ("bank_account", "Bank account number"),
    ("account_number", "Bank account number"),
    ("ifsc", "IFSC code"),
)


@register("SEC-INT-003")
def check_pii_encryption(agent: SecurityAuditAgent) -> List[Finding]:
    out: List[Finding] = []
    models_text = agent.models_text()
    if not models_text:
        return out
    for col_name, label in _PII_PATTERNS:
        rx = re.compile(rf"\b{re.escape(col_name)}\s*=\s*db\.Column", re.IGNORECASE)
        for m in rx.finditer(models_text):
            line = _line_of(models_text, m.start())
            lines = models_text.splitlines()
            line_text = lines[line - 1] if line - 1 < len(lines) else ""
            if "Encrypted" in line_text or "EncryptedType" in line_text:
                continue
            out.append(Finding(
                check_id="SEC-INT-003",
                title=f"PII column `{col_name}` ({label}) stored in plaintext",
                severity="high",
                theme="COMPLIANCE",
                category="pii_plaintext",
                file="models.py",
                line=line,
                evidence=_evidence(line_text),
                description=(
                    "Sensitive identifiers (PAN/Aadhaar/bank account) sit unencrypted in MySQL. "
                    "A DB dump, replica leak, or an internal user with read access can copy them. "
                    "SEBI guidance and the DPDP Act expect strong protection of these identifiers."
                ),
                fix=(
                    "Use column-level encryption (e.g. `sqlalchemy_utils.EncryptedType` with "
                    "AES-256-GCM and a key stored in `.env` / KMS), or store only a salted hash "
                    "when only equality comparison is needed. Mask in all logs and UI export. "
                    "Restrict SELECT access via a dedicated DB user."
                ),
                cwe="CWE-312",
            ))
    return out


# ---- 13) INTERNAL: sensitive data in logs ----

_LOG_LEAK = re.compile(
    r"(?:logger|logging|print|current_app\.logger)\.(?:debug|info|warning|error|exception)\s*\("
    r"[^)]*\b(password|token|secret|api_key|access_token|pan|aadhaar|aadhar)\b",
    re.IGNORECASE,
)


@register("SEC-INT-004")
def check_log_leaks(agent: SecurityAuditAgent) -> List[Finding]:
    out: List[Finding] = []
    for path in agent.py_files():
        rel = agent.relpath(path)
        if rel.startswith(("tests/", "_deprecated/")):
            continue
        text = _safe_read(path)
        for m in _LOG_LEAK.finditer(text):
            line = _line_of(text, m.start())
            lines = text.splitlines()
            line_text = lines[line - 1] if line - 1 < len(lines) else ""
            # debug-only auth log already noisy in routes/auth.py — call out specifically
            sev = "medium"
            if "CSRF" in line_text or "csrf" in line_text:
                sev = "low"
            out.append(Finding(
                check_id="SEC-INT-004",
                title="Potential sensitive value in a log statement",
                severity=sev,
                theme="INTERNAL",
                category="log_pii_leak",
                file=rel,
                line=line,
                evidence=_evidence(line_text),
                description=(
                    "Log files can be read by internal users with shell access, shared with "
                    "third parties for debugging, or ingested by SaaS log services. Secrets "
                    "and PII inside logs are then effectively leaked."
                ),
                fix=(
                    "Never log password / token / api_key / PAN values. Log a constant marker "
                    "(e.g. `password=***`) and at most the first/last 2 characters of identifiers. "
                    "Add a redaction filter to `logging.Logger`."
                ),
                cwe="CWE-532",
            ))
    return out


# ---- 14) COMPLIANCE: mandatory 2FA enforcement ----

@register("SEC-CMP-001")
def check_mandatory_2fa(agent: SecurityAuditAgent) -> List[Finding]:
    out: List[Finding] = []
    cfg = agent.config_text()
    enforcement = (agent.root / "services/two_factor_enforcement.py").exists()
    if "FORCE_2FA_FOR_ALL_USERS" not in cfg:
        out.append(Finding(
            check_id="SEC-CMP-001",
            title="Mandatory 2FA policy flag missing",
            severity="high",
            theme="COMPLIANCE",
            category="missing_mfa_policy",
            file="config.py",
            line=None,
            evidence=None,
            description="No `FORCE_2FA_FOR_ALL_USERS` config flag found — 2FA is optional per user.",
            fix=(
                "Add `FORCE_2FA_FOR_ALL_USERS = os.environ.get('FORCE_2FA_FOR_ALL_USERS', 'true').lower() == 'true'` "
                "in `config.py` and wire a `before_request` handler that redirects users without "
                "2FA enabled to the setup screen."
            ),
            cwe="CWE-308",
        ))
    elif not enforcement:
        out.append(Finding(
            check_id="SEC-CMP-001",
            title="Mandatory 2FA flag exists but enforcement module is missing",
            severity="medium",
            theme="COMPLIANCE",
            category="mfa_enforcement_gap",
            file="services/two_factor_enforcement.py",
            line=None,
            evidence=None,
            description="Add an enforcement module + a `before_request` hook so the flag actually blocks logins without 2FA.",
            fix="Create `services/two_factor_enforcement.py` with `enforce_session_2fa()` and call it from the app `before_request`.",
        ))
    return out


# ---- 15) COMPLIANCE: audit log model + service ----

@register("SEC-CMP-002")
def check_audit_log(agent: SecurityAuditAgent) -> List[Finding]:
    has_model = (agent.root / "models/audit_log.py").exists() or "class AuditLog" in agent.models_text()
    has_service = (agent.root / "services/audit_service.py").exists()
    if has_model and has_service:
        return []
    return [Finding(
        check_id="SEC-CMP-002",
        title="DB-backed audit log not fully wired",
        severity="high",
        theme="COMPLIANCE",
        category="missing_audit_log",
        file=None,
        line=None,
        evidence=None,
        description=(
            "SEBI record-keeping requires an immutable audit trail of login, client access, "
            "and recommendation events. File-based audit trails are not sufficient on their own."
        ),
        fix=(
            "Add `models/audit_log.py` with an `AuditLog` model (user_id, action, resource_type, "
            "resource_id, client_id, ip_address, user_agent, details_json, created_at). "
            "Wrap writes in `services/audit_service.log_audit_event(...)` and call it from "
            "login, client view, recommendation publish, and bulk export."
        ),
        cwe="CWE-778",
    )]


# ---- 16) COMPLIANCE: API authentication guards ----

@register("SEC-CMP-003")
def check_api_auth_guards(agent: SecurityAuditAgent) -> List[Finding]:
    out: List[Finding] = []
    v1_guard = agent.root / "api/v1/auth_guard.py"
    v2_guard = agent.root / "api/v2/auth_guard.py"
    v1_init = _safe_read(agent.root / "api/v1/__init__.py")
    v2_init = _safe_read(agent.root / "api/v2/__init__.py")
    if not (v1_guard.exists() and "@api_v1.before_request" in v1_init):
        out.append(Finding(
            check_id="SEC-CMP-003a",
            title="Global authentication guard missing on `/api/v1/*`",
            severity="high",
            theme="EXTERNAL",
            category="missing_api_auth",
            file="api/v1/__init__.py",
            line=None,
            evidence=None,
            description="Without a blueprint-level `before_request`, any new API route that forgets `@login_required` is unauthenticated.",
            fix=(
                "Create `api/v1/auth_guard.py` exposing `enforce_v1_api_auth()` that allows the public "
                "allowlist (login, contact, webhook) and rejects everyone else with `APIResponse.error('Unauthorized', 401)`. "
                "Register `@api_v1.before_request` in `api/v1/__init__.py`."
            ),
            cwe="CWE-306",
        ))
    if not (v2_guard.exists() and "@api_v2.before_request" in v2_init):
        out.append(Finding(
            check_id="SEC-CMP-003b",
            title="Global authentication guard missing on `/api/v2/*`",
            severity="high",
            theme="EXTERNAL",
            category="missing_api_auth",
            file="api/v2/__init__.py",
            line=None,
            evidence=None,
            description="Period analysis and transactions v2 endpoints handle PII and need auth at the blueprint level.",
            fix="Mirror v1: `api/v2/auth_guard.py` + `@api_v2.before_request`. Allowlist only `/transactions/health`.",
            cwe="CWE-306",
        ))
    return out


# ---- 17) COMPLIANCE: data retention / backup hygiene ----

@register("SEC-CMP-004")
def check_backup_in_repo(agent: SecurityAuditAgent) -> List[Finding]:
    out: List[Finding] = []
    suspicious_files = []
    skip_prefixes = (
        "_deprecated/",
        ".venv/",
        "venv/",
        "node_modules/",
        "migrations/",
        "scripts/migration/",
    )
    skip_names = {"scripts/local_mysql_bootstrap.sql"}
    for pat in ("*.sql", "*.sql.gz", "*.dump", "*.bak"):
        for p in agent.root.rglob(pat):
            rel = p.relative_to(agent.root).as_posix()
            if any(rel.startswith(pfx) for pfx in skip_prefixes):
                continue
            if rel in skip_names:
                continue
            if "scripts/sql_queries/" in rel:
                continue
            base = p.name.lower()
            if pat == "*.sql" and not (
                "backup" in base
                or base.startswith("query_")
                or rel.endswith(".sql.gz")
            ):
                continue
            suspicious_files.append(rel)
    if not suspicious_files:
        return out
    for rel in suspicious_files[:10]:
        out.append(Finding(
            check_id="SEC-CMP-004",
            title=f"Database backup file in working tree: `{rel}`",
            severity="medium",
            theme="INTERNAL",
            category="backup_in_tree",
            file=rel,
            line=None,
            evidence=None,
            description=(
                "SQL dumps inside the repo or working tree get copied to laptops, "
                "containers, and CI artifacts — multiplying the blast radius of a breach."
            ),
            fix=(
                "Move backups to an off-host bucket (S3 / GCS) with object-lock + server-side "
                "encryption. Keep `backups/` in `.gitignore`. Enforce 30-day rotation via "
                "`scripts/cleanup_local_disk.sh` (already provided) or scheduled job."
            ),
        ))
    return out


# ---- 18) CONFIG: 0.0.0.0 bind without firewall note ----

@register("SEC-CFG-001")
def check_bind_all_interfaces(agent: SecurityAuditAgent) -> List[Finding]:
    out: List[Finding] = []
    for entry in ("run.py", "run_app.py"):
        p = agent.root / entry
        if not p.exists():
            continue
        text = _safe_read(p)
        if "0.0.0.0" in text:
            line = _line_of(text, text.index("0.0.0.0"))
            out.append(Finding(
                check_id="SEC-CFG-001",
                title=f"App binds to 0.0.0.0 in `{entry}` (dev runner)",
                severity="low",
                theme="CONFIG",
                category="bind_all",
                file=entry,
                line=line,
                evidence=_evidence(text.splitlines()[line - 1]),
                description=(
                    "Listening on every interface during development exposes the dev server to "
                    "the LAN. With `debug=True`, this is exploitable as RCE via the Werkzeug debugger."
                ),
                fix=(
                    "Bind to `127.0.0.1` for local development, or run via gunicorn behind a reverse "
                    "proxy. Production uses gunicorn — only the dev scripts are affected."
                ),
            ))
    return out


# ---- 19) CONFIG: JWT secret length ----

@register("SEC-CFG-002")
def check_jwt_strength(agent: SecurityAuditAgent) -> List[Finding]:
    jwt_path = agent.root / "services/jwt_service.py"
    if not jwt_path.exists():
        return [Finding(
            check_id="SEC-CFG-002",
            title="JWT service missing — mobile auth not implemented",
            severity="medium",
            theme="CONFIG",
            category="missing_jwt",
            file="services/jwt_service.py",
            line=None,
            evidence=None,
            description="Mobile / API clients need short-lived signed tokens.",
            fix="Add `services/jwt_service.py` exposing `issue_access_token` / `decode_access_token` using PyJWT HS256.",
        )]
    text = _safe_read(jwt_path)
    out: List[Finding] = []
    if "Config.SECRET_KEY" in text and "32" not in text:
        out.append(Finding(
            check_id="SEC-CFG-002",
            title="JWT signed with SECRET_KEY; verify key strength",
            severity="low",
            theme="CONFIG",
            category="jwt_weak_key",
            file="services/jwt_service.py",
            line=None,
            evidence=None,
            description=(
                "PyJWT warns when the HMAC key is shorter than 32 bytes for SHA-256. "
                "Short keys can be brute-forced offline if a token is leaked."
            ),
            fix=(
                "Generate `SECRET_KEY` >= 48 bytes (`python -c 'import secrets; print(secrets.token_urlsafe(48))'`). "
                "Consider a dedicated `JWT_SECRET` separate from Flask `SECRET_KEY` so rotation is independent."
            ),
        ))
    return out


# ---- 20) CONFIG: unauthenticated webhook signature ----

@register("SEC-CFG-003")
def check_webhook_signature(agent: SecurityAuditAgent) -> List[Finding]:
    wa = agent.root / "api/v1/whatsapp.py"
    if not wa.exists():
        return []
    text = _safe_read(wa)
    if "_verify_whatsapp_signature" in text and "compare_digest" in text:
        return []
    return [Finding(
        check_id="SEC-CFG-003",
        title="WhatsApp webhook does not verify HMAC signature",
        severity="medium",
        theme="EXTERNAL",
        category="missing_webhook_signature",
        file="api/v1/whatsapp.py",
        line=None,
        evidence=None,
        description=(
            "Meta signs every webhook with `X-Hub-Signature-256`. Without verification, "
            "anyone who learns the public webhook URL can post fake events (status updates, "
            "inbound messages, opt-outs)."
        ),
        fix=(
            "Compute `hmac.new(WHATSAPP_APP_SECRET.encode(), request.data, hashlib.sha256).hexdigest()` "
            "and compare with the request header using `hmac.compare_digest` before processing."
        ),
        cwe="CWE-345",
    )]


# ---- 21) CONFIG: dependencies pinned ----

@register("SEC-CFG-004")
def check_requirements_pinned(agent: SecurityAuditAgent) -> List[Finding]:
    req = agent.root / "requirements.txt"
    if not req.exists():
        return []
    out: List[Finding] = []
    text = req.read_text(encoding="utf-8", errors="ignore")
    unpinned = []
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Allow >= for our own additions (PyJWT, email_validator) but flag unbounded ones
        if "==" not in line and ">=" not in line and "~=" not in line:
            unpinned.append((i, line))
    for i, line in unpinned[:8]:
        out.append(Finding(
            check_id="SEC-CFG-004",
            title=f"Dependency not pinned: `{line}`",
            severity="low",
            theme="CONFIG",
            category="unpinned_dependency",
            file="requirements.txt",
            line=i,
            evidence=line,
            description=(
                "Unpinned dependencies make builds non-reproducible and let a compromised "
                "upstream silently push malicious code on the next `pip install`."
            ),
            fix=(
                "Pin with `==<version>`. Generate a reproducible lock with `pip freeze > requirements-lock.txt` "
                "or migrate to `uv` / `pip-tools`. Run `pip-audit` weekly."
            ),
            cwe="CWE-1357",
        ))
    return out


# ---- 22) COMPLIANCE: session lifetime ----

@register("SEC-CMP-005")
def check_session_lifetime(agent: SecurityAuditAgent) -> List[Finding]:
    cfg = agent.config_text()
    m = re.search(r"PERMANENT_SESSION_LIFETIME\s*=\s*timedelta\((\w+)\s*=\s*(\d+)\)", cfg)
    if not m:
        return []
    unit, value = m.group(1), int(m.group(2))
    minutes = {"seconds": value // 60, "minutes": value, "hours": value * 60, "days": value * 60 * 24}.get(unit, 0)
    if minutes <= 240:  # <= 4 hours is reasonable
        return []
    return [Finding(
        check_id="SEC-CMP-005",
        title=f"Session lifetime is {value} {unit} (long for financial data)",
        severity="low",
        theme="COMPLIANCE",
        category="long_session",
        file="config.py",
        line=_line_of(cfg, m.start()),
        evidence=_evidence(cfg.splitlines()[_line_of(cfg, m.start()) - 1]),
        description="Long sessions on shared / lost devices increase the window for unauthorized access.",
        fix="Set to `timedelta(hours=2)` (default) or shorter for advisor accounts. Force re-auth for admin actions.",
    )]


# ---- 23) INTERNAL: missing client_access_required on client-scoped routes ----

_SCOPE_ALLOW_PATTERNS = (
    "client_access_required",
    "can_access_client",
    "get_accessible_clients",
    "verify_client_access",
    "_check_client_access",
    "current_user.is_admin",
    "current_user.has_role",
)


@register("SEC-INT-005")
def check_client_access_decorator(agent: SecurityAuditAgent) -> List[Finding]:
    """Aggregate per file: 1 finding per route file with up to 3 example offending lines."""
    out: List[Finding] = []
    routes_dir = agent.root / "routes"
    if not routes_dir.is_dir():
        return out
    route_re = re.compile(r"@\w+\.route\([^)]*<int:client_id>[^)]*\)")
    for path in routes_dir.glob("*.py"):
        text = _safe_read(path)
        rel = agent.relpath(path)
        lines = text.splitlines()
        # File-level allowance: blueprint before_request enforces client access
        if "enforce_client_id_from_view_args" in text:
            continue
        file_has_before_request_guard = any(
            ("before_request" in ln and "enforce_client" in text)
            for ln in lines
        )
        if file_has_before_request_guard:
            continue
        offenders: List[Tuple[int, str]] = []
        for m in route_re.finditer(text):
            line = _line_of(text, m.start())
            # widen window to next 30 lines to find access check anywhere in the function body
            window = "\n".join(lines[max(0, line - 1): line + 30])
            if any(p in window for p in _SCOPE_ALLOW_PATTERNS):
                continue
            offenders.append((line, _evidence(lines[line - 1], 120)))
        if not offenders:
            continue
        examples = "; ".join(f"L{l}: {t}" for l, t in offenders[:3])
        more = f" (+{len(offenders) - 3} more)" if len(offenders) > 3 else ""
        out.append(Finding(
            check_id="SEC-INT-005",
            title=f"`{rel}` has {len(offenders)} client-scoped route(s) without an explicit access check",
            severity="medium",
            theme="INTERNAL",
            category="missing_client_scope",
            file=rel,
            line=offenders[0][0],
            evidence=examples + more,
            description=(
                "Routes that take `<int:client_id>` should verify the current user can see that "
                "client. Without it, an advisor can read other advisors' clients by guessing IDs."
            ),
            fix=(
                "Add `@client_access_required` (from `access_control`) to each route, OR add a "
                "`before_request` hook on this blueprint that calls `can_access_client(client_id)` "
                "and aborts 403 if not. Also call "
                "`services.audit_service.log_audit_event('client_view', client_id=client_id)` so "
                "internal access is recorded for SEBI."
            ),
            cwe="CWE-639",
        ))
    return out


# ---- 24) COMPLIANCE: password policy ----

@register("SEC-CMP-006")
def check_password_policy(agent: SecurityAuditAgent) -> List[Finding]:
    # Reset path
    text = _safe_read(agent.root / "routes/auth.py")
    if "validate_password" in text and "password_policy" in text:
        return []
    if "len(new_password) < 6" in text:
        line = _line_of(text, text.index("len(new_password) < 6"))
        return [Finding(
            check_id="SEC-CMP-006",
            title="Weak password policy: minimum length is 6",
            severity="medium",
            theme="COMPLIANCE",
            category="weak_password_policy",
            file="routes/auth.py",
            line=line,
            evidence=_evidence(text.splitlines()[line - 1]),
            description="Modern guidance (NIST SP 800-63B, RBI/SEBI cyber security advisories) is >=12 characters with no composition rules.",
            fix=(
                "Raise minimum to **12 characters**; allow any printable characters; check against "
                "Have-I-Been-Pwned k-anonymity API (or local breach list); enforce no reuse of the last 5 passwords."
            ),
            cwe="CWE-521",
        )]
    return []


# ---------------------------------------------------------------------------
# Public convenience
# ---------------------------------------------------------------------------

def run(root: Optional[Path] = None) -> List[Finding]:
    """Convenience: run a one-shot audit from the given repo root (defaults to repo of this file)."""
    base = root or Path(__file__).resolve().parents[1]
    agent = SecurityAuditAgent(base)
    return agent.run_audit()


__all__ = [
    "SecurityAuditAgent",
    "Finding",
    "THREAT_THEMES",
    "SEVERITY_ORDER",
    "run",
]
