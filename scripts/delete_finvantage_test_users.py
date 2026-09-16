#!/usr/bin/env python3
"""
Delete FinVantage planning data for test emails.
Run: cd /home/inertia/app && python scripts/delete_finvantage_test_users.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from models.finvantage_models import FinVantageUser
from sqlalchemy import func

TEST_EMAILS = [
    'erkhare.anshul@gmail.com',
    'anshul@equities4wealth.com',
    '1708.swati@gmail.com',
]


def main():
    app = create_app()
    with app.app_context():
        for email in TEST_EMAILS:
            user = FinVantageUser.query.filter(func.lower(FinVantageUser.email) == email.lower()).first()
            if user:
                from extensions import db
                db.session.delete(user)
                db.session.commit()
                print(f"Deleted: {email}")
            else:
                print(f"Not found: {email}")


if __name__ == '__main__':
    main()
