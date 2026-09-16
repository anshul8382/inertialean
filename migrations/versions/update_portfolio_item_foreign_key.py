"""stub missing migration: update_portfolio_item_foreign_key

Revision ID: update_portfolio_item_foreign_key
Revises:
Create Date: 2026-01-11

This file exists to repair the Alembic revision graph.
Several historical migrations reference `update_portfolio_item_foreign_key`
as their `down_revision`, but the revision file was missing from the repo.

No schema changes are applied here.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "update_portfolio_item_foreign_key"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Intentionally empty (graph repair only)
    pass


def downgrade():
    # Intentionally empty (graph repair only)
    pass


