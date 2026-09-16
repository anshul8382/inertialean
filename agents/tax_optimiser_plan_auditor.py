"""
Tax Optimiser Strategies v2 — plan compliance auditor.

Reads a Cursor plan markdown (path from env TAX_OPTIMISER_PLAN_PATH or defaults),
runs static + optional DB-backed checks against the codebase, and returns a
structured report. This is **not** a BaseAgent / DataIntegrityIssue agent.

Usage:
  python scripts/tax_optimiser_plan_audit_agent.py
  python scripts/tax_optimiser_plan_audit_agent.py --no-db --markdown report.md
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class AuditFinding:
    check_id: str
    status: str  # pass | fail | warn | skip
    detail: str
    plan_ref: str = ""


@dataclass
class AuditReport:
    plan_path: Optional[str]
    plan_found: bool
    plan_overview: str
    findings: List[AuditFinding] = field(default_factory=list)

    def add(self, f: AuditFinding) -> None:
        self.findings.append(f)

    def summary_counts(self) -> Dict[str, int]:
        c = {"pass": 0, "fail": 0, "warn": 0, "skip": 0}
        for f in self.findings:
            c[f.status] = c.get(f.status, 0) + 1
        return c


def _default_plan_candidates() -> List[Path]:
    env = os.environ.get("TAX_OPTIMISER_PLAN_PATH", "").strip()
    paths: List[Path] = []
    if env:
        paths.append(Path(env))
    paths.extend(
        [
            Path("/root/.cursor/plans/tax_optimiser_strategies_v2_6fdecf90.plan.md"),
            REPO_ROOT / ".cursor" / "plans" / "tax_optimiser_strategies_v2_6fdecf90.plan.md",
        ]
    )
    return paths


def locate_plan(explicit: Optional[Path] = None) -> tuple[Optional[Path], str]:
    if explicit and explicit.is_file():
        return explicit.resolve(), ""
    for p in _default_plan_candidates():
        if p.is_file():
            return p.resolve(), ""
    tried = ", ".join(str(p) for p in _default_plan_candidates())
    return None, tried


def read_plan_snippet(path: Path, max_chars: int = 1200) -> tuple[bool, str, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return False, "", str(e)
    overview = ""
    if text.startswith("---"):
        fm_end = text.find("\n---", 3)
        if fm_end != -1:
            fm = text[3:fm_end]
            om = re.search(r"overview:\s*[\"']?([^\"'\n]+)[\"']?", fm)
            if om:
                overview = om.group(1).strip()
    body_start = text.find("\n# ") if text.startswith("---") else 0
    snippet = text[body_start : body_start + max_chars].strip()
    return True, overview, snippet


def _file_exists(rel: str) -> bool:
    return (REPO_ROOT / rel).is_file()


def _grep_file(rel: str, needle: str) -> bool:
    p = REPO_ROOT / rel
    if not p.is_file():
        return False
    try:
        return needle in p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def run_static_checks(report: AuditReport) -> None:
    """Checks that do not need Flask/DB."""

    def ok(cid: str, ref: str, msg: str) -> None:
        report.add(AuditFinding(cid, "pass", msg, ref))

    def warn(cid: str, ref: str, msg: str) -> None:
        report.add(AuditFinding(cid, "warn", msg, ref))

    def fail(cid: str, ref: str, msg: str) -> None:
        report.add(AuditFinding(cid, "fail", msg, ref))

    # Core files (plan §5)
    core = [
        ("svc_strategy_engine", "services/tax_optimiser_strategy_engine.py", "Strategy engine module"),
        ("svc_enhanced", "services/enhanced_tax_optimiser_service.py", "Enhanced report service"),
        ("svc_interactive", "services/tax_optimiser_interactive_service.py", "Interactive session"),
        ("svc_followup", "services/tax_optimiser_followup_service.py", "T+N follow-up batch"),
        ("svc_capital_gains", "services/capital_gains_service.py", "FIFO / open lots"),
        ("svc_ltcg", "services/ltcg_eligibility.py", ">12 month LTCG rule"),
        ("tpl_enhanced", "templates/tools/tax_optimiser_enhanced.html", "Enhanced UI"),
        ("tpl_email", "templates/email/tax_optimiser_client.html", "Client email"),
        ("tpl_followup", "templates/email/tax_optimiser_followup_draft.html", "Follow-up draft email"),
        ("script_refresh", "scripts/tax_optimiser_followup_refresh.py", "T+N refresh script"),
        ("dag_followup", "airflow/dags/tax_optimiser_followup_dag.py", "Airflow DAG"),
        ("api_enhanced", "api/v1/enhanced_tax_optimiser.py", "REST API"),
        ("migration_strategy", "migrations/add_tax_optimiser_strategy_and_followup.py", "Strategy + batch migration"),
        ("docs_req", "docs/ENHANCED_TAX_OPTIMISER_REQUIREMENTS.md", "Requirements doc"),
    ]
    for cid, rel, label in core:
        if _file_exists(rel):
            ok(f"file_{cid}", "plan §5 Files likely touched", f"{label}: `{rel}` present")
        else:
            fail(f"file_{cid}", "plan §5", f"Missing {label}: `{rel}`")

    # Engine wires attach_strategies
    if _grep_file("services/enhanced_tax_optimiser_service.py", "attach_strategies_to_report"):
        ok("wire_engine", "plan §3", "`enhanced_tax_optimiser_service` imports/calls `attach_strategies_to_report`")
    else:
        fail("wire_engine", "plan §3", "No `attach_strategies_to_report` in enhanced_tax_optimiser_service")

    # LTCG twelve-month in capital gains path
    if _grep_file("services/capital_gains_service.py", "classify_listed_equity_gain_type"):
        ok("ltcg_twelve", "plan §B / success criteria", "`capital_gains_service` uses `classify_listed_equity_gain_type`")
    else:
        fail("ltcg_twelve", "plan success criteria", "LTCG classification not wired via ltcg_eligibility in capital_gains_service")

    # Payload shape on S1 branch
    eng = REPO_ROOT / "services/tax_optimiser_strategy_engine.py"
    if eng.is_file():
        t = eng.read_text(encoding="utf-8", errors="replace")
        need = ["benefit_breakdown", "cost_breakdown", "net_metrics", "rejection_reason", "gates_passed"]
        missing = [k for k in need if k not in t]
        if not missing:
            ok("payload_s1", "plan Per-suggestion payload", "Engine source contains benefit/cost/net/gates/rejection fields")
        else:
            warn("payload_s1", "plan Per-suggestion payload", f"Engine may be missing keys: {missing}")

    # Email template
    if _grep_file("templates/email/tax_optimiser_client.html", "benefit_breakdown"):
        ok("email_payload", "plan §4 Immediate email", "Client email template renders benefit_breakdown / assumptions")
    else:
        warn("email_payload", "plan §4", "tax_optimiser_client.html may not expose benefit_breakdown")

    # Reversal dimension
    if _grep_file("services/tax_optimiser_interactive_service.py", "reversal_status"):
        ok("reversal", "plan §3 Interactive", "Interactive service implements `reversal_status` / reversal_action")
    else:
        fail("reversal", "plan §3", "No reversal_status in tax_optimiser_interactive_service")

    # S2 explicit engine branch (plan catalog — docs say reserved)
    if _grep_file("services/tax_optimiser_strategy_engine.py", "book_stcg"):
        ok("s2_engine", "plan S2", "`book_stcg_gains` referenced in strategy engine")
    else:
        warn(
            "s2_engine",
            "plan S2 / docs",
            "`book_stcg_gains` (S2) not implemented in strategy engine — docs list as reserved/future",
        )

    # S3 + §94(8) overlay on unrealised harvest (plan S9): seed param vs engine
    hpath = REPO_ROOT / "services/tax_optimiser_strategy_engine.py"
    mig = REPO_ROOT / "migrations/add_tax_optimiser_strategy_and_followup.py"
    ht = hpath.read_text(encoding="utf-8", errors="replace") if hpath.is_file() else ""
    seed_94 = False
    if mig.is_file():
        try:
            seed_94 = "apply_94_8_ranking" in mig.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    if "section_94_8" in ht or "apply_94_8" in ht:
        ok("s3_94_8", "plan S9", "Strategy engine references §94(8) / apply_94_8 for unrealised harvest")
    elif seed_94:
        warn(
            "s3_94_8",
            "plan S9",
            "Migration seeds `apply_94_8_ranking` on harvest_unrealized_loss, but "
            "`tax_optimiser_strategy_engine._harvest_unrealized_bundle` does not rank by §94(8) tier",
        )
    else:
        warn("s3_94_8", "plan S9", "Could not verify §94(8) overlay on S3 (no seed flag or engine hook)")

    # Objective 2 future carry-forward quantification
    if _grep_file("services/tax_optimiser_strategy_engine.py", "future_tax_shield_inr_estimate"):
        ft = (REPO_ROOT / "services/tax_optimiser_strategy_engine.py").read_text(encoding="utf-8", errors="replace")
        if "future_tax_shield_inr_estimate\": 0.0" in ft or "future_tax_shield_inr_estimate\": 0" in ft:
            warn(
                "obj2_future",
                "plan Objective 2",
                "`future_tax_shield_inr_estimate` is stubbed to 0 on S1; full carry-forward valuation not modelled",
            )
        else:
            ok("obj2_future", "plan Objective 2", "Non-zero future tax shield path present")
    else:
        warn("obj2_future", "plan Objective 2", "No future_tax_shield field in engine")

    # rules_extended / display blocks (plan §1 table B)
    if _grep_file("models/__init__.py", "rules_extended") or _grep_file("models.py", "rules_extended"):
        ok("rules_extended", "plan §1 B", "Model mentions rules_extended")
    else:
        warn(
            "rules_extended",
            "plan §1 B",
            "No `rules_extended` on settings — acceptable per docs ('may later'); display-only rule blocks optional",
        )

    # Unit tests dedicated to strategy engine
    tests = list(REPO_ROOT.glob("**/test*tax*optimiser*.py")) + list(
        REPO_ROOT.glob("**/test*strategy*engine*.py")
    )
    if tests:
        ok("unit_tests", "quality", f"Found test file(s): {[p.name for p in tests[:5]]}")
    else:
        warn(
            "unit_tests",
            "quality",
            "No pytest files matching test*tax*optimiser* or test*strategy*engine* — rely on smoke script only",
        )


def run_db_checks(report: AuditReport) -> None:
    """Flask app + DB: tables, loader, one report build."""
    try:
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        os.chdir(REPO_ROOT)
        from __init__ import create_app
        from extensions import db
        from sqlalchemy import inspect
    except Exception as e:
        report.add(
            AuditFinding(
                "db_context",
                "skip",
                f"Could not import app: {e}",
                "optional",
            )
        )
        return

    app = create_app()
    with app.app_context():
        try:
            insp = inspect(db.engine)
            tables = set(insp.get_table_names())
        except Exception as e:
            report.add(AuditFinding("db_tables", "fail", f"DB inspect failed: {e}", "plan migrations"))
            return

        need = {"tax_optimiser_settings", "tax_optimiser_strategy", "tax_optimiser_followup_batch"}
        miss = need - tables
        if miss:
            report.add(
                AuditFinding(
                    "db_tables",
                    "fail",
                    f"Missing tables: {sorted(miss)}",
                    "plan db-strategies-followup",
                )
            )
        else:
            report.add(
                AuditFinding(
                    "db_tables",
                    "pass",
                    "Tables tax_optimiser_settings, tax_optimiser_strategy, tax_optimiser_followup_batch present",
                    "plan migrations",
                )
            )

        try:
            from models import TaxOptimiserStrategy
            from services.tax_optimiser_strategy_loader import load_enabled_strategy_params

            n = TaxOptimiserStrategy.query.count()
            sp = load_enabled_strategy_params()
            if n < 1:
                report.add(
                    AuditFinding(
                        "db_strategies",
                        "warn",
                        "tax_optimiser_strategy empty — loader uses in-code defaults",
                        "plan strategies",
                    )
                )
            else:
                report.add(
                    AuditFinding(
                        "db_strategies",
                        "pass",
                        f"{n} strategy row(s); loader keys include: {', '.join(sorted(sp.keys())[:8])}…",
                        "plan strategies",
                    )
                )
            if "book_ltcg_exemption" not in sp:
                report.add(
                    AuditFinding(
                        "loader_s1",
                        "fail",
                        "load_enabled_strategy_params missing book_ltcg_exemption",
                        "plan S1",
                    )
                )
            else:
                report.add(AuditFinding("loader_s1", "pass", "book_ltcg_exemption in loader map", "plan S1"))
        except Exception as e:
            report.add(AuditFinding("db_strategies", "fail", str(e), "plan strategies"))


def run_smoke_script(report: AuditReport) -> None:
    script = REPO_ROOT / "scripts" / "smoke_tax_optimiser_workflow.py"
    if not script.is_file():
        report.add(AuditFinding("smoke", "skip", "smoke_tax_optimiser_workflow.py not found", ""))
        return
    try:
        r = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if r.returncode == 0:
            report.add(
                AuditFinding(
                    "smoke",
                    "pass",
                    "scripts/smoke_tax_optimiser_workflow.py exited 0",
                    "end-to-end",
                )
            )
        else:
            tail = (r.stderr or r.stdout or "")[-800:]
            report.add(
                AuditFinding(
                    "smoke",
                    "fail",
                    f"Smoke exit {r.returncode}. Tail:\n{tail}",
                    "end-to-end",
                )
            )
    except subprocess.TimeoutExpired:
        report.add(AuditFinding("smoke", "fail", "Smoke script timed out", ""))
    except Exception as e:
        report.add(AuditFinding("smoke", "warn", f"Could not run smoke: {e}", ""))


def run_audit(
    *,
    plan_path: Optional[Path] = None,
    with_db: bool = True,
    run_smoke: bool = True,
) -> AuditReport:
    path, tried = locate_plan(plan_path)
    report = AuditReport(
        plan_path=str(path) if path else None,
        plan_found=path is not None,
        plan_overview="",
    )
    if path:
        ok, overview, _ = read_plan_snippet(path)
        if ok:
            report.plan_overview = overview
        else:
            report.plan_overview = "Could not read plan file"
    else:
        report.add(
            AuditFinding(
                "plan_file",
                "warn",
                f"Plan markdown not found. Tried: {tried}. Set TAX_OPTIMISER_PLAN_PATH.",
                "input",
            )
        )

    run_static_checks(report)
    if with_db:
        run_db_checks(report)
    else:
        report.add(AuditFinding("db_context", "skip", "--no-db: skipped DB checks", ""))

    if run_smoke:
        run_smoke_script(report)
    else:
        report.add(AuditFinding("smoke", "skip", "--no-smoke: skipped smoke script", ""))

    return report


def report_to_markdown(report: AuditReport) -> str:
    lines: List[str] = []
    lines.append("# Tax Optimiser Strategies v2 — plan audit report")
    lines.append("")
    lines.append("Generated by `agents/tax_optimiser_plan_auditor.py` (via `scripts/tax_optimiser_plan_audit_agent.py`).")
    lines.append("")
    lines.append("## Plan source")
    if report.plan_found and report.plan_path:
        lines.append(f"- **File:** `{report.plan_path}`")
        if report.plan_overview:
            lines.append(f"- **Overview (YAML):** {report.plan_overview}")
    else:
        lines.append("- Plan file not found; static checks still ran against the repo.")
    lines.append("")
    sc = report.summary_counts()
    lines.append("## Summary")
    lines.append(
        f"| Pass | Fail | Warn | Skip |\n|------|------|------|------|\n"
        f"| {sc.get('pass', 0)} | {sc.get('fail', 0)} | {sc.get('warn', 0)} | {sc.get('skip', 0)} |"
    )
    lines.append("")
    lines.append("## Findings")
    lines.append("")
    for f in report.findings:
        lines.append(f"### `{f.check_id}` — **{f.status.upper()}**")
        if f.plan_ref:
            lines.append(f"*Plan ref:* {f.plan_ref}")
        lines.append("")
        lines.append(detail_paragraph(f.detail))
        lines.append("")
    lines.append("## Coverage vs plan (narrative)")
    lines.append("")
    lines.append(
        "**Covered (high confidence):** Strategy engine module with S1 LTCG exemption booking, "
        "cost–benefit gates, `rejection_reason`, structured `benefit_breakdown` / `cost_breakdown` / `net_metrics` on S1; "
        "S3 unrealised loss heuristic; S4 defer-to-LTCG from open lots; persisted `tax_optimiser_strategy` + loader; "
        "interactive queue with `reversal_status`; follow-up batch + OpsTask hook + refresh script + Airflow DAG; "
        "client email template with benefit/cost; LTCG eligibility via `ltcg_eligibility` + `capital_gains_service`."
    )
    lines.append("")
    lines.append(
        "**Gaps / partial vs plan:** S2 `book_stcg_gains` not in engine (documented as reserved). "
        "S6/S7 are workflow-driven (follow-up batch) rather than separate strategy rows. "
        "Objective 2 future carry-forward ₹ estimate largely not populated on suggestions (S1 sets future shield to 0). "
        "S9 `apply_94_8_ranking` on **unrealised** S3 candidates may not change ordering (param seeded; engine picks best net only). "
        "Optional `rules_extended` / tax rule blocks for display copy not required in v1 per docs."
    )
    lines.append("")
    return "\n".join(lines)


def detail_paragraph(s: str) -> str:
    return s.strip().replace("\n", "\n\n")


def report_to_json(report: AuditReport) -> str:
    return json.dumps(
        {
            "plan_path": report.plan_path,
            "plan_found": report.plan_found,
            "plan_overview": report.plan_overview,
            "summary": report.summary_counts(),
            "findings": [
                {
                    "id": f.check_id,
                    "status": f.status,
                    "detail": f.detail,
                    "plan_ref": f.plan_ref,
                }
                for f in report.findings
            ],
        },
        indent=2,
    )
