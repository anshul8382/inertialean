"""Add generic workflow tables

Revision ID: add_generic_workflow_tables
Revises: add_missing_lead_fields
Create Date: 2025-01-08 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = 'add_generic_workflow_tables'
down_revision = 'add_missing_lead_fields'
branch_labels = None
depends_on = None

def upgrade():
    # Get database connection
    conn = op.get_bind()
    inspector = inspect(conn)
    
    # Create generic_workflow table
    op.create_table('generic_workflow',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('module_type', sa.String(length=50), nullable=False),
        sa.Column('record_id', sa.Integer(), nullable=False),
        sa.Column('current_stage', sa.String(length=50), nullable=False),
        sa.Column('target_completion_date', sa.Date(), nullable=False),
        sa.Column('actual_completion_date', sa.Date(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # Create generic_workflow_action table
    op.create_table('generic_workflow_action',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workflow_id', sa.Integer(), nullable=False),
        sa.Column('action_type', sa.String(length=100), nullable=False),
        sa.Column('action_date', sa.DateTime(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.ForeignKeyConstraint(['workflow_id'], ['generic_workflow.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes for better performance
    op.create_index('ix_generic_workflow_module_record', 'generic_workflow', ['module_type', 'record_id'])
    op.create_index('ix_generic_workflow_status', 'generic_workflow', ['status'])
    op.create_index('ix_generic_workflow_created_by', 'generic_workflow', ['created_by'])
    op.create_index('ix_generic_workflow_action_workflow_id', 'generic_workflow_action', ['workflow_id'])
    op.create_index('ix_generic_workflow_action_user_id', 'generic_workflow_action', ['user_id'])

def downgrade():
    # Drop indexes
    op.drop_index('ix_generic_workflow_action_user_id', 'generic_workflow_action')
    op.drop_index('ix_generic_workflow_action_workflow_id', 'generic_workflow_action')
    op.drop_index('ix_generic_workflow_created_by', 'generic_workflow')
    op.drop_index('ix_generic_workflow_status', 'generic_workflow')
    op.drop_index('ix_generic_workflow_module_record', 'generic_workflow')
    
    # Drop tables
    op.drop_table('generic_workflow_action')
    op.drop_table('generic_workflow') 