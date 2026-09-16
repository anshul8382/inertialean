"""INERTIA help assistant — docs + codebase context, fast path, local Ollama."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from services.help_codebase_context_service import build_codebase_context
from services.ollama_service import OllamaUnavailableError, ollama_service

_DOCS_ROOT = Path(__file__).resolve().parent.parent / "docs"
_KNOWLEDGE_PATH = _DOCS_ROOT / "HELP_ASSISTANT_KNOWLEDGE.md"
_MANUAL_PATH = _DOCS_ROOT / "USER_MANUAL.md"
_WORKFLOW_GUIDE_PATH = _DOCS_ROOT / "WORKFLOW_SYSTEM_GUIDE.md"
_NAVBAR_PATH = _DOCS_ROOT / "NAVBAR_MODULES.md"

_MAX_CONTEXT_CHARS = 14_000
_HELP_MAX_TOKENS = int(os.environ.get("OLLAMA_HELP_MAX_TOKENS", "520"))

_KNOWLEDGE_ALWAYS_SECTIONS = (
    "response policy and guardrails",
    "not in this app",
    "navigation quick map",
)

_KNOWLEDGE_KEYWORD_SECTIONS: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("workflow", "monthly", "funds", "recos", "notify", "exec"), ("monthly investment workflows",)),
    (("security model", "asset model", "allocation model", "assign model"), ("security and asset allocation models",)),
    (("client", "lead", "onboard", "crm"), ("client onboarding and crm",)),
    (("recommendation", "reco", "trade", "execution"), ("recommendations and execution",)),
    (("upload", "import", "data upload", "cashflow", "historical price"), ("data upload and maintenance",)),
    (("review", "period analysis"), ("reviews and analytics",)),
    (("alert", "task", "hub", "issue"), ("tasks, alerts, and hub",)),
    (("admin", "permission", "role", "user"), ("admin and permissions",)),
    (("assistant", "sql", "manual", "pdf", "help"), ("assistant and sql tools",)),
    (("error", "not working", "cannot", "failed", "issue"), ("troubleshooting playbook",)),
]

_ALWAYS_MANUAL_SECTIONS = (
    "getting started",
    "navigation overview",
    "dashboards",
    "core business processes",
)

_KEYWORD_SECTIONS: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("security model", "asset model", "allocation model", "assign model"), ("maintenance", "reference data")),
    (("workflow", "funds", "recos", "monthly investment"), ("dashboards", "core business processes")),
    (("recommend", "recs", "trade"), ("maintenance", "recommendations")),
    (("client", "lead"), ("clients and crm", "client detail")),
    (("review",), ("dashboards", "maintenance")),
    (("upload", "cashflow", "transaction"), ("maintenance", "data upload")),
]

_ALLOCATION_MODEL_RE = re.compile(
    r"\b(security|asset|stock)\s+(allocation\s+)?models?\b|\ballocation\s+models?\b",
    re.I,
)

# Instant answers (no Ollama) — keyed by pattern id
_FAST_PATH_ANSWERS: dict[str, str] = {
    "create_workflow": """To create a new **monthly investment workflow**:

1. Open **Dashboards → Monthly investments** (hub at `/monthly-investments`).
2. Click the green **New monthly investment** button.
3. Choose **client**, **planned amount**, and **investment date**, then submit.
4. INERTIA creates the monthly investment and a **Workflow** starting at **FUNDS**.

Open the row to advance stages: **FUNDS → RECOS → NOTIFY → EXEC → UPDATE → COMPLETED**.

**Not in this app:** There is no **Maintenance → Workflows** menu and no generic **Create New Workflow** form with name/description fields.

**Other workflow types:** Use **Dashboards → All workflows** to view/filter lead and onboarding workflows (created from leads, not via a blank “create workflow” wizard).

**Bulk fix:** On the monthly investments hub, **Sync missing workflows** creates missing cycles when safe.""",
    "security_model_from_client": """To create a **new security model** based on an **existing client** (import from holdings):

1. Open **Maintenance → Reference data → Allocation models** (`/models`).
2. In the **Security Models** section, click **Create Security Model by Import**.
3. Select the **client** and **asset class** (e.g. Equity), then **Import**.
4. INERTIA creates a new security allocation model from that client’s distribution for that class.

