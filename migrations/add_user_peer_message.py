#!/usr/bin/env python3
"""Create user_peer_message table if missing.

Run: python3 migrations/add_user_peer_message.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db


def main():
    app = create_app()
    with app.app_context():
        from models.user_peer_message import UserPeerMessage
        import services.db_cutover as cutover

        UserPeerMessage.__table__.create(db.engine, checkfirst=True)
        cutover._peer_message_table_checked = None
        print("user_peer_message table ready")
        print("peer_messages_enabled =", cutover.peer_messages_enabled())


if __name__ == "__main__":
    main()
