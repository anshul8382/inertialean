"""Add audit trail fields to lead table

Revision ID: add_lead_audit_trail
Revises: add_missing_lead_fields
Create Date: 2025-01-08 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = 'add_lead_audit_trail'
down_revision = 'add_missing_lead_fields'
branch_labels = None
depends_on = None

def upgrade():
    # Get database connection
    conn = op.get_bind()
    inspector = inspect(conn)
    
    # Check if lead table exists
    if 'lead' not in inspector.get_table_names():
        return
    
    # Add audit trail fields to lead table
    op.add_column('lead', sa.Column('status_changed_at', sa.DateTime(), nullable=True))
    op.add_column('lead', sa.Column('status_changed_by', sa.Integer(), nullable=True))
    op.add_column('lead', sa.Column('previous_status', sa.String(50), nullable=True))
    op.add_column('lead', sa.Column('status_notes', sa.Text(), nullable=True))
    
    # Add foreign key for status_changed_by
    op.create_foreign_key(
        'fk_lead_status_changed_by',
        'lead', 'user', ['status_changed_by'], ['id']
    )
    
    # Create index for better performance
    op.create_index('ix_lead_status_changed_at', 'lead', ['status_changed_at'])

def downgrade():
    # Get database connection
    conn = op.get_bind()
    inspector = inspect(conn)
    
    # Check if lead table exists
    if 'lead' not in inspector.get_table_names():
        return
    
    # Drop index
    op.drop_index('ix_lead_status_changed_at', 'lead')
    
    # Drop foreign key
    op.drop_constraint('fk_lead_status_changed_by', 'lead', type_='foreignkey')
    
    # Drop columns
    op.drop_column('lead', 'status_notes')
    op.drop_column('lead', 'previous_status')
    op.drop_column('lead', 'status_changed_by')
    op.drop_column('lead', 'status_changed_at') 