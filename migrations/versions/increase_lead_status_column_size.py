"""Increase lead status column size

Revision ID: increase_lead_status_size
Revises: add_lead_is_active
Create Date: 2025-08-25 10:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'increase_lead_status_size'
down_revision = 'add_lead_is_active'
branch_labels = None
depends_on = None


def upgrade():
    # Increase the status column size from 20 to 50 characters
    op.alter_column('lead', 'status',
                    existing_type=sa.String(length=20),
                    type_=sa.String(length=50),
                    existing_nullable=True)


def downgrade():
    # Revert the status column size back to 20 characters
    op.alter_column('lead', 'status',
                    existing_type=sa.String(length=50),
                    type_=sa.String(length=20),
                    existing_nullable=True)
