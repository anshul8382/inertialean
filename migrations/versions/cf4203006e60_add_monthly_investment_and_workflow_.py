"""add monthly investment and workflow models

Revision ID: cf4203006e60
Revises: 2ab3db38e4bf
Create Date: 2024-03-19 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'cf4203006e60'
down_revision = '2ab3db38e4bf'
branch_labels = None
depends_on = None


def upgrade():
    # Create monthly_investment table
    op.create_table('monthly_investment',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('planned_amount', sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column('investment_date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
        sa.ForeignKeyConstraint(['created_by'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # Create workflow table
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

    # Create workflow_action table
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


def downgrade():
    op.drop_table('workflow_action')
    op.drop_table('workflow')
    op.drop_table('monthly_investment')
