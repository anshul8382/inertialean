"""make lead email nullable

Revision ID: make_lead_email_nullable
Revises: add_risk_profile_updated_at
Create Date: 2024-01-15 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'make_lead_email_nullable'
down_revision = 'add_risk_profile_updated_at'
branch_labels = None
depends_on = None


def upgrade():
    # Make email column nullable in lead table
    op.alter_column('lead', 'email',
                    existing_type=sa.String(120),
                    nullable=True)


def downgrade():
    # Make email column not nullable in lead table
    op.alter_column('lead', 'email',
                    existing_type=sa.String(120),
                    nullable=False) 