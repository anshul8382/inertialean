#!/usr/bin/env python3
"""
Migration: Meeting team participants + Google Calendar fields on meeting.

- meeting.created_by, google_calendar_event_id, google_calendar_organizer_user_id
- meeting_participant (meeting_id, user_id)
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from sqlalchemy import inspect, text


def _table_exists(engine, name: str) -> bool:
    return name in inspect(engine).get_table_names()


def _column_exists(engine, table: str, column: str) -> bool:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade():
    app = create_app()
    with app.app_context():
        eng = db.engine
        try:
            if not _column_exists(eng, "meeting", "created_by"):
                db.session.execute(
                    text("ALTER TABLE meeting ADD COLUMN created_by INT NULL")
                )
                db.session.commit()
                db.session.execute(
                    text(
                        "ALTER TABLE meeting ADD CONSTRAINT meeting_created_by_fk "
                        "FOREIGN KEY (created_by) REFERENCES user(id) ON DELETE SET NULL"
                    )
                )
                db.session.commit()
                print("Added meeting.created_by")
            else:
                print("meeting.created_by already exists")

            if not _column_exists(eng, "meeting", "google_calendar_event_id"):
                db.session.execute(
                    text(
                        "ALTER TABLE meeting ADD COLUMN google_calendar_event_id VARCHAR(280) NULL"
                    )
                )
                db.session.commit()
                print("Added meeting.google_calendar_event_id")
            else:
                print("meeting.google_calendar_event_id already exists")

            if not _column_exists(eng, "meeting", "google_calendar_organizer_user_id"):
                db.session.execute(
                    text(
                        "ALTER TABLE meeting ADD COLUMN google_calendar_organizer_user_id INT NULL"
                    )
                )
                db.session.commit()
                db.session.execute(
                    text(
                        "ALTER TABLE meeting ADD CONSTRAINT meeting_gcal_org_fk "
                        "FOREIGN KEY (google_calendar_organizer_user_id) REFERENCES user(id) ON DELETE SET NULL"
                    )
                )
                db.session.commit()
                print("Added meeting.google_calendar_organizer_user_id")
            else:
                print("meeting.google_calendar_organizer_user_id already exists")

            if not _table_exists(eng, "meeting_participant"):
                db.session.execute(
                    text(
                        """
                        CREATE TABLE meeting_participant (
                            meeting_id INT NOT NULL,
                            user_id INT NOT NULL,
                            PRIMARY KEY (meeting_id, user_id),
                            CONSTRAINT mp_meeting_fk FOREIGN KEY (meeting_id)
                                REFERENCES meeting(id) ON DELETE CASCADE,
                            CONSTRAINT mp_user_fk FOREIGN KEY (user_id)
                                REFERENCES user(id) ON DELETE CASCADE
                        )
                        """
                    )
                )
                db.session.commit()
                print("Created meeting_participant")
            else:
                print("meeting_participant already exists")
        except Exception as e:
            db.session.rollback()
            print(f"Error: {e}")
            raise


if __name__ == "__main__":
    upgrade()
