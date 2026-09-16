"""add risk profile updated at

Revision ID: add_risk_profile_updated_at
Revises: cf4203006e60_add_monthly_investment_and_workflow_
Create Date: 2024-01-15 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_risk_profile_updated_at'
# Fix: down_revision must reference the revision id, not the filename/docstring label
down_revision = 'cf4203006e60'
branch_labels = None
depends_on = None


def upgrade():
    # Add risk_profile_updated_at column to client table
    op.add_column('client', sa.Column('risk_profile_updated_at', sa.DateTime(), nullable=True))


def downgrade():
    # Remove risk_profile_updated_at column from client table
    op.drop_column('client', 'risk_profile_updated_at') 