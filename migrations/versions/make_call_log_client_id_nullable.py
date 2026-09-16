"""make call_log client_id nullable

Revision ID: make_call_log_client_id_nullable
Revises: make_lead_client_id_nullable
Create Date: 2024-03-17 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'make_call_log_client_id_nullable'
down_revision = 'make_lead_client_id_nullable'
branch_labels = None
depends_on = None

def upgrade():
    # Make client_id nullable in call_log table
    op.alter_column('call_log', 'client_id',
               existing_type=sa.Integer(),
               nullable=True)

def downgrade():
    # Make client_id non-nullable in call_log table
    op.alter_column('call_log', 'client_id',
               existing_type=sa.Integer(),
               nullable=False) 