"""Add referral_by field to lead table

Revision ID: add_referral_by_to_lead
Revises: complete_generic_workflow_setup
Create Date: 2025-06-21 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_referral_by_to_lead'
down_revision = 'complete_generic_workflow_setup'
branch_labels = None
depends_on = None


def upgrade():
    # Add referral_by column to lead table
    op.add_column('lead', sa.Column('referral_by', sa.String(100), nullable=True))


def downgrade():
    # Remove referral_by column from lead table
    op.drop_column('lead', 'referral_by') 