#!/usr/bin/env python3
"""
Assign every client (advisor_id + history) and every lead (user_id) to one advisor user.

Usage:
  python scripts/assign_all_clients_and_leads_to_advisor.py --username sharveen
  python scripts/assign_all_clients_and_leads_to_advisor.py --username sharveen --dry-run
"""
from __future__ import annotations

import argparse
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _require_db_tunnel() -> None:
    """Fail fast when local .env expects SSH-forwarded MySQL (DB_PORT, default 3307)."""
    try:
        from dotenv import load_dotenv

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        load_dotenv(os.path.join(root, ".env"))
    except ImportError:
        pass
    host = os.environ.get("DB_HOST", "127.0.0.1")
    port = int(os.environ.get("DB_PORT", "3306"))
    if host not ("127.0.0.1", "localhost"):
        return
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        sock.connect((host, port))
    except OSError:
        print(
            f"\nERROR: Cannot connect to MySQL at {host}:{port}.\n\n"
            "Your .env uses a local port forwarded from the server. "
            "Open a **separate** terminal, run this, and leave it open:\n\n"
            f"  ssh -L {port}:127.0.0.1:3306 root@66.116.199.231\n\n"
            "Then run this script again.\n\n"
            "Alternatively, SSH to the server and run the script there "
            "(DB is on localhost:3306 on the server):\n\n"
            "  ssh root@66.116.199.231\n"
            "  cd /home/inertia/app   # or your app path\n"
            "  venv/bin/python scripts/assign_all_clients_and_leads_to_advisor.py "
            "--username sharveen --dry-run\n",
            file=sys.stderr,
        )
        raise SystemExit(1)
    finally:
        sock.close()

from sqlalchemy import func

from extensions import db
from main import create_app
from models import Client, Lead, User
from services.client_advisor_assignment_service import (
    bulk_assign_clients_to_advisor,
    bulk_assign_leads_to_user,
    get_client_ids_for_assignment,
    get_lead_ids_for_assignment,
)


def find_user(username: str) -> User | None:
    term = username.strip().lower()
    return (
        User.query.filter(
            func.lower(User.username) == term,
        )
        .first()
        or User.query.filter(func.lower(User.email).like(f"{term}@%")).first()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--username",
        default="sharveen",
        help="Advisor username (default: sharveen)",
    )
    parser.add_argument(
        "--assigned-by",
        type=int,
        default=None,
        help="User id for assignment audit (default: same as advisor)",
    )
    parser.add_argument(
        "--notes",
        default="Bulk assign all clients and leads to advisor (script)",
    )
    parser.add_argument(
        "--active-clients-only",
        action="store_true",
        help="Only assign active clients (default: all clients)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts only; do not write",
    )
    args = parser.parse_args()
    _require_db_tunnel()

    app = create_app()
    with app.app_context():
        advisor = find_user(args.username)
        if not advisor:
            print(f"ERROR: No user found for username '{args.username}'")
            return 1
        if not advisor.is_active:
            print(f"ERROR: User {advisor.username} (id={advisor.id}) is inactive")
            return 1

        assigned_by = args.assigned_by or advisor.id
        client_ids, truncated = get_client_ids_for_assignment(
            active_only=args.active_clients_only,
        )
        lead_ids, leads_truncated = get_lead_ids_for_assignment(active_only=False)
        clients_already = Client.query.filter(Client.advisor_id == advisor.id).count()
        leads_already = Lead.query.filter(Lead.user_id == advisor.id).count()

        print(f"Advisor: {advisor.username} (id={advisor.id}, email={advisor.email})")
        print(f"Clients to process: {len(client_ids)} (already on advisor: {clients_already})")
        if truncated:
            print("WARNING: client list truncated at 5000")
        print(f"Leads to process: {len(lead_ids)} (already owner: {leads_already})")
        if leads_truncated:
            print("WARNING: lead list truncated at 5000")

        if args.dry_run:
            print("Dry run — no changes made.")
            return 0

        client_result = bulk_assign_clients_to_advisor(
            client_ids,
            advisor.id,
            assigned_by,
            notes=args.notes,
        )
        print(
            "Clients:",
            f"assigned={client_result.get('assigned')}",
            f"unchanged={client_result.get('unchanged')}",
            f"failed={client_result.get('failed')}",
        )
        if client_result.get("errors"):
            for err in client_result["errors"]:
                print(f"  - {err}")

        lead_result = bulk_assign_leads_to_user(
            lead_ids,
            advisor.id,
            assigned_by,
        )
        print(
            "Leads:",
            f"assigned={lead_result.get('assigned')}",
            f"unchanged={lead_result.get('unchanged')}",
            f"failed={lead_result.get('failed')}",
        )
        if lead_result.get("errors"):
            for err in lead_result["errors"]:
                print(f"  - {err}")

        return 0 if client_result.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