**Assign that model to the client (if needed):**
1. **Clients → All Clients** → open the client → **Model Assignment** → **Assign Models** / **Update Models**.
2. Under **Security Distribution Models (by Asset Class)**, choose the model per class → **Save Model Assignments**.

**Not the same as:** monthly investment workflows (**Dashboards → Monthly investments**).""",
    "security_model_assign": """To **assign** security / asset models to an existing client:

1. **Clients → All Clients** → open the client.
2. In **Model Assignment**, click **Assign Models** or **Update Models**.
3. Pick **Asset Allocation Model** and **Security Distribution Models** per asset class (recommended).
4. Click **Save Model Assignments**.

To **create** a new security model from the client’s holdings first, use **Maintenance → Reference data → Allocation models → Create Security Model by Import**.""",
    "security_model_blank": """To create a **new blank security model** (template, not imported from a client):

1. **Maintenance → Reference data → Allocation models** (`/models`).
2. Click **Create security model**.
3. Enter name, asset class, and security weights, then save.""",
    "assign_models_client": """To assign models for an existing client:

1. Open **Clients → All Clients** and open the client profile.
2. In **Model Assignment**, click **Assign Models** / **Update Models**.
3. Choose **Asset Allocation Model** and **Security Distribution Models (by Asset Class)**.
4. Click **Save Model Assignments**.""",
    "upload_trades": """To upload trades:

1. Open **Maintenance → Data upload → Trade upload**.
2. Download/use the required template (if shown on page).
3. Upload the file and review validation messages.
4. Fix rejected rows (if any) and re-upload.

If you only need one entry, use manual trade entry where enabled.""",
    "generate_recommendations": """To generate recommendations:

1. Open **Maintenance → Recommendations → Generate new recommendations**.
2. Select client, amount, and required inputs.
3. Generate and review outputs.
4. Use workflow stages to progress from **RECOS** to **NOTIFY** after review/approval.""",
    "reviews_period_analysis": """For review analytics:

1. Open **Maintenance → Reviews → Period analysis V2** for review metrics.
2. Use **Dashboards → Reviews** for schedules/status.
3. For client-level details, open the client and review workflow/status sections.""",
    "user_manual_pdf": """To open the user manual PDF:

