"""Read-only codebase excerpts for the help assistant (whitelist only; Ollama never reads disks)."""

from __future__ import annotations

import re

from services.ai_readonly_policy import HELP_CODEBASE_READ_WHITELIST, resolve_whitelisted_read_path

# Keywords used to rank whitelisted files for a question
_FILE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "monthly_investments.py": ("workflow", "monthly", "investment", "funds", "recos", "stage"),
    "routes/workflows.py": ("workflow", "lead", "list_workflow"),
    "templates/new_monthly_investment.html": ("monthly", "investment", "workflow", "new"),
    "templates/monthly_investment_hub.html": ("monthly", "investment", "workflow", "sync", "new"),
    "templates/monthly_investments.html": ("monthly", "investment", "workflow"),
    "templates/workflows/list.html": ("workflow", "list", "filter"),
    "templates/base.html": ("monthly", "workflow", "dashboard"),
    "templates/models.html": ("security", "model", "import", "allocation", "client"),
    "templates/create_security_model.html": ("security", "model", "create", "allocation"),
    "templates/clients/assign_client_model.html": ("security", "model", "assign", "client", "asset"),
    "templates/maintenance/recommendations.html": ("recommendation", "generate", "trades", "monthly"),
    "templates/maintenance/reference_data.html": ("reference", "allocation", "security", "models", "prices"),
    "templates/review_schedules_list.html": ("review", "schedule", "dashboard"),
    "templates/tools/sql_query.html": ("sql", "assistant", "query", "admin"),
    "templates/clients/client_details.html": ("client", "workflow", "model assignment", "portfolio"),
    "docs/HELP_ASSISTANT_KNOWLEDGE.md": ("workflow", "monthly", "help", "security", "model"),
}

_STOPWORDS = frozenset(
    "a an the is are was were be been being have has had do does did will would "
    "could should may might must shall can how what where when why who which "
    "to from in on at for of and or not with this that these those i me my we "
    "you your our it its use using app inertia".split()
)

_MAX_SNIPPET_CHARS = 1_200
_MAX_TOTAL_CHARS = 4_500


def _tokens(text: str) -> set[str]:
    words = {w.lower() for w in re.findall(r"[a-z][a-z0-9_]{2,}", text.lower())}
    return {w for w in words if w not in _STOPWORDS}


def _score_file(question_tokens: set[str], path_keywords: tuple[str, ...]) -> float:
    return float(len(question_tokens & set(path_keywords)))


def _extract_snippet(path, question_tokens: set[str], *, rel: str) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if not lines:
        return ""

    hit_indexes: list[int] = []
    for i, line in enumerate(lines):
        low = line.lower()
        if any(t in low for t in question_tokens):
            hit_indexes.append(i)

    if not hit_indexes:
        start, end = 0, min(len(lines), 80)
    else:
        center = hit_indexes[0]
        start = max(0, center - 25)
        end = min(len(lines), center + 55)

    snippet_lines = lines[start:end]
    body = "\n".join(snippet_lines)
    if len(body) > _MAX_SNIPPET_CHARS:
        body = body[:_MAX_SNIPPET_CHARS] + "\n..."
    return f"--- {rel} (lines {start + 1}-{end}, read-only) ---\n{body}"


def build_codebase_context(question: str) -> str:
    """
    Return ranked excerpts from a fixed whitelist. Called by the app server only;
    Ollama receives the resulting text in the prompt and cannot open files itself.
    """
    q_tokens = _tokens(question)
    q_low = question.lower()

    scored: list[tuple[float, str]] = []
    for rel in HELP_CODEBASE_READ_WHITELIST:
        if rel.startswith("docs/") and rel != "docs/HELP_ASSISTANT_KNOWLEDGE.md":
            continue
        keywords = _FILE_KEYWORDS.get(rel, ())
        score = _score_file(q_tokens, keywords)
        boost = 0.0
        if "workflow" in rel or "monthly" in rel:
            boost = 0.5 if any(k in q_low for k in ("workflow", "monthly", "investment")) else 0
        if "model" in rel:
            boost = max(boost, 0.6 if any(k in q_low for k in ("security", "model", "allocation", "assign")) else 0)
        if score > 0 or boost > 0:
            scored.append((score + boost, rel))

    scored.sort(key=lambda x: -x[0])
    parts: list[str] = []
    total = 0
    for _, rel in scored[:5]:
        path = resolve_whitelisted_read_path(rel)
        if path is None:
            continue
        snippet = _extract_snippet(path, q_tokens, rel=rel)
        if not snippet:
            continue
        if total + len(snippet) > _MAX_TOTAL_CHARS:
            break
        parts.append(snippet)
        total += len(snippet)

    if not parts:
        return ""
    return (
        "=== CODEBASE (read-only excerpts; model cannot edit files) ===\n\n"
        + "\n\n".join(parts)
    )
