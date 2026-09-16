"""Admin-only database browser and read-only SQL query helpers."""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone
from typing import Any

from flask import abort
from flask_login import current_user
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from extensions import db

try:
    from models import SQLQueryHistory
except Exception:  # pragma: no cover
    SQLQueryHistory = None  # type: ignore[misc, assignment]

_TABLE_NAME_RE = re.compile(r"^[a-zA-Z0-9_]{1,128}$")
_FORBIDDEN_SQL = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|REPLACE|"
    r"CALL|EXECUTE|MERGE|PROCEDURE|FUNCTION|TRIGGER|EVENT|"
    r"INTO\s+OUTFILE|DUMPFILE|LOAD\s+DATA|"
    r"LOCK\s+TABLES|UNLOCK\s+TABLES"
    r")\b",
    re.I | re.MULTILINE,
)
_FOR_UPDATE = re.compile(r"\bFOR\s+UPDATE\b", re.I)
_LOCK_SHARE = re.compile(r"\bLOCK\s+IN\s+SHARE\s+MODE\b", re.I)
_PII_COLUMN = re.compile(
    r"(pan|aadhaar|aadhar|password|bank_account|account_number|ifsc)",
    re.I,
)

_MAX_SQL_LEN = 100_000
_MAX_PREVIEW_ROWS = 1000
_MAX_QUERY_ROWS = 5000


def require_admin_user() -> None:
    if not current_user.is_authenticated or not getattr(current_user, "is_admin", False):
        abort(403)


def _inspector():
    return inspect(db.engine)


def _strip_sql_comments(sql: str) -> str:
    out_lines: list[str] = []
    for line in sql.splitlines():
        if "--" in line:
            line = line[: line.index("--")]
        out_lines.append(line)
    s = "\n".join(out_lines)
    while "/*" in s:
        start = s.find("/*")
        end = s.find("*/", start + 2)
        if end == -1:
            break
        s = s[:start] + " " + s[end + 2 :]
    return s


def validate_readonly_select(sql: str) -> tuple[bool, str]:
    if not sql or not sql.strip():
        return False, "Empty query"
    if len(sql) > _MAX_SQL_LEN:
        return False, "Query exceeds maximum length"
    body = _strip_sql_comments(sql).strip()
    if not body:
        return False, "Empty query after removing comments"
    trimmed = body.rstrip().rstrip(";").strip()
    inner = trimmed.rstrip(";")
    if ";" in inner:
        return False, "Multiple statements are not allowed"
    lead = inner.lstrip()[:24].upper()
    if not (lead.startswith("SELECT") or lead.startswith("WITH")):
        return False, "Only SELECT or WITH (CTE) queries are allowed"
    if _FORBIDDEN_SQL.search(inner):
        return False, "Query contains disallowed keywords"
    if _FOR_UPDATE.search(inner) or _LOCK_SHARE.search(inner):
        return False, "FOR UPDATE / LOCK IN SHARE MODE are not allowed"
    return True, inner


def _serialize_cell(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, (dict, list)):
        return value
    return value


def _mask_pii_value(column: str, value: Any) -> Any:
    if value is None or not _PII_COLUMN.search(column or ""):
        return _serialize_cell(value)
    s = str(value)
    if len(s) <= 4:
        return "****"
    return s[:2] + "****" + s[-2:]


def _row_mapping_to_dict(row: Any) -> dict[str, Any]:
    m = row._mapping if hasattr(row, "_mapping") else row
    return {k: _mask_pii_value(k, v) for k, v in dict(m).items()}


def build_database_browser_context() -> dict[str, Any]:
    insp = _inspector()
    tables = sorted(insp.get_table_names())
    schema: dict[str, list[dict[str, Any]]] = {}
    for tname in tables:
        pk_cols = set()
        try:
            pk = insp.get_pk_constraint(tname)
            pk_cols = set(pk.get("constrained_columns") or [])
        except Exception:
            pass
        fk_by_col: dict[str, str] = {}
        try:
            for fk in insp.get_foreign_keys(tname):
                refs = fk.get("referred_table") or ""
                rcols = fk.get("referred_columns") or []
                for i, col in enumerate(fk.get("constrained_columns") or []):
                    refc = rcols[i] if i < len(rcols) else ""
                    fk_by_col[col] = f"{refs}.{refc}" if refc else refs
        except Exception:
            pass
        cols_out: list[dict[str, Any]] = []
        try:
            for c in insp.get_columns(tname):
                cname = c["name"]
                default = c.get("default")
                if default is not None and not isinstance(default, (str, int, float, bool)):
                    default = str(default)
                cols_out.append(
                    {
                        "name": cname,
                        "type": str(c["type"]),
                        "nullable": bool(c.get("nullable", True)),
                        "default": default,
                        "primary_key": cname in pk_cols,
                        "foreign_key": fk_by_col.get(cname),
                    }
                )
        except Exception:
            cols_out = []
        schema[tname] = cols_out
    return {"tables": tables, "schema": schema}


