#!/usr/bin/env python3
"""One-off script to re-sanitize all bank statement descriptions in the database."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from models import BankStatementTransaction
from services.bank_statement_service import sanitize_description


def main():
    app = create_app()
    with app.app_context():
        txns = BankStatementTransaction.query.filter(BankStatementTransaction.description.isnot(None)).all()
        updated = 0
        for t in txns:
            if t.description:
                new_val = sanitize_description(t.description)
                if t.description_sanitized != new_val:
                    t.description_sanitized = new_val
                    updated += 1
        db.session.commit()
        print(f"Updated {updated} of {len(txns)} transaction descriptions")


if __name__ == '__main__':
    main()
