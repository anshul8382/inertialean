"""stub missing migration: add_2fa_policy_fields

Revision ID: add_2fa_policy_fields
Revises:
Create Date: 2026-01-11

This revision is referenced by `create_whatsapp_tables` but was missing from
`migrations/versions/`, which breaks the Alembic graph.

The repo also contains a standalone script `migrations/add_2fa_policy_fields.py`
that may have been executed manually in the past. To avoid re-applying schema
changes unpredictably across environments, this Alembic revision is a no-op and
exists purely to repair the revision graph.
"""

# revision identifiers, used by Alembic.
revision = "add_2fa_policy_fields"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Intentionally empty (graph repair only)
    pass


def downgrade():
    # Intentionally empty (graph repair only)
    pass


