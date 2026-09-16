"""add workflow tables

Revision ID: 2ab3db38e4bf
Revises: update_portfolio_item_foreign_key
Create Date: 2024-03-19 10:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError


# revision identifiers, used by Alembic.
revision = '2ab3db38e4bf'
down_revision = 'update_portfolio_item_foreign_key'
branch_labels = None
depends_on = None


def upgrade():
    # Create workflow table
    try:
        op.create_table('workflow',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('monthly_investment_id', sa.Integer(), nullable=False),
            sa.Column('current_stage', sa.String(length=20), nullable=True),
            sa.Column('planned_amount', sa.Numeric(precision=15, scale=2), nullable=False),
            sa.Column('actual_amount', sa.Numeric(precision=15, scale=2), nullable=True),
            sa.Column('investment_date', sa.Date(), nullable=False),
            sa.Column('target_completion_date', sa.Date(), nullable=False),
            sa.Column('actual_completion_date', sa.Date(), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('created_by', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['created_by'], ['user.id'], ),
            sa.ForeignKeyConstraint(['monthly_investment_id'], ['monthly_investment.id'], ),
            sa.PrimaryKeyConstraint('id')
        )
    except OperationalError as e:
        if "already exists" not in str(e):
            raise

    # Create workflow_action table
    try:
        op.create_table('workflow_action',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('workflow_id', sa.Integer(), nullable=False),
            sa.Column('action_type', sa.String(length=50), nullable=False),
            sa.Column('action_date', sa.DateTime(), nullable=False),
            sa.Column('amount', sa.Numeric(precision=15, scale=2), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
            sa.ForeignKeyConstraint(['workflow_id'], ['workflow.id'], ),
            sa.PrimaryKeyConstraint('id')
        )
    except OperationalError as e:
        if "already exists" not in str(e):
            raise


def downgrade():
    op.drop_table('workflow_action')
    op.drop_table('workflow')
