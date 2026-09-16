"""
Unit tests for the Security Audit Agent.

These tests construct a temporary fake "repo" and assert that each check
fires (or does not fire) appropriately. They run without DB / Flask app.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.security_audit_agent import (
    SecurityAuditAgent,
    SEVERITY_ORDER,
    THREAT_THEMES,
    Finding,
    _CHECKS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_repo(tmp_path: Path, files: dict) -> Path:
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return tmp_path


# ---------------------------------------------------------------------------
# Smoke / registry tests
# ---------------------------------------------------------------------------

def test_checks_registry_is_populated():
    assert len(_CHECKS) >= 15, "expected at least 15 distinct security checks"
    ids = [cid for cid, _ in _CHECKS]
    assert len(ids) == len(set(ids)), "duplicate check_id registered"


def test_agent_runs_on_empty_repo(tmp_path: Path):
    agent = SecurityAuditAgent(tmp_path)
    findings = agent.run_audit()
    # Empty repo should not crash. There may be compliance findings about
    # missing AuditLog / API guards, which is expected behaviour.
    assert isinstance(findings, list)
    for f in findings:
        assert f.severity in SEVERITY_ORDER
        assert f.theme in THREAT_THEMES


def test_severity_filter_in_runner():
    """Severity values must be exactly the five documented ones."""
    assert list(SEVERITY_ORDER.keys()) == ["critical", "high", "medium", "low", "info"]


# ---------------------------------------------------------------------------
# Per-check tests
# ---------------------------------------------------------------------------

def test_detects_hardcoded_password(tmp_path: Path):
    repo = _make_repo(tmp_path, {
        "scripts/dump.py": "DB_PASSWORD = '!Nert!a2025'\n",
        "config.py": "SECRET_KEY = os.environ['SECRET_KEY']\n",
    })
    findings = SecurityAuditAgent(repo).run_audit()
    assert any(f.check_id == "SEC-EXT-001" and "dump.py" in (f.file or "") for f in findings)


def test_detects_sql_injection_fstring(tmp_path: Path):
    repo = _make_repo(tmp_path, {
        "services/bad.py": (
            "from sqlalchemy import text\n"
            "def lookup(name, conn):\n"
            "    return conn.execute(f\"SELECT * FROM users WHERE name='{name}'\")\n"
        ),
    })
    findings = SecurityAuditAgent(repo).run_audit()
    assert any(f.check_id == "SEC-EXT-002" for f in findings)


def test_detects_dangerous_eval(tmp_path: Path):
    repo = _make_repo(tmp_path, {
        "services/dangerous.py": "def run(x):\n    return eval(x)\n",
    })
    findings = SecurityAuditAgent(repo).run_audit()
    assert any(f.check_id == "SEC-EXT-003" and f.severity == "high" for f in findings)


def test_detects_jinja_safe_filter(tmp_path: Path):
    repo = _make_repo(tmp_path, {
        "templates/x.html": "<p>{{ note|safe }}</p>",
    })
    findings = SecurityAuditAgent(repo).run_audit()
    xss = [f for f in findings if f.check_id == "SEC-EXT-004"]
    assert xss, "expected SEC-EXT-004 finding for plain |safe"
    # Should aggregate per file with severity medium since not JSON-only
    assert xss[0].severity == "medium"


def test_jinja_tojson_safe_downgraded(tmp_path: Path):
    repo = _make_repo(tmp_path, {
        "templates/x.html": "<script>const d = {{ data|tojson|safe }};</script>",
    })
    findings = SecurityAuditAgent(repo).run_audit()
    xss = [f for f in findings if f.check_id == "SEC-EXT-004"]
    assert xss and xss[0].severity == "low"


def test_detects_csrf_exempt(tmp_path: Path):
    repo = _make_repo(tmp_path, {
        "routes/x.py": (
            "from flask_wtf.csrf import CSRFProtect\n"
            "csrf = CSRFProtect()\n"
            "@csrf.exempt\n"
            "def do_thing():\n    pass\n"
        ),
    })
    findings = SecurityAuditAgent(repo).run_audit()
    assert any(f.check_id == "SEC-EXT-005" for f in findings)


def test_detects_open_redirect(tmp_path: Path):
    repo = _make_repo(tmp_path, {
        "routes/x.py": (
            "from flask import redirect, request\n"
            "def go():\n    return redirect(request.args.get('next'))\n"
        ),
    })
    findings = SecurityAuditAgent(repo).run_audit()
    assert any(f.check_id == "SEC-EXT-006" for f in findings)


def test_detects_dev_secret_key(tmp_path: Path):
    repo = _make_repo(tmp_path, {
        "config.py": "SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-key-please-change-in-production'\n",
    })
    findings = SecurityAuditAgent(repo).run_audit()
    assert any(f.check_id == "SEC-EXT-007a" for f in findings)


def test_detects_file_upload_without_secure_filename(tmp_path: Path):
    repo = _make_repo(tmp_path, {
        "routes/up.py": (
            "from flask import request\n"
            "def upload():\n"
            "    f = request.files.get('doc')\n"
            "    f.save('uploads/' + f.filename)\n"
        ),
    })
    findings = SecurityAuditAgent(repo).run_audit()
    assert any(f.check_id == "SEC-EXT-009" for f in findings)


def test_detects_pii_plaintext(tmp_path: Path):
    repo = _make_repo(tmp_path, {
        "models.py": (
            "from extensions import db\n"
            "class Client(db.Model):\n"
            "    pan_number = db.Column(db.String(20))\n"
            "    bank_account = db.Column(db.String(40))\n"
        ),
    })
    findings = SecurityAuditAgent(repo).run_audit()
    ids = {f.check_id for f in findings}
    assert "SEC-INT-003" in ids


def test_pii_encrypted_skipped(tmp_path: Path):
    repo = _make_repo(tmp_path, {
        "models.py": (
            "from extensions import db\n"
            "from sqlalchemy_utils import EncryptedType\n"
            "class Client(db.Model):\n"
            "    pan_number = db.Column(EncryptedType(db.String, 'key'))\n"
        ),
    })
    findings = SecurityAuditAgent(repo).run_audit()
    pii = [f for f in findings if f.check_id == "SEC-INT-003"]
    assert not pii, "EncryptedType columns must NOT be flagged"


def test_detects_log_pii_leak(tmp_path: Path):
    repo = _make_repo(tmp_path, {
        "services/svc.py": (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def authn(pw):\n"
            "    logger.info(f'login with password={pw}')\n"
        ),
    })
    findings = SecurityAuditAgent(repo).run_audit()
    assert any(f.check_id == "SEC-INT-004" for f in findings)


def test_writes_json_and_markdown(tmp_path: Path):
    repo = _make_repo(tmp_path, {
        "config.py": "SECRET_KEY = 'dev-key-please-change-in-production'\n",
    })
    agent = SecurityAuditAgent(repo)
    findings = agent.run_audit()
    md = tmp_path / "out/report.md"
    js = tmp_path / "out/report.json"
    agent.write_markdown(findings, md)
    agent.write_json(findings, js)
    assert md.exists() and md.read_text().startswith("# Security audit (VAPT) report")
    data = json.loads(js.read_text())
    assert "findings" in data and "summary" in data
    assert data["summary"]["total"] == len(findings)


def test_ignore_file_suppresses_findings(tmp_path: Path):
    repo = _make_repo(tmp_path, {
        "config.py": "SECRET_KEY = 'dev-key-please-change-in-production'\n",
    })
    findings_default = SecurityAuditAgent(repo).run_audit()
    assert any(f.check_id == "SEC-EXT-007a" for f in findings_default)

    findings_suppressed = SecurityAuditAgent(repo, ignore_ids={"SEC-EXT-007a"}).run_audit()
    assert not any(f.check_id == "SEC-EXT-007a" for f in findings_suppressed)


def test_findings_sorted_by_severity(tmp_path: Path):
    repo = _make_repo(tmp_path, {
        "config.py": "SECRET_KEY = 'dev-key-please-change-in-production'\n",
        "templates/x.html": "<p>{{ note|safe }}</p>",
        "scripts/dump.py": "DB_PASSWORD = '!Nert!a2025'\n",
    })
    findings = SecurityAuditAgent(repo).run_audit()
    severities = [SEVERITY_ORDER[f.severity] for f in findings]
    assert severities == sorted(severities), "findings must be sorted critical -> info"
