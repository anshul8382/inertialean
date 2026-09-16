#!/usr/bin/env python3
"""
Chat-first functional test orchestrator (scaffold).

Design goals:
- Human-readable "test cards" printed to stdout (works well with Cursor chat)
- Deterministic execution via pytest (used by pre-commit/CI)
- Module-oriented: start small, expand as modules register

Current scope:
- Client Details → "Security Model" display precedence
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Iterable


@dataclass(frozen=True)
class TestCard:
    id: str
    title: str
    given: str
    when: str
    then: list[str]
    pytest_target: str


CLIENT_DETAILS_SECURITY_MODEL = [
    TestCard(
        id="TC-CLIENTDETAILS-SECURITYMODEL-001",
        title="Prefer Equity per-asset-class model (UI)",
        given="Client has equity per-asset-class security model AND legacy stock_model (different names).",
        when="Render the Model Assignment card on Client Details.",
        then=[
            "Security Model shows the equity per-asset-class model name",
            "Security Model does NOT show the legacy stock_model name",
        ],
        pytest_target="tests/functional/test_client_details_security_model.py::test_security_model_prefers_equity_override",
    ),
    TestCard(
        id="TC-CLIENTDETAILS-SECURITYMODEL-002",
        title="Fallback to legacy stock_model when no equity override",
        given="Client has legacy stock_model and no equity per-asset-class row.",
        when="Render the Model Assignment card on Client Details.",
        then=["Security Model shows the legacy stock_model name"],
        pytest_target="tests/functional/test_client_details_security_model.py::test_security_model_falls_back_to_stock_model",
    ),
    TestCard(
        id="TC-CLIENTDETAILS-SECURITYMODEL-003",
        title='"Not assigned" when no security model configured',
        given="Client has no equity override and no legacy stock_model.",
        when="Render the Model Assignment card on Client Details.",
        then=['Security Model shows "Not assigned"'],
        pytest_target="tests/functional/test_client_details_security_model.py::test_security_model_shows_not_assigned",
    ),
]

# -----------------------------------------------------------------------------
# New module packs (system map)
# -----------------------------------------------------------------------------

AUTH_CORE = [
    TestCard(
        id="TC-AUTH-CORE-001",
        title="Auth module pack present",
        given="Developer changes auth / access modules.",
        when="Run orchestrator on changed files.",
        then=["Auth pack prints test cards and has runnable pytest target"],
        pytest_target="tests/functional/test_system_module_packs_smoke.py::test_pack_auth_core",
    ),
]

CLIENTS_CORE = [
    TestCard(
        id="TC-CLIENTS-CORE-001",
        title="Clients module pack present",
        given="Developer changes client routes/templates/services.",
        when="Run orchestrator on changed files.",
        then=["Clients pack prints cards and has runnable pytest target"],
        pytest_target="tests/functional/test_system_module_packs_smoke.py::test_pack_clients_core",
    ),
]

AGREEMENTS_CORE = [
    TestCard(
        id="TC-AGREEMENTS-CORE-001",
        title="Agreements module pack present",
        given="Developer changes agreements routes/templates/services.",
        when="Run orchestrator on changed files.",
        then=["Agreements pack prints cards and has runnable pytest target"],
        pytest_target="tests/functional/test_system_module_packs_smoke.py::test_pack_agreements_core",
    ),
]

BILLING_CORE = [
    TestCard(
        id="TC-BILLING-CORE-001",
        title="Billing core pack present",
        given="Developer changes billing calculation/generation code.",
        when="Run orchestrator on changed files.",
        then=["Billing pack prints cards and has runnable pytest target"],
        pytest_target="tests/functional/test_system_module_packs_smoke.py::test_pack_billing_core",
    ),
]

INVOICES_CORE = [
    TestCard(
        id="TC-INVOICES-CORE-001",
        title="Invoices module pack present",
        given="Developer changes invoice routes/templates/import.",
        when="Run orchestrator on changed files.",
        then=["Invoices pack prints cards and has runnable pytest target"],
        pytest_target="tests/functional/test_system_module_packs_smoke.py::test_pack_invoices_core",
    ),
]

RECOMMENDATIONS_CORE = [
    TestCard(
        id="TC-RECO-CORE-001",
        title="Recommendations module pack present",
        given="Developer changes recommendation module code.",
        when="Run orchestrator on changed files.",
        then=["Recommendations pack prints cards and has runnable pytest target"],
        pytest_target="tests/functional/test_recommendations_smoke.py::test_recommendation_service_imports",
    ),
]

REVIEWS_CORE = [
    TestCard(
        id="TC-REVIEWS-CORE-001",
        title="Reviews module pack present",
        given="Developer changes review routes/templates/services.",
        when="Run orchestrator on changed files.",
        then=["Reviews pack prints cards and has runnable pytest target"],
        pytest_target="tests/functional/test_system_module_packs_smoke.py::test_pack_reviews_core",
    ),
    TestCard(
        id="TC-REVIEWS-PERIOD-PERFORMER-001",
        title="Best/worst performers use holder-relevant start price",
        given="A stock is first bought mid-period after the market fell from period-start level.",
        when="Compute period performer price return (blended / purchase VWAP).",
        then=[
            "New position uses period BUY VWAP as effective start (not period-start market)",
            "Held-throughout position uses period-start adjusted market",
            "Increased position uses quantity-weighted blended start",
        ],
        pytest_target="tests/test_period_performer_return_service.py",
    ),
]

TRANSACTIONS_CORE = [
    TestCard(
        id="TC-TXN-CORE-001",
        title="Transactions module pack present",
        given="Developer changes transaction/cashflow modules.",
        when="Run orchestrator on changed files.",
        then=["Transactions pack prints cards and has runnable pytest target"],
        pytest_target="tests/functional/test_system_module_packs_smoke.py::test_pack_transactions_core",
    ),
]

PORTFOLIO_CORE = [
    TestCard(
        id="TC-PORTFOLIO-CORE-001",
        title="Portfolio module pack present",
        given="Developer changes holdings/valuation/price modules.",
        when="Run orchestrator on changed files.",
        then=["Portfolio pack prints cards and has runnable pytest target"],
        pytest_target="tests/functional/test_system_module_packs_smoke.py::test_pack_portfolio_core",
    ),
]

OPS_TASKS_CORE = [
    TestCard(
        id="TC-OPS-TASKS-001",
        title="Ops/tasks module pack present",
        given="Developer changes tasks/assignment/ops workflows.",
        when="Run orchestrator on changed files.",
        then=["Ops/tasks pack prints cards and has runnable pytest target"],
        pytest_target="tests/functional/test_system_module_packs_smoke.py::test_pack_ops_tasks_core",
    ),
]

ALERTS_CORE = [
    TestCard(
        id="TC-ALERTS-CORE-001",
        title="Alerts module pack present",
        given="Developer changes alerts/dashboard modules.",
        when="Run orchestrator on changed files.",
        then=["Alerts pack prints cards and has runnable pytest target"],
        pytest_target="tests/functional/test_system_module_packs_smoke.py::test_pack_alerts_core",
    ),
]

MAINTENANCE_TOOLS_CORE = [
    TestCard(
        id="TC-MAINT-TOOLS-001",
        title="Maintenance/tools module pack present",
        given="Developer changes maintenance hubs/tools.",
        when="Run orchestrator on changed files.",
        then=["Maintenance/tools pack prints cards and has runnable pytest target"],
        pytest_target="tests/functional/test_system_module_packs_smoke.py::test_pack_maintenance_tools_core",
    ),
]

BILLING_INVOICES_GENERATE = [
    TestCard(
        id="TC-BILLING-INVOICEGEN-001",
        title="Generate invoice screen renders (UI)",
        given="User opens Generate invoice screen from Billing hub / Invoice management.",
        when="Render the /invoices/generate template with accessible agreements.",
        then=[
            "Page shows 'Generate invoice'",
            "Agreement dropdown is present",
        ],
        pytest_target="tests/functional/test_billing_invoice_generate_ui.py::test_generate_invoice_page_renders",
    ),
]

BILLING_AGREEMENTS_OVERVIEW = [
    TestCard(
        id="TC-BILLING-AGREEMENTS-OVERVIEW-001",
        title="Agreements overview shows missing agreement flag (UI)",
        given="Client has no agreement on file.",
        when="Render the Agreements overview table row.",
        then=[
            "Row shows 'None' in Agreement column",
            "Issues include 'No agreement on file'",
        ],
        pytest_target="tests/functional/test_agreements_overview_ui.py::test_overview_row_missing_agreement",
    ),
]


MODULES: dict[str, list[TestCard]] = {
    "auth.core": AUTH_CORE,
    "clients.core": CLIENTS_CORE,
    "clients.client_details.security_model": CLIENT_DETAILS_SECURITY_MODEL,
    "agreements.core": AGREEMENTS_CORE,
    "billing.core": BILLING_CORE,
    "invoices.core": INVOICES_CORE,
    "billing.invoices.generate": BILLING_INVOICES_GENERATE,
    "billing.agreements.overview": BILLING_AGREEMENTS_OVERVIEW,
    "recommendations.core": RECOMMENDATIONS_CORE,
    "reviews.core": REVIEWS_CORE,
    "transactions.core": TRANSACTIONS_CORE,
    "portfolio.core": PORTFOLIO_CORE,
    "ops.tasks.core": OPS_TASKS_CORE,
    "alerts.core": ALERTS_CORE,
    "maintenance.tools.core": MAINTENANCE_TOOLS_CORE,
}

MODULE_FILE_RULES: dict[str, list[str]] = {
    "auth.core": [
        "routes/auth.py",
        "access_control.py",
        "services/jwt_service.py",
        "services/permission_service.py",
        "services/audit_service.py",
        "models/audit_log.py",
        "models.py",
        "templates/auth/**",
    ],
    "clients.core": [
        "routes/clients.py",
        "routes/leads.py",
        "api/v1/clients.py",
        "templates/clients/**",
        "templates/leads/**",
        "services/client_*",
        "services/*client*",
        "models.py",
        "models/**",
    ],
    # If any of these files change, we consider the module impacted.
    "clients.client_details.security_model": [
        "templates/clients/client_details.html",
        "templates/clients/partials/model_assignment_card.html",
        "routes/clients.py",
        "api/v1/clients.py",
        "models.py",
        "models/**",
    ],
    "agreements.core": [
        "routes/agreements.py",
        "templates/agreements/**",
        "services/*agreement*",
        "migrations/*agreement*",
        "models.py",
        "models/**",
    ],
    "billing.core": [
        "billing_calculation_service.py",
        "invoice_generation_service.py",
        "services/billing_manager_service.py",
        "routes/billing_api.py",
        "models.py",
        "models/**",
    ],
    "invoices.core": [
        "routes/invoices.py",
        "templates/invoices/**",
        "services/invoice_*",
        "services/*invoice*",
        "migrations/*invoice*",
        "models.py",
        "models/**",
    ],
    "billing.invoices.generate": [
        "routes/invoices.py",
        "invoice_generation_service.py",
        "billing_calculation_service.py",
        "templates/invoices/generate.html",
        "templates/invoices/list.html",
        "templates/maintenance/billing.html",
        "templates/base.html",
        "models.py",
        "models/**",
        "services/**",
    ],
    "billing.agreements.overview": [
        "routes/agreements.py",
        "services/agreement_overview_service.py",
        "templates/agreements/overview.html",
        "templates/maintenance/billing.html",
        "templates/base.html",
        "models.py",
        "models/**",
    ],
    "recommendations.core": [
        "services/recommendation_service.py",
        "services/recommendation_*.py",
        "services/recommendation*.py",
        "routes/recommendation_execution.py",
        "routes/unified_recommendations.py",
        "templates/reviews/**",
        "templates/unified_recommendations/**",
        "models.py",
        "models/**",
    ],
    "reviews.core": [
        "review_routes.py",
        "review_service.py",
        "simple_review_service.py",
        "enhanced_review_service.py",
        "routes/enhanced_review_routes.py",
        "templates/reviews/**",
        "templates/reviews/**/**",
        "api/v1/performance.py",
        "api/v1/period_analysis.py",
        "api/v2/period_analysis.py",
        "api/v2/period_enhanced_analytics.py",
        "services/period_performer_return_service.py",
        "tests/test_period_performer_return_service.py",
    ],
    "transactions.core": [
        "routes/main.py",
        "routes/forms.py",
        "api/v1/transactions.py",
        "api/v2/**",
        "templates/historical_prices/**",
        "templates/account_management/**",
        "models.py",
        "models/**",
    ],
    "portfolio.core": [
        "services/price_service.py",
        "services/portfolio_valuation_service.py",
        "services/forward_holding_calculation_service.py",
        "services/quantity_adjustment_service.py",
        "routes/historical_prices.py",
        "models.py",
        "models/**",
    ],
    "ops.tasks.core": [
        "services/task_assignment_service.py",
        "agents/task_assignment_agent.py",
        "routes/tasks.py",
        "templates/tasks/**",
        "models.py",
        "models/**",
    ],
    "alerts.core": [
        "routes/alerts.py",
        "alert_service.py",
        "templates/alerts/**",
        "api/v1/alerts.py",
        "models.py",
        "models/**",
    ],
    "maintenance.tools.core": [
        "routes/main_sections/**",
        "templates/maintenance/**",
        "templates/tools/**",
        "scripts/run_mobile_ui_check.py",
        "scripts/run_agent_approval_loop.py",
        "templates/base.html",
    ],
}


def _print_cards(cards: list[TestCard]) -> None:
    for c in cards:
        print()
        print(f"{c.id}: {c.title}")
        print(f"Given: {c.given}")
        print(f"When:  {c.when}")
        print("Then (Expected):")
        for t in c.then:
            print(f"  - {t}")
        print(f"Pytest: {c.pytest_target}")
    print()


def _run_pytest(cards: list[TestCard]) -> int:
    # Ensure the "cards" are visible before pytest writes output.
    sys.stdout.flush()
    targets = [c.pytest_target for c in cards]
    cmd = [sys.executable, "-m", "pytest", "-q", *targets]
    p = subprocess.run(cmd)
    return int(p.returncode or 0)


def _git_changed_files(*, staged: bool) -> list[str]:
    # --staged uses index vs HEAD; otherwise uses working tree vs HEAD.
    cmd = ["git", "diff", "--name-only"]
    if staged:
        cmd.append("--staged")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stdout or "") + (r.stderr or ""))
    return [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]


def _module_matches_file(module: str, path: str) -> bool:
    patterns = MODULE_FILE_RULES.get(module) or []
    return any(fnmatch(path, pat) for pat in patterns)


def _detect_modules(changed_files: Iterable[str]) -> list[str]:
    impacted: set[str] = set()
    for f in changed_files:
        for module in MODULES.keys():
            if _module_matches_file(module, f):
                impacted.add(module)
    return sorted(impacted)


def _print_changed_files(files: list[str]) -> None:
    if not files:
        print("Changed files: (none)")
        return
    print("Changed files:")
    for f in files:
        print(f"  - {f}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", choices=sorted(MODULES.keys()), help="Run cards/tests for a single module")
    parser.add_argument("--changed", action="store_true", help="Auto-detect impacted modules from git diff")
    parser.add_argument("--staged", action="store_true", help="Use staged changes for --changed")
    parser.add_argument("--run", action="store_true", help="Run pytest targets for selected modules")
    args = parser.parse_args()

    if not args.module and not args.changed:
        parser.error("one of --module or --changed is required")

    modules: list[str]
    changed_files: list[str] = []
    if args.changed:
        changed_files = _git_changed_files(staged=args.staged)
        modules = _detect_modules(changed_files)
    else:
        modules = [args.module]

    if args.changed:
        print()
        _print_changed_files(changed_files)
        print()

    if not modules:
        print("No impacted modules detected. (No functional packs selected.)")
        return 0

    all_cards: list[TestCard] = []
    print("Impacted modules:")
    for m in modules:
        print(f"  - {m}")
        all_cards.extend(MODULES[m])

    _print_cards(all_cards)

    if not args.run:
        print("Reply in chat with: approve / edit expectations / ignore-fields.")
        print("Then re-run with --run (or rely on pre-commit verification).")
        return 0

    return _run_pytest(all_cards)


if __name__ == "__main__":
    raise SystemExit(main())