1. Go to **Assistant → User manual (PDF)**.
2. The app opens `/docs/user-manual.pdf` in-browser.
3. If unavailable, ask admin to regenerate the PDF from `docs/USER_MANUAL.md`.""",
}

# Checked before workflow rules (security model ≠ workflow)
_ALLOCATION_FAST_PATH_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\b(create|new|import|build|make)\b.*\b(security|stock)\s+(allocation\s+)?model",
            re.I,
        ),
        "security_model_from_client",
    ),
    (
        re.compile(
            r"\b(security|stock)\s+(allocation\s+)?model\b.*\b(client|import|existing|from)\b",
            re.I,
        ),
        "security_model_from_client",
    ),
    (
        re.compile(r"\b(assign|attach|link|update)\b.*\b(security|asset|stock)\s+model", re.I),
        "security_model_assign",
    ),
    (
        re.compile(r"\b(create|new)\b.*\b(security|stock)\s+model\b", re.I),
        "security_model_from_client",
    ),
    (
        re.compile(r"\b(assign|update|set)\b.*\b(asset|security|stock)\s+models?\b", re.I),
        "assign_models_client",
    ),
]

_WORKFLOW_FAST_PATH_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(create|start|add|new)\b.*\bworkflow\b", re.I), "create_workflow"),
    (re.compile(r"\bworkflow\b.*\b(create|new|start)\b", re.I), "create_workflow"),
    (re.compile(r"how\s+(do|to)\s+i\s+create\s+a?\s*workflow\b", re.I), "create_workflow"),
]

_GENERAL_FAST_PATH_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(upload|import)\b.*\btrades?\b", re.I), "upload_trades"),
    (re.compile(r"\b(generate|create)\b.*\brecommendations?\b", re.I), "generate_recommendations"),
    (re.compile(r"\b(period analysis|review schedule|reviews?)\b", re.I), "reviews_period_analysis"),
    (re.compile(r"\b(user manual|pdf manual|manual pdf)\b", re.I), "user_manual_pdf"),
]


def _read_file(path: Path, max_chars: int | None = None) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    if max_chars and len(text) > max_chars:
        return text[:max_chars] + "\n\n[... truncated ...]\n"
    return text


def _split_manual_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_key = "_intro"
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if buf:
                sections[current_key] = "\n".join(buf).strip()
            title = line[3:].strip()
            current_key = re.sub(r"^\d+\.\s*", "", title).lower()
            buf = [line]
        else:
            buf.append(line)
    if buf:
        sections[current_key] = "\n".join(buf).strip()
    return sections


def _split_markdown_sections(text: str) -> dict[str, str]:
    """Split markdown by level-2 headings and key by normalized heading."""
    sections: dict[str, str] = {}
    current_key = "_intro"
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if buf:
                sections[current_key] = "\n".join(buf).strip()
            current_key = line[3:].strip().lower()
            buf = [line]
        else:
            buf.append(line)
    if buf:
        sections[current_key] = "\n".join(buf).strip()
    return sections


def _build_knowledge_excerpt(question: str) -> str:
    knowledge = _read_file(_KNOWLEDGE_PATH)
    if not knowledge:
        return ""
    sections = _split_markdown_sections(knowledge)
    q = question.lower()
    picked: list[str] = []

    for heading in _KNOWLEDGE_ALWAYS_SECTIONS:
        for key, body in sections.items():
            if key != "_intro" and heading in key:
                picked.append(body)
                break

    for keywords, section_titles in _KNOWLEDGE_KEYWORD_SECTIONS:
        if not any(k in q for k in keywords):
            continue
        for wanted in section_titles:
            for key, body in sections.items():
                if key != "_intro" and wanted in key and body not in picked:
                    picked.append(body)
                    break

    if not picked:
        return knowledge[:4000]

    excerpt = "\n\n---\n\n".join(picked)
    return excerpt[:7000]


def _sections_for_question(question: str) -> set[str]:
    q = question.lower()
    keys = set(_ALWAYS_MANUAL_SECTIONS)
    for keywords, section_names in _KEYWORD_SECTIONS:
        if any(k in q for k in keywords):
            keys.update(section_names)
    return keys


def _build_manual_excerpt(question: str) -> str:
    manual = _read_file(_MANUAL_PATH)
    if not manual:
        return ""
    sections = _split_manual_sections(manual)
    want = _sections_for_question(question)
    parts = [
        body
        for key, body in sections.items()
        if key != "_intro" and (any(w in key for w in want) or key in want)
    ]
    return "\n\n---\n\n".join(parts) if parts else ""


def _is_allocation_model_question(question: str) -> bool:
    return bool(_ALLOCATION_MODEL_RE.search(question))


def _try_fast_path(question: str) -> str | None:
    if _is_allocation_model_question(question):
        for pattern, key in _ALLOCATION_FAST_PATH_RULES:
            if pattern.search(question):
                return _FAST_PATH_ANSWERS.get(key)
        # Default for ambiguous security-model questions
        return _FAST_PATH_ANSWERS.get("security_model_from_client")

    for pattern, key in _GENERAL_FAST_PATH_RULES:
        if pattern.search(question):
            return _FAST_PATH_ANSWERS.get(key)

    for pattern, key in _WORKFLOW_FAST_PATH_RULES:
        if pattern.search(question):
            return _FAST_PATH_ANSWERS.get(key)
    return None


def _build_knowledge_context(question: str, *, include_manual: bool = True) -> str:
    chunks: list[str] = []

    knowledge = _build_knowledge_excerpt(question)
    if knowledge:
        chunks.append("=== PROCEDURAL KNOWLEDGE (highest priority) ===\n" + knowledge)

    codebase = build_codebase_context(question)
    if codebase:
        chunks.append(codebase)

    q_low = question.lower()
    if _is_allocation_model_question(question):
        pass  # avoid flooding context with workflow guide
    elif any(w in q_low for w in ("workflow", "funds", "recos", "monthly")):
        guide = _read_file(_WORKFLOW_GUIDE_PATH, max_chars=2_500)
        if guide:
            chunks.append("=== WORKFLOW GUIDE (excerpt) ===\n" + guide)

    if include_manual:
        manual_excerpt = _build_manual_excerpt(question)
        if manual_excerpt:
            chunks.append("=== USER MANUAL (selected sections) ===\n" + manual_excerpt[:6_000])

    navbar = _read_file(_NAVBAR_PATH, max_chars=2_000)
    if navbar:
        chunks.append("=== NAVBAR (excerpt) ===\n" + navbar)

    combined = "\n\n".join(chunks)
    if len(combined) > _MAX_CONTEXT_CHARS:
        combined = combined[:_MAX_CONTEXT_CHARS] + "\n\n[... truncated ...]\n"
    return combined


def _build_prompt(question: str, history: list[dict] | None) -> str:
    context = _build_knowledge_context(question)
    hist_lines = []
    if history:
        for turn in history[-4:]:
            role = turn.get("role", "user")
            content = (turn.get("content") or "").strip()[:500]
            if content:
                hist_lines.append(f"{role.upper()}: {content}")
    hist_block = ("\n".join(hist_lines) + "\n") if hist_lines else ""

    return f"""You are the INERTIA in-app help assistant. Answer ONLY from DOCUMENTATION below.

