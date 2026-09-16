"""
AI read-only policy for INERTIA native (Ollama) assistants.

Ollama is used only for text generation (HTTP /api/generate). It has:
- NO filesystem access
- NO ability to run shell, git, or edit tools
- NO database write capability from the model itself

Application code may READ whitelisted paths to build prompts. Any file writes
related to help are human-initiated (propose/approve) and limited to docs/ only.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Paths the help assistant may read for context (relative to repo root).
HELP_CODEBASE_READ_WHITELIST: frozenset[str] = frozenset(
    {
        "monthly_investments.py",
        "routes/workflows.py",
        "templates/new_monthly_investment.html",
        "templates/monthly_investment_hub.html",
        "templates/monthly_investments.html",
        "templates/workflows/list.html",
        "templates/base.html",
        "templates/models.html",
        "templates/create_security_model.html",
        "templates/clients/assign_client_model.html",
        "templates/maintenance/recommendations.html",
        "templates/maintenance/reference_data.html",
        "templates/review_schedules_list.html",
        "templates/tools/sql_query.html",
        "templates/clients/client_details.html",
        "docs/HELP_ASSISTANT_KNOWLEDGE.md",
        "docs/USER_MANUAL.md",
        "docs/WORKFLOW_SYSTEM_GUIDE.md",
        "docs/NAVBAR_MODULES.md",
    }
)

# Only these docs files may be updated by the human approve flow (never by Ollama).
HELP_DOC_WRITE_WHITELIST: frozenset[str] = frozenset(
    {
        "docs/HELP_ASSISTANT_KNOWLEDGE.md",
        "docs/.help_knowledge_pending.json",
    }
)


def resolve_whitelisted_read_path(relative_path: str) -> Path | None:
    """Return resolved path if relative_path is on the read whitelist; else None."""
    rel = relative_path.replace("\\", "/").lstrip("/")
    if rel not in HELP_CODEBASE_READ_WHITELIST:
        return None
    full = (_REPO_ROOT / rel).resolve()
    try:
        full.relative_to(_REPO_ROOT.resolve())
    except ValueError:
        return None
    if not full.is_file():
        return None
    return full


def assert_help_doc_write_path(path: Path) -> None:
    """Raise if path is not an allowed help-documentation write target."""
    try:
        rel = path.resolve().relative_to(_REPO_ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise PermissionError("Write path must be inside the application directory.") from exc
    if rel not in HELP_DOC_WRITE_WHITELIST:
        raise PermissionError(
            f"AI/help flows cannot write to {rel!r}. "
            "Only approved help documentation files may be updated (not application code)."
        )
