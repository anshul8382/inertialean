#!/usr/bin/env python3
"""WhatsApp Groups monitoring tables and message columns.

Run on prod at cutover: python migrations/add_whatsapp_groups_tables.py

Listed in docs/DB_CUTOVER_REGISTRY.md (optional module — group UI degrades until run).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text

from main import create_app
from extensions import db


def _column_names(engine, table_name: str):
    insp = inspect(engine)
    if table_name not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table_name)}


def main():
    app = create_app()
    with app.app_context():
        from models.whatsapp_models import WhatsAppGroup, WhatsAppMessage

        engine = db.engine
        WhatsAppGroup.__table__.create(engine, checkfirst=True)

        if "whatsapp_messages" in inspect(engine).get_table_names():
            cols = _column_names(engine, "whatsapp_messages")
            alters = []
            if "group_id" not in cols:
                alters.append(
                    "ADD COLUMN group_id VARCHAR(255) NULL"
                )
            if "direction" not in cols:
                alters.append("ADD COLUMN direction VARCHAR(10) NULL")
            if "sender_wa_id" not in cols:
                alters.append("ADD COLUMN sender_wa_id VARCHAR(32) NULL")
            for clause in alters:
                db.session.execute(text(f"ALTER TABLE whatsapp_messages {clause}"))
            if alters:
                db.session.commit()
                print("whatsapp_messages: added columns", [a.split()[2] for a in alters])
            idx_name = "idx_whatsapp_messages_group_id"
            existing_indexes = {i["name"] for i in inspect(engine).get_indexes("whatsapp_messages")}
            if "group_id" in _column_names(engine, "whatsapp_messages") and idx_name not in existing_indexes:
                try:
                    db.session.execute(
                        text(f"CREATE INDEX {idx_name} ON whatsapp_messages (group_id)")
                    )
                    db.session.commit()
                    print(f"Created index {idx_name}")
                except Exception as exc:
                    db.session.rollback()
                    print(f"Index {idx_name} skipped: {exc}")

        print("whatsapp_groups table ready")


if __name__ == "__main__":
    main()
