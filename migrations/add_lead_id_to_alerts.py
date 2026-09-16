"""Add lead_id to alert table

Revision ID: add_lead_id_to_alerts
Revises: 
Create Date: 2025-08-20 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_lead_id_to_alerts'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    # Add lead_id column to alert table
    op.add_column('alert', sa.Column('lead_id', sa.Integer(), nullable=True))
    
    # Add foreign key constraint
    op.create_foreign_key(
        'alert_lead_id_fkey',
        'alert', 'lead',
        ['lead_id'], ['id'],
        ondelete='SET NULL'
    )
    
    # Add index for better performance
    op.create_index('idx_alert_lead_id', 'alert', ['lead_id'])

def downgrade():
    # Remove index
    op.drop_index('idx_alert_lead_id', 'alert')
    
    # Remove foreign key constraint
    op.drop_constraint('alert_lead_id_fkey', 'alert', type_='foreignkey')
    
    # Remove column
    op.drop_column('alert', 'lead_id')

