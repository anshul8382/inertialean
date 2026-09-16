#!/usr/bin/env python3
"""Clone or data-refresh one MySQL schema into another via pymysql.

Full clone (default): DROP/CREATE target, copy structure + data.
Data-only: keep target schema; replace data in shared base tables.
  - Tables only on target (test-only): left alone
  - Extra columns on target: preserved; only common columns refreshed

Examples:
  # Local full seed from tunneled prod → local _dev (separate ports)
  python3 scripts/utilities/clone_mysql_database.py \\
    --source inertia_app2025 --target inertia_app2025_dev \\
    --source-host 127.0.0.1 --source-port 3307 \\
    --source-password '…' \\
    --target-host 127.0.0.1 --target-port 3306 \\
    --target-password local_inertia_dev

  # After prod cutover: refresh server test data only
  python3 scripts/utilities/clone_mysql_database.py \\
    --source inertia_app2025 --target inertia_app2025_test \\
    --host 127.0.0.1 --port 3307 --data-only --password '…'

Credentials: DB_* from env/.env, or --password / --source-password / --target-password.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Sequence, Tuple

import pymysql

BATCH = 2000


def load_dotenv() -> None:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def connect(host, port, user, password, db=None):
    kw = dict(
        host=host,
        port=port,
        user=user,
        password=password,
        charset="utf8mb4",
        autocommit=False,
        connect_timeout=30,
        read_timeout=600,
        write_timeout=600,
    )
    if db:
        kw["database"] = db
    return pymysql.connect(**kw)


def strip_definer(sql: str) -> str:
    return re.sub(r"DEFINER=`[^`]+`@`[^`]+`\s*", "", sql)


def list_base_and_views(cur, schema: str) -> Tuple[List[str], List[str]]:
    cur.execute(
        """
        SELECT table_name, table_type
        FROM information_schema.tables
        WHERE table_schema=%s
        ORDER BY table_type DESC, table_name
        """,
        (schema,),
    )
    rows = cur.fetchall()
    base = [n for n, typ in rows if typ == "BASE TABLE"]
    views = [n for n, typ in rows if typ == "VIEW"]
    return base, views


def column_names(cur, schema: str, table: str) -> List[str]:
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema=%s AND table_name=%s
        ORDER BY ordinal_position
        """,
        (schema, table),
    )
    return [r[0] for r in cur.fetchall()]


def copy_table_data(
    sc,
    dc,
    source: str,
    target: str,
    name: str,
    cols: Sequence[str],
) -> int:
    if not cols:
        return 0
    col_list = ",".join(f"`{c}`" for c in cols)
    placeholders = ",".join(["%s"] * len(cols))
    sc.execute(f"SELECT {col_list} FROM `{source}`.`{name}`")
    insert = f"INSERT INTO `{name}` ({col_list}) VALUES ({placeholders})"
    copied = 0
    while True:
        rows = sc.fetchmany(BATCH)
        if not rows:
            break
        dc.executemany(insert, rows)
        copied += len(rows)
        if copied % (BATCH * 5) == 0:
            dc.connection.commit()
    dc.connection.commit()
    return copied


