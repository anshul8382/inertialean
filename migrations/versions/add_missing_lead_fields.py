"""Add missing fields to Lead model

Revision ID: add_missing_lead_fields
Revises: 9025ee670eee
Create Date: 2025-06-08 08:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = 'add_missing_lead_fields'
down_revision = '9025ee670eee'
branch_labels = None
depends_on = None

def upgrade():
    # Get database connection
    conn = op.get_bind()
    inspector = inspect(conn)
    
    # Check if lead table exists
    if 'lead' not in inspector.get_table_names():
        return
        
    # Create foreign key if user table exists
    if 'user' in inspector.get_table_names():
        op.create_foreign_key(
            'fk_lead_user_id',
            'lead',  # source table
            'user',  # target table
            ['user_id'],  # source columns
            ['id']  # target columns
        )

def downgrade():
    # Get database connection
    conn = op.get_bind()
    inspector = inspect(conn)
    
    # Check if lead table exists
    if 'lead' not in inspector.get_table_names():
        return
        
    # Drop foreign key if it exists
    if 'user' in inspector.get_table_names():
        op.drop_constraint('fk_lead_user_id', 'lead', type_='foreignkey') 