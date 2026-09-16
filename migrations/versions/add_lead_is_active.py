"""add lead is_active field

Revision ID: add_lead_is_active
Revises: fix_lead_cascade_delete
Create Date: 2024-01-15 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_lead_is_active'
down_revision = 'fix_lead_cascade_delete'
branch_labels = None
depends_on = None


def upgrade():
    # Add is_active column to lead table
    op.add_column('lead', sa.Column('is_active', sa.Boolean(), nullable=True, server_default='1'))


def downgrade():
    # Remove is_active column from lead table
    op.drop_column('lead', 'is_active') 