def _quoted_table(name: str) -> str:
    if not _TABLE_NAME_RE.match(name or ""):
        raise ValueError("Invalid table name")
    return "`" + name.replace("`", "") + "`"


def preview_table_data(table_name: str, limit: int) -> dict[str, Any]:
    if not _TABLE_NAME_RE.match(table_name or ""):
        return {"error": "Invalid table name"}
    lim = max(1, min(int(limit or 50), _MAX_PREVIEW_ROWS))
    names = set(_inspector().get_table_names())
    if table_name not in names:
        return {"error": "Unknown table"}
    q = text("SELECT * FROM " + _quoted_table(table_name) + " LIMIT :lim")
    try:
        result = db.session.execute(q, {"lim": lim})
        rows = result.mappings().fetchall()
        try:
            from services.audit_service import log_audit_event

            log_audit_event(
                "database_browser_preview",
                user_id=getattr(current_user, "id", None),
                resource_type="database_table",
                resource_id=table_name,
                details={"row_limit": lim, "rows_returned": len(rows)},
            )
        except Exception:
            pass
        if not rows:
            return {"columns": list(result.keys()), "data": []}
        columns = list(rows[0].keys())
        data = [_row_mapping_to_dict(r) for r in rows]
        return {"columns": columns, "data": data}
    except SQLAlchemyError as e:
        return {"error": str(e)}


def build_sql_schema_api_payload() -> dict[str, Any]:
    try:
        ctx = build_database_browser_context()
        tables_payload: dict[str, Any] = {}
        for tname in ctx["tables"]:
            cols = ctx["schema"].get(tname) or []
            tables_payload[tname] = {
                "column_count": len(cols),
                "columns": [
                    {
                        "name": c["name"],
                        "type": c["type"],
                        "nullable": c["nullable"],
                        "primary_key": c["primary_key"],
                        "foreign_key": (
                            {
                                "references_table": (c["foreign_key"] or "").split(".")[0],
                                "references_column": (c["foreign_key"] or "").split(".")[-1]
                                if c.get("foreign_key") and "." in c["foreign_key"]
                                else "",
                            }
                            if c.get("foreign_key")
                            else None
                        ),
                        "indexed": False,
                    }
                    for c in cols
                ],
            }
        return {"success": True, "tables": tables_payload}
    except Exception as e:
        return {"success": False, "error": str(e)}


def execute_readonly_query(sql_body: str) -> tuple[list[dict[str, Any]], list[str], float | None, str | None]:
    """Returns (rows as dicts, column names, elapsed_ms, error_message)."""
    t0 = time.perf_counter()
    try:
        result = db.session.execute(text(sql_body))
        chunk = result.mappings().fetchmany(_MAX_QUERY_ROWS + 1)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if len(chunk) > _MAX_QUERY_ROWS:
            return [], [], elapsed_ms, f"Result has more than {_MAX_QUERY_ROWS} rows; add a LIMIT clause."
        if not chunk:
            keys = list(result.keys())
            return [], keys, elapsed_ms, None
        columns = list(chunk[0].keys())
        data = [_row_mapping_to_dict(r) for r in chunk]
        return data, columns, elapsed_ms, None
    except SQLAlchemyError as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return [], [], elapsed_ms, str(e)


def record_sql_history(
    user_id: int,
    query_text: str,
    *,
    success: bool,
    error_message: str | None,
    row_count: int | None,
    execution_ms: float | None,
) -> None:
    if SQLQueryHistory is None:
        return
    try:
        qh = hashlib.sha256(query_text.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        row = SQLQueryHistory.query.filter_by(user_id=user_id, query_hash=qh).first()
        if row:
            row.last_executed_at = now
            row.execute_count = (row.execute_count or 0) + 1
            row.last_success = success
            row.last_error = error_message
            row.last_row_count = row_count
            row.last_execution_ms = execution_ms
        else:
            db.session.add(
                SQLQueryHistory(
                    user_id=user_id,
                    query_text=query_text,
                    query_hash=qh,
                    created_at=now,
                    last_executed_at=now,
                    execute_count=1,
                    last_success=success,
                    last_error=error_message,
                    last_row_count=row_count,
                    last_execution_ms=execution_ms,
                )
            )
        try:
            from services.audit_service import log_audit_event

            log_audit_event(
                "sql_query_run",
                user_id=user_id,
                resource_type="sql_query",
                details={
                    "success": success,
                    "row_count": row_count,
                    "execution_ms": execution_ms,
                    "query_hash": qh[:16],
                    "query_preview": (query_text or "")[:200],
                    "error": (error_message or "")[:200] or None,
                },
                commit=False,
            )
        except Exception:
            pass
        db.session.commit()
    except Exception:
        db.session.rollback()


def recent_query_history_for_user(user_id: int, limit: int = 40) -> list[Any]:
    if SQLQueryHistory is None:
        return []
    try:
        return (
            SQLQueryHistory.query.filter_by(user_id=user_id)
            .order_by(SQLQueryHistory.last_executed_at.desc())
            .limit(limit)
            .all()
        )
    except Exception:
        return []
