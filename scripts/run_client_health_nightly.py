#!/usr/bin/env python3
"""
Local / pre-prod smoke test for Client Health nightly.

Prereq: MySQL reachable per .env (this repo uses DB_HOST=127.0.0.1 DB_PORT=3307 —
typically an SSH tunnel to the server DB).

Examples:
  # Deterministic only (no Ollama) — safest first pass
  python3 scripts/run_client_health_nightly.py --no-llm

  # Use local Ollama if available
  python3 scripts/run_client_health_nightly.py

  # Dry collectors only (no interpret / digests write beyond observations? full cycle always)
  python3 scripts/run_client_health_nightly.py --no-llm --print-summary

Never set CLIENT_HEALTH_SEND_DIGESTS=1 until you have reviewed digests_*.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip Ollama; use deterministic phrase fallback only",
    )
    ap.add_argument(
        "--print-summary",
        action="store_true",
        help="Pretty-print JSON summary to stdout",
    )
    ap.add_argument(
        "--check-db",
        action="store_true",
        help="Only verify DB connectivity, then exit",
    )
    ap.add_argument(
        "--max-clients",
        type=int,
        default=None,
        help="Interpret only the N densest clients (local LLM smoke). Collectors still full.",
    )
    args = ap.parse_args()

    # Digests stay draft unless user explicitly enables send in env
    if os.environ.get("CLIENT_HEALTH_SEND_DIGESTS", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        print(
            "WARNING: CLIENT_HEALTH_SEND_DIGESTS is enabled — emails may send.",
            file=sys.stderr,
        )

    # Preflight without full Flask boot (create_app may connect eagerly)
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    host = os.environ.get("DB_HOST", "127.0.0.1")
    port = int(os.environ.get("DB_PORT", "3306"))
    name = os.environ.get("DB_NAME", "?")
    user = os.environ.get("DB_USER", "?")
    password = os.environ.get("DB_PASSWORD", "")
    print(f"DB target: {user}@{host}:{port}/{name}")

    try:
        import pymysql

        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=name,
            connect_timeout=5,
        )
        conn.close()
        print("DB: connected OK")
    except Exception as exc:
        print(f"DB: FAILED — {exc}", file=sys.stderr)
        print(
            "Open the SSH tunnel (local 3307 → server MySQL), then retry.",
            file=sys.stderr,
        )
        print(
            "  Example: ssh -L 3307:127.0.0.1:3306 root@YOUR_SERVER -N",
            file=sys.stderr,
        )
        return 1

    if args.check_db:
        return 0

    from main import create_app

    app = create_app()
    with app.app_context():
        from services.client_health_nightly_service import run_client_health_nightly

        use_llm = False if args.no_llm else None
        print(
            f"Running client health nightly "
            f"(use_llm={use_llm!r}, max_clients={args.max_clients!r})…"
        )
        summary = run_client_health_nightly(
            use_llm=use_llm,
            max_clients=args.max_clients,
        )
        print(json.dumps(summary, indent=2, default=str))

        pack_dir = ROOT / "var" / "client_health"
        print(f"\nPacks directory: {pack_dir}")
        for name in (
            "latest_observations.json",
            "latest_interpretations.json",
        ):
            p = pack_dir / name
            print(f"  {'OK' if p.is_file() else 'MISSING'}: {p}")
        digests = sorted(pack_dir.glob("digests_*.json"))
        if digests:
            print(f"  latest digest: {digests[-1]}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
