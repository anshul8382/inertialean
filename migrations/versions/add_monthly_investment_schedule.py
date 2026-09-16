"""add monthly investment schedule table

Revision ID: add_monthly_schedule
Revises: add_generic_workflow_tables
Create Date: 2025-11-04 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'add_monthly_schedule'
down_revision = 'add_generic_workflow_tables'
branch_labels = None
depends_on = None


def upgrade():
    # Create monthly_investment_schedule table
    op.create_table('monthly_investment_schedule',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('planned_amount', sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column('day_of_month', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['client.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('client_id', name='unique_client_schedule')
    )


def downgrade():
    # Drop monthly_investment_schedule table
    op.drop_table('monthly_investment_schedule')

