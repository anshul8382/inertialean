"""Safe DDL helpers for migrations (validated identifiers, no f-strings in execute())."""
from __future__ import annotations

import re

from sqlalchemy import text

_IDENT = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,127}$")


def _quote_ident(name: str) -> str:
    if not _IDENT.match(name or ""):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return "`" + name.replace("`", "") + "`"


def drop_table_if_exists(session, table_name: str) -> None:
    tbl = _quote_ident(table_name)
    session.execute(text("DROP TABLE IF EXISTS " + tbl))


def alter_table_drop_column(session, table_name: str, column_name: str) -> None:
    tbl = _quote_ident(table_name)
    col = _quote_ident(column_name)
    session.execute(text("ALTER TABLE " + tbl + " DROP COLUMN " + col))


def alter_table_drop_foreign_key(session, table_name: str, fk_name: str) -> None:
    tbl = _quote_ident(table_name)
    fk = _quote_ident(fk_name)
    session.execute(text("ALTER TABLE " + tbl + " DROP FOREIGN KEY " + fk))


def alter_table_add_column(session, table_name: str, column_definition: str) -> None:
    """column_definition e.g. 'budget_opt_in TINYINT(1) NULL' from trusted migration code."""
    if not column_definition or ";" in column_definition:
        raise ValueError("Invalid column definition")
    tbl = _quote_ident(table_name)
    session.execute(text("ALTER TABLE " + tbl + " ADD COLUMN " + column_definition.strip()))


def alter_table_clause(session, table_name: str, clause: str) -> None:
    """Run ALTER TABLE with a pre-built clause from trusted migration code only."""
    if not clause or not clause.strip():
        raise ValueError("Empty ALTER clause")
    if ";" in clause:
        raise ValueError("Multiple statements not allowed")
    tbl = _quote_ident(table_name)
    session.execute(text("ALTER TABLE " + tbl + " " + clause.strip()))

