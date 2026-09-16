#!/usr/bin/env python3
"""Disable legacy strategy rows: defer_to_ltcg, realised_loss_review, section_94_8_ranking_overlay."""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from sqlalchemy import text

_CODES = ("defer_to_ltcg", "realised_loss_review", "section_94_8_ranking_overlay")


def upgrade():
    app = create_app()
    with app.app_context():
        for code in _CODES:
            db.session.execute(
                text("UPDATE tax_optimiser_strategy SET enabled = 0 WHERE code = :c"),
                {"c": code},
            )
        db.session.commit()
        print("Disabled strategies:", ", ".join(_CODES))


if __name__ == "__main__":
    upgrade()
