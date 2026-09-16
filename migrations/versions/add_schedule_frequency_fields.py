"""Add frequency and flexible amount fields to monthly_investment_schedule

Revision ID: add_schedule_frequency_fields
Revises: 
Create Date: 2025-01-16

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'add_schedule_frequency_fields'
down_revision = None  # Update this to the latest migration
branch_labels = None
depends_on = None


def upgrade():
    # Add new columns to monthly_investment_schedule table
    op.add_column('monthly_investment_schedule', 
                  sa.Column('change_frequency_months', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('monthly_investment_schedule', 
                  sa.Column('withdrawal_mode', sa.String(length=20), nullable=False, server_default='AD_HOC'))
    op.add_column('monthly_investment_schedule', 
                  sa.Column('withdrawal_frequency_months', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('monthly_investment_schedule', 
                  sa.Column('allow_zero_amount', sa.Boolean(), nullable=False, server_default='1'))
    op.add_column('monthly_investment_schedule', 
                  sa.Column('allow_negative_amount', sa.Boolean(), nullable=False, server_default='1'))


def downgrade():
    # Remove the columns
    op.drop_column('monthly_investment_schedule', 'allow_negative_amount')
    op.drop_column('monthly_investment_schedule', 'allow_zero_amount')
    op.drop_column('monthly_investment_schedule', 'withdrawal_frequency_months')
    op.drop_column('monthly_investment_schedule', 'withdrawal_mode')
    op.drop_column('monthly_investment_schedule', 'change_frequency_months')