You are read-only: you cannot edit code, files, the database, or settings. You only explain steps.

Strict rules:
- Copy exact menu paths and button labels from PROCEDURAL KNOWLEDGE and CODEBASE sections.
- NEVER mention: Maintenance → Workflows, Create New Workflow button, workflow Name/Description/Module Type form — these do NOT exist.
- "Create a workflow" = monthly investment cycle (Dashboards → Monthly investments), NOT security models.
- "Security model" / "allocation model" = Maintenance → Reference data → Allocation models; import or assign on client — never monthly investments.
- If the user asks about security models, do NOT mention New monthly investment or FUNDS/RECOS stages.
- If DOCUMENTATION does not cover the question, say so briefly (a human may add to help docs separately — not you).
- No client balances, holdings, or SQL.
- Under 220 words. Numbered steps.

DOCUMENTATION:
{context}

{hist_block}USER: {question.strip()}

ASSISTANT:"""


def _clean_reply(raw: str) -> str:
    text = raw.strip()
    if text.upper().startswith("ASSISTANT:"):
        text = text[10:].strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def _misanswer_security_with_workflow(question: str, reply: str) -> bool:
    if not _is_allocation_model_question(question):
        return False
    low = reply.lower()
    return "monthly investment" in low or "new monthly investment" in low or (
        "funds" in low and "recos" in low
    )


def _looks_hallucinated(reply: str) -> bool:
    bad = (
        "maintenance → workflows",
        "maintenance -> workflows",
        "create new workflow",
        "module type",
        "give your workflow a descriptive name",
    )
    low = reply.lower()
    return any(b in low for b in bad)


def ask_help_assistant(question: str, history: list[dict] | None = None) -> dict:
    q = (question or "").strip()
    if not q:
        return {"success": False, "error": "Please enter a question."}
    if len(q) > 2000:
        return {"success": False, "error": "Question is too long (max 2000 characters)."}

    t0 = time.perf_counter()

    fast = _try_fast_path(q)
    if fast:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "success": True,
            "reply": fast,
            "model": "fast-path",
            "fast_path": True,
            "elapsed_ms": elapsed_ms,
            "suggest_doc_update": False,
            "sources": ["knowledge_base", "codebase"],
        }

    try:
        raw = ollama_service.generate(
            _build_prompt(q, history),
            max_tokens=_HELP_MAX_TOKENS,
            temperature=0.05,
        )
        reply = _clean_reply(raw)
        if not reply:
            return {"success": False, "error": "Empty response from AI. Try again."}

        if _looks_hallucinated(reply) or _misanswer_security_with_workflow(q, reply):
            retry = _try_fast_path(q)
            if retry:
                reply = retry

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        ctx = _build_knowledge_context(q, include_manual=False)
        thin_doc = len(ctx) < 800 or q.lower() not in ctx.lower()

        return {
            "success": True,
            "reply": reply,
            "model": ollama_service.get_model(),
            "fast_path": False,
            "elapsed_ms": elapsed_ms,
            "suggest_doc_update": thin_doc,
            "sources": ["knowledge_base", "codebase", "user_manual", "ollama"],
        }
    except OllamaUnavailableError as exc:
        return {"success": False, "error": str(exc), "unavailable": True}
    except Exception as exc:
        return {"success": False, "error": f"Help assistant failed: {exc}"}
