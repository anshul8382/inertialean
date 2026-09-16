"""Tests for native AI assistant helpers (no Ollama required)."""

import pytest

from services.ai_readonly_policy import assert_help_doc_write_path, resolve_whitelisted_read_path
from services.help_assistant_service import _build_knowledge_context, ask_help_assistant
from services.help_codebase_context_service import build_codebase_context
from services.sql_assistant_service import _extract_sql
from services.database_tool_service import validate_readonly_select


def test_extract_sql_from_fence():
    raw = "Here is the query:\n```sql\nSELECT id, name FROM client LIMIT 10\n```"
    assert _extract_sql(raw).upper().startswith("SELECT")


def test_extract_sql_plain():
    assert _extract_sql("SELECT 1").upper() == "SELECT 1"


def test_validate_rejects_delete():
    ok, msg = validate_readonly_select("DELETE FROM client")
    assert not ok


def test_validate_accepts_select():
    ok, sql = validate_readonly_select("SELECT id FROM client LIMIT 5")
    assert ok
    assert "LIMIT" in sql.upper()


def test_help_context_includes_workflow_creation():
    ctx = _build_knowledge_context("How to create a new workflow")
    assert "PROCEDURAL KNOWLEDGE" in ctx
    assert "New monthly investment" in ctx
    assert "Monthly investments" in ctx
    assert "FUNDS" in ctx
    assert "Navigation quick map" in ctx


def test_codebase_context_has_monthly_investment_route():
    ctx = build_codebase_context("create workflow monthly investment")
    assert "read-only" in ctx.lower()
    assert "new_monthly_investment" in ctx.lower() or "New monthly investment" in ctx


def test_whitelist_blocks_arbitrary_paths():
    assert resolve_whitelisted_read_path("routes/main.py") is None
    assert resolve_whitelisted_read_path("../../../etc/passwd") is None
    assert resolve_whitelisted_read_path("monthly_investments.py") is not None


def test_doc_write_rejects_code_paths():
    from pathlib import Path

    with pytest.raises(PermissionError):
        assert_help_doc_write_path(Path("monthly_investments.py"))
    with pytest.raises(PermissionError):
        assert_help_doc_write_path(Path("routes/assistant.py"))


def test_fast_path_security_model_from_client():
    result = ask_help_assistant("How can I create a new security model of existing client")
    assert result["success"]
    assert result.get("fast_path") is True
    assert "Create Security Model by Import" in result["reply"]
    assert "Allocation models" in result["reply"]
    assert "New monthly investment" not in result["reply"]


def test_fast_path_create_workflow():
    result = ask_help_assistant("How to create a new workflow")
    assert result["success"]
    assert result.get("fast_path") is True
    assert "New monthly investment" in result["reply"]
    assert "Go to the **Maintenance**" not in result["reply"]
    assert result["elapsed_ms"] < 500


def test_fast_path_upload_trades():
    result = ask_help_assistant("How do I upload trades?")
    assert result["success"]
    assert result.get("fast_path") is True
    assert "Trade upload" in result["reply"]


def test_fast_path_generate_recommendations():
    result = ask_help_assistant("How to generate recommendations")
    assert result["success"]
    assert result.get("fast_path") is True
    assert "Generate new recommendations" in result["reply"]
