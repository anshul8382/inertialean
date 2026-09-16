"""Natural-language → read-only SQL drafts via local Ollama (admin review required before run)."""

from __future__ import annotations

import re

from services.database_tool_service import build_sql_schema_api_payload, validate_readonly_select
from services.ollama_service import OllamaUnavailableError, ollama_service

# Tables admins most often need; full schema is still available but capped in prompt.
_PRIORITY_TABLES = (
    "client",
    "user",
    "transaction",
    "holding",
    "workflow",
    "monthly_investment",
    "data_integrity_issue",
    "ops_task",
    "review_workflow",
    "security",
    "cashflow",
    "alert",
)

_MAX_SCHEMA_TABLES = 40


def _schema_prompt_block() -> str:
    payload = build_sql_schema_api_payload()
    if not payload.get("success"):
        return "Schema unavailable."
    tables = payload.get("tables") or {}
    names = list(tables.keys())
    ordered: list[str] = []
    for pref in _PRIORITY_TABLES:
        for n in names:
            if n.lower() == pref or pref in n.lower():
                if n not in ordered:
                    ordered.append(n)
    for n in sorted(names):
        if n not in ordered:
            ordered.append(n)
    ordered = ordered[:_MAX_SCHEMA_TABLES]

    lines = []
    for tname in ordered:
        cols = tables[tname].get("columns") or []
        col_bits = []
        for c in cols[:25]:
            bit = c["name"]
            if c.get("primary_key"):
                bit += " PK"
            if c.get("foreign_key"):
                ref = c["foreign_key"]
                if isinstance(ref, dict):
                    bit += f" FK->{ref.get('references_table')}.{ref.get('references_column')}"
            col_bits.append(bit)
        extra = len(cols) - 25
        suffix = f" (+{extra} more cols)" if extra > 0 else ""
        lines.append(f"- {tname}: {', '.join(col_bits)}{suffix}")
    if len(names) > len(ordered):
        lines.append(f"... and {len(names) - len(ordered)} more tables (use exact names from schema browser)")
    return "\n".join(lines)


def _extract_sql(raw: str) -> str:
    text = raw.strip()
    fence = re.search(r"```(?:sql)?\s*([\s\S]*?)```", text, re.I)
    if fence:
        text = fence.group(1).strip()
    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if s.upper().startswith("SELECT") or s.upper().startswith("WITH"):
            start = i
            break
    return "\n".join(lines[start:]).strip().rstrip(";")


def suggest_readonly_sql(natural_language: str) -> dict:
    request = (natural_language or "").strip()
    if not request:
        return {"success": False, "error": "Describe what you want to query."}
    if len(request) > 1500:
        return {"success": False, "error": "Request is too long (max 1500 characters)."}

    schema = _schema_prompt_block()
    prompt = f"""You are a MySQL expert for the INERTIA investment management database.
Write ONE read-only SQL query for the user request.
Rules:
- Only SELECT or WITH ... SELECT (CTEs). No INSERT, UPDATE, DELETE, DDL.
- Single statement only (no semicolons between statements).
- Use exact table and column names from SCHEMA below.
- Prefer LIMIT 500 or less on large tables.
- Do not query passwords or secrets.
- Reply with ONLY the SQL inside a ```sql fenced block, no other text.

SCHEMA (partial):
{schema}

USER REQUEST:
{request}

SQL:"""

    try:
        raw = ollama_service.generate(prompt, max_tokens=800, temperature=0.05)
        sql = _extract_sql(raw)
        if not sql:
            return {"success": False, "error": "Could not extract SQL from model response. Try rephrasing."}
        ok, normalized_or_err = validate_readonly_select(sql)
        if not ok:
            return {
                "success": False,
                "error": f"Generated SQL failed safety checks: {normalized_or_err}",
                "sql_draft": sql,
            }
        return {
            "success": True,
            "sql": normalized_or_err,
            "model": ollama_service.get_model(),
            "warning": "Review this query before running. AI can make mistakes.",
        }
    except OllamaUnavailableError as exc:
        return {"success": False, "error": str(exc), "unavailable": True}
    except Exception as exc:
        return {"success": False, "error": f"SQL assistant failed: {exc}"}