def refresh_data_only(
    source: str,
    target: str,
    source_host: str,
    source_port: int,
    target_host: str,
    target_port: int,
    source_user: str,
    source_password: str,
    target_user: str,
    target_password: str,
) -> None:
    t0 = time.time()
    print(
        f"=== Data-only {source}@{source_host}:{source_port} "
        f"→ {target}@{target_host}:{target_port} ===",
        flush=True,
    )
    src = connect(source_host, source_port, source_user, source_password, source)
    dst = connect(target_host, target_port, target_user, target_password, target)
    sc, dc = src.cursor(), dst.cursor()

    src_tables, _ = list_base_and_views(sc, source)
    dst_tables, _ = list_base_and_views(dc, target)
    shared = sorted(set(src_tables) & set(dst_tables))
    only_src = sorted(set(src_tables) - set(dst_tables))
    only_dst = sorted(set(dst_tables) - set(src_tables))

    if only_src:
        print(f"  WARN missing on target (skip data): {', '.join(only_src[:20])}"
              + ("…" if len(only_src) > 20 else ""), flush=True)
    if only_dst:
        print(f"  keep test-only tables: {', '.join(only_dst[:20])}"
              + ("…" if len(only_dst) > 20 else ""), flush=True)
    print(f"  refreshing {len(shared)} shared tables", flush=True)

    dc.execute("SET FOREIGN_KEY_CHECKS=0")
    dc.execute("SET UNIQUE_CHECKS=0")
    dst.commit()

    for i, name in enumerate(shared, 1):
        src_cols = column_names(sc, source, name)
        dst_cols = column_names(dc, target, name)
        cols = [c for c in src_cols if c in set(dst_cols)]
        if not cols:
            print(f"  [{i}/{len(shared)}] {name}: no common columns — skip", flush=True)
            continue
        extra = [c for c in dst_cols if c not in set(src_cols)]
        dc.execute(f"DELETE FROM `{name}`")
        dst.commit()
        copied = copy_table_data(sc, dc, source, target, name, cols)
        note = f" (+keep cols: {','.join(extra)})" if extra else ""
        if i % 15 == 0 or copied > 50000 or extra:
            print(f"  [{i}/{len(shared)}] {name}: {copied} rows{note}", flush=True)

    dc.execute("SET FOREIGN_KEY_CHECKS=1")
    dc.execute("SET UNIQUE_CHECKS=1")
    dst.commit()
    src.close()
    dst.close()
    print(f"DONE data-only → {target} in {time.time() - t0:.1f}s", flush=True)


