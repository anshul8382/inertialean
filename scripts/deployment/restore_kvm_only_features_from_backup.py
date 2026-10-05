#!/usr/bin/env python3
"""Extract KVM-only feature data from a pre-refresh mysqldump (.sql.gz).

Writes a merge SQL file: TRUNCATE+INSERT for KVM-only tables and client Drive columns.

Usage:
  python3 scripts/deployment/restore_kvm_only_features_from_backup.py \\
    backups/kvm_inertia_app2025_YYYYMMDD.sql.gz /tmp/kvm_feature_merge.sql
"""
from __future__ import annotations

import gzip
import re
import sys
from typing import Iterator, List, Optional, Tuple

FEATURE_TABLES = (
    "advisory_register_entry",
    "finding_notification_decision",
    "user_peer_message",
    "suitability_report",
)

CLIENT_COLS = (
    "id",
    "name",
    "email",
    "phone",
    "address",
    "risk_profile",
    "created_at",
    "user_id",
    "advisor_id",
    "date_of_birth",
    "risk_profile_updated_at",
    "whatsapp_number",
    "starting_aua",
    "designation",
    "linkedin_profile_url",
    "date_of_joining",
    "type_of_engagement",
    "portfolio_inherited",
    "company_name",
    "industry",
    "planning_synopsis",
    "background_notes",
    "other_notes",
    "is_active",
    "secondary_emails",
    "google_drive_folder_id",
    "google_drive_folder_note",
)


def _unescape_mysql_string(raw: str) -> str:
    out: List[str] = []
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == "\\" and i + 1 < len(raw):
            n = raw[i + 1]
            mapping = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", "'": "'", '"': '"', "0": "\0"}
            out.append(mapping.get(n, n))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _parse_sql_value(text: str, pos: int) -> Tuple[Optional[str], int]:
    while pos < len(text) and text[pos] in " \t\n\r":
        pos += 1
    if pos >= len(text):
        return None, pos
    if text.startswith("NULL", pos):
        return None, pos + 4
    if text[pos] == "'":
        pos += 1
        buf: List[str] = []
        while pos < len(text):
            c = text[pos]
            if c == "\\" and pos + 1 < len(text):
                buf.append(c)
                buf.append(text[pos + 1])
                pos += 2
                continue
            if c == "'":
                if pos + 1 < len(text) and text[pos + 1] == "'":
                    buf.append("'")
                    pos += 2
                    continue
                pos += 1
                return _unescape_mysql_string("".join(buf)), pos
            buf.append(c)
            pos += 1
        raise ValueError("unterminated string in VALUES")
    m = re.match(r"-?\d+(?:\.\d+)?", text[pos:])
    if m:
        return m.group(0), pos + len(m.group(0))
    raise ValueError(f"unexpected value at {pos}: {text[pos : pos + 40]!r}")


def _parse_row(text: str, pos: int, ncols: int) -> Tuple[List[Optional[str]], int]:
    while pos < len(text) and text[pos] in " \t\n\r":
        pos += 1
    if text[pos] != "(":
        raise ValueError("expected '('")
    pos += 1
    vals: List[Optional[str]] = []
    for i in range(ncols):
        if i:
            while pos < len(text) and text[pos] in " \t\n\r":
                pos += 1
            if text[pos] != ",":
                raise ValueError("expected comma between columns")
            pos += 1
        v, pos = _parse_sql_value(text, pos)
        vals.append(v)
    while pos < len(text) and text[pos] in " \t\n\r":
        pos += 1
    if text[pos] != ")":
        raise ValueError("expected ')'")
    return vals, pos + 1


def _iter_client_rows(values_blob: str) -> Iterator[List[Optional[str]]]:
    pos = 0
    ncols = len(CLIENT_COLS)
    while pos < len(values_blob):
        while pos < len(values_blob) and values_blob[pos] in " \t\n\r,":
            pos += 1
        if pos >= len(values_blob):
            break
        row, pos = _parse_row(values_blob, pos, ncols)
        yield row


def _sql_quote(s: Optional[str]) -> str:
    if s is None:
        return "NULL"
    return "'" + s.replace("\\", "\\\\").replace("'", "''") + "'"


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    dump_path, out_path = sys.argv[1], sys.argv[2]
    inserts: dict[str, str] = {}
    client_values: Optional[str] = None

    with gzip.open(dump_path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            for t in FEATURE_TABLES:
                prefix = f"INSERT INTO `{t}`"
                if line.startswith(prefix):
                    inserts[t] = line.strip().rstrip(";")
            if line.startswith("INSERT INTO `client`"):
                m = re.search(r"VALUES\s*(.*);?\s*$", line, re.DOTALL)
                if m:
                    client_values = m.group(1).strip().rstrip(";")

    if len(inserts) != len(FEATURE_TABLES):
        missing = set(FEATURE_TABLES) - set(inserts)
        print(f"warning: missing INSERT for {missing}", file=sys.stderr)

    drive_rows: List[Tuple[str, Optional[str], Optional[str]]] = []
    if client_values:
        idx_id = CLIENT_COLS.index("id")
        idx_drive = CLIENT_COLS.index("google_drive_folder_id")
        idx_note = CLIENT_COLS.index("google_drive_folder_note")
        for row in _iter_client_rows(client_values):
            cid = row[idx_id]
            drive = row[idx_drive]
            note = row[idx_note]
            if (drive and drive.strip()) or (note and note.strip()):
                drive_rows.append((cid or "", drive, note))

    lines: List[str] = [
        "SET FOREIGN_KEY_CHECKS=0;",
        "",
    ]
    for t in FEATURE_TABLES:
        if t not in inserts:
            continue
        lines.append(f"TRUNCATE TABLE `{t}`;")
        lines.append(inserts[t] + ";")
        lines.append("")

    if drive_rows:
        lines.append("DROP TABLE IF EXISTS `_kvm_client_drive_restore`;")
        lines.append(
            "CREATE TABLE `_kvm_client_drive_restore` ("
            "`id` INT NOT NULL PRIMARY KEY,"
            "`google_drive_folder_id` VARCHAR(128) NULL,"
            "`google_drive_folder_note` VARCHAR(255) NULL"
            ") ENGINE=InnoDB;"
        )
        for cid, drive, note in drive_rows:
            lines.append(
                "INSERT INTO `_kvm_client_drive_restore` (id, google_drive_folder_id, google_drive_folder_note) "
                f"VALUES ({int(cid)}, {_sql_quote(drive)}, {_sql_quote(note)});"
            )
        lines.append(
            "UPDATE `client` c "
            "INNER JOIN `_kvm_client_drive_restore` k ON k.id = c.id "
            "SET c.google_drive_folder_id = k.google_drive_folder_id, "
            "c.google_drive_folder_note = k.google_drive_folder_note;"
        )
        lines.append("DROP TABLE `_kvm_client_drive_restore`;")
        lines.append("")

    lines.append("SET FOREIGN_KEY_CHECKS=1;")
    lines.append("")

    with open(out_path, "w", encoding="utf-8") as out:
        out.write("\n".join(lines))

    print(f"wrote {out_path}")
    for t in FEATURE_TABLES:
        print(f"  {t}: {'yes' if t in inserts else 'MISSING'}")
    print(f"  client drive rows: {len(drive_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
