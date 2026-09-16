"""make lead client_id nullable

Revision ID: make_lead_client_id_nullable
Revises: 
Create Date: 2024-03-16 05:32:00.328735

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'make_lead_client_id_nullable'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    # Make client_id nullable in lead table
    op.alter_column('lead', 'client_id',
               existing_type=sa.Integer(),
               nullable=True)

def downgrade():
    # Make client_id non-nullable in lead table
    op.alter_column('lead', 'client_id',
               existing_type=sa.Integer(),
               nullable=False) 