def clone_full(
    source: str,
    target: str,
    source_host: str,
    source_port: int,
    target_host: str,
    target_port: int,
    source_user: str,
    source_password: str,
    target_user: str,
    target_password: str,
) -> None:
    t0 = time.time()
    same_server = (source_host, source_port) == (target_host, target_port)
    print(
        f"=== Full clone {source}@{source_host}:{source_port} "
        f"→ {target}@{target_host}:{target_port} ===",
        flush=True,
    )

    admin = connect(target_host, target_port, target_user, target_password)
    cur = admin.cursor()
    try:
        cur.execute(f"DROP DATABASE IF EXISTS `{target}`")
        cur.execute(
            f"CREATE DATABASE `{target}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        admin.commit()
    except pymysql.err.OperationalError as e:
        if e.args and e.args[0] == 1044:
            print(
                f"Access denied creating `{target}` on {target_host}:{target_port}.\n"
                "Create the database (and GRANT) as MySQL root first.",
                file=sys.stderr,
            )
        raise
    finally:
        admin.close()
    print(f"  recreated {target}", flush=True)

    src = connect(source_host, source_port, source_user, source_password, source)
    dst = connect(target_host, target_port, target_user, target_password, target)
    sc, dc = src.cursor(), dst.cursor()

    base_tables, views = list_base_and_views(sc, source)
    print(f"  {len(base_tables)} tables, {len(views)} views", flush=True)

    dc.execute("SET FOREIGN_KEY_CHECKS=0")
    dc.execute("SET UNIQUE_CHECKS=0")
    dst.commit()

    for i, name in enumerate(base_tables, 1):
        sc.execute(f"SHOW CREATE TABLE `{source}`.`{name}`")
        create_sql = sc.fetchone()[1]
        dc.execute(f"DROP TABLE IF EXISTS `{name}`")
        dc.execute(create_sql)
        sc.execute(f"SELECT COUNT(*) FROM `{source}`.`{name}`")
        total = sc.fetchone()[0]
        copied = 0
        if total:
            sc.execute(f"SELECT * FROM `{source}`.`{name}`")
            cols = [d[0] for d in sc.description]
            placeholders = ",".join(["%s"] * len(cols))
            col_list = ",".join(f"`{c}`" for c in cols)
            insert = f"INSERT INTO `{name}` ({col_list}) VALUES ({placeholders})"
            while True:
                rows = sc.fetchmany(BATCH)
                if not rows:
                    break
                dc.executemany(insert, rows)
                copied += len(rows)
                if copied % (BATCH * 5) == 0 or copied == total:
                    dst.commit()
        dst.commit()
        if i % 20 == 0 or total > 50000:
            print(f"  [{i}/{len(base_tables)}] {name}: {copied} rows", flush=True)

    pending = views[:]
    for attempt in range(5):
        if not pending:
            break
        still = []
        for name in pending:
            try:
                sc.execute(f"SHOW CREATE VIEW `{source}`.`{name}`")
                create_sql = strip_definer(sc.fetchone()[1])
                if same_server:
                    create_sql = create_sql.replace(f"`{source}`.", f"`{target}`.")
                dc.execute(f"DROP VIEW IF EXISTS `{name}`")
                dc.execute(create_sql)
            except Exception as e:
                still.append(name)
                if attempt == 4:
                    print(f"  WARN view {name}: {e}", flush=True)
        pending = still

    for kind, show in (("PROCEDURE", "PROCEDURE"), ("FUNCTION", "FUNCTION")):
        sc.execute(
            """SELECT routine_name FROM information_schema.routines
               WHERE routine_schema=%s AND routine_type=%s""",
            (source, kind),
        )
        for (rname,) in sc.fetchall():
            try:
                sc.execute(f"SHOW CREATE {show} `{source}`.`{rname}`")
                row = sc.fetchone()
                create_sql = strip_definer(row[2] if len(row) > 2 else row[-1])
                if same_server:
                    create_sql = create_sql.replace(f"`{source}`.", f"`{target}`.")
                dc.execute(f"DROP {show} IF EXISTS `{rname}`")
                dc.execute(create_sql)
            except Exception as e:
                print(f"  WARN {kind} {rname}: {e}", flush=True)
    dst.commit()

    dc.execute("SET FOREIGN_KEY_CHECKS=1")
    dc.execute("SET UNIQUE_CHECKS=1")
    dst.commit()

    sc.execute(
        """SELECT COUNT(*) FROM information_schema.tables
           WHERE table_schema=%s AND table_type='BASE TABLE'""",
        (source,),
    )
    src_n = sc.fetchone()[0]
    dc.execute(
        """SELECT COUNT(*) FROM information_schema.tables
           WHERE table_schema=%s AND table_type='BASE TABLE'""",
        (target,),
    )
    dst_n = dc.fetchone()[0]
    src.close()
    dst.close()
    print(f"DONE {target}: {dst_n}/{src_n} base tables in {time.time() - t0:.1f}s", flush=True)
    if dst_n != src_n:
        raise SystemExit(f"Table count mismatch for {target}: {dst_n} vs {src_n}")


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--data-only", action="store_true", help="Refresh data only; keep target schema")
    p.add_argument("--host", default=None, help="Shared host (sets both source and target)")
    p.add_argument("--port", type=int, default=None, help="Shared port")
    p.add_argument("--source-host", default=None)
    p.add_argument("--source-port", type=int, default=None)
    p.add_argument("--target-host", default=None)
    p.add_argument("--target-port", type=int, default=None)
    p.add_argument("--user", default=os.environ.get("DB_USER", "inertia_admin"))
    p.add_argument("--password", default=None, help="Shared password")
    p.add_argument("--source-user", default=None)
    p.add_argument("--source-password", default=None)
    p.add_argument("--target-user", default=None)
    p.add_argument("--target-password", default=None)
    args = p.parse_args()

    default_host = os.environ.get("DB_HOST", "127.0.0.1")
    default_port = int(os.environ.get("DB_PORT", "3306"))
    default_password = args.password or os.environ.get("DB_PASSWORD", "")

    source_host = args.source_host or args.host or default_host
    target_host = args.target_host or args.host or default_host
    source_port = args.source_port if args.source_port is not None else (
        args.port if args.port is not None else default_port
    )
    target_port = args.target_port if args.target_port is not None else (
        args.port if args.port is not None else default_port
    )
    source_user = args.source_user or args.user
    target_user = args.target_user or args.user
    source_password = args.source_password or default_password
    target_password = args.target_password or default_password

    if not source_password or not target_password:
        raise SystemExit("Set passwords via --password / --source-password / --target-password or DB_PASSWORD")

    kwargs = dict(
        source=args.source,
        target=args.target,
        source_host=source_host,
        source_port=source_port,
        target_host=target_host,
        target_port=target_port,
        source_user=source_user,
        source_password=source_password,
        target_user=target_user,
        target_password=target_password,
    )
    if args.data_only:
        refresh_data_only(**kwargs)
    else:
        clone_full(**kwargs)


if __name__ == "__main__":
    main()
