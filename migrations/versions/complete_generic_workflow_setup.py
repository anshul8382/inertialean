"""Complete generic workflow tables setup

Revision ID: complete_generic_workflow_setup
Revises: add_missing_lead_fields
Create Date: 2025-01-08 12:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = 'complete_generic_workflow_setup'
down_revision = 'add_missing_lead_fields'
branch_labels = None
depends_on = None

def upgrade():
    # Get database connection
    conn = op.get_bind()
    inspector = inspect(conn)
    
    # Check if generic_workflow table exists
    if 'generic_workflow' not in inspector.get_table_names():
        return
    
    # Add missing indexes for better performance
    try:
        op.create_index('ix_generic_workflow_module_record', 'generic_workflow', ['module_type', 'record_id'])
    except:
        pass  # Index might already exist
    
    try:
        op.create_index('ix_generic_workflow_status', 'generic_workflow', ['status'])
    except:
        pass  # Index might already exist
    
    try:
        op.create_index('ix_generic_workflow_current_stage', 'generic_workflow', ['current_stage'])
    except:
        pass  # Index might already exist
    
    try:
        op.create_index('ix_generic_workflow_target_date', 'generic_workflow', ['target_completion_date'])
    except:
        pass  # Index might already exist
    
    # Add missing indexes for generic_workflow_action
    if 'generic_workflow_action' in inspector.get_table_names():
        try:
            op.create_index('ix_generic_workflow_action_action_date', 'generic_workflow_action', ['action_date'])
        except:
            pass  # Index might already exist
        
        try:
            op.create_index('ix_generic_workflow_action_action_type', 'generic_workflow_action', ['action_type'])
        except:
            pass  # Index might already exist

def downgrade():
    # Get database connection
    conn = op.get_bind()
    inspector = inspect(conn)
    
    # Check if tables exist before dropping indexes
    if 'generic_workflow' in inspector.get_table_names():
        try:
            op.drop_index('ix_generic_workflow_target_date', 'generic_workflow')
        except:
            pass
        
        try:
            op.drop_index('ix_generic_workflow_current_stage', 'generic_workflow')
        except:
            pass
        
        try:
            op.drop_index('ix_generic_workflow_status', 'generic_workflow')
        except:
            pass
        
        try:
            op.drop_index('ix_generic_workflow_module_record', 'generic_workflow')
        except:
            pass
    
    if 'generic_workflow_action' in inspector.get_table_names():
        try:
            op.drop_index('ix_generic_workflow_action_action_type', 'generic_workflow_action')
        except:
            pass
        
        try:
            op.drop_index('ix_generic_workflow_action_action_date', 'generic_workflow_action')
        except:
            pass 