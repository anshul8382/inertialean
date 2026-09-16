"""increase lead phone column size

Revision ID: increase_lead_phone_column_size
Revises: add_referral_by_to_lead
Create Date: 2025-07-11 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'increase_lead_phone_column_size'
down_revision = 'add_referral_by_to_lead'
branch_labels = None
depends_on = None


def upgrade():
    # Increase phone column size from 20 to 50 characters
    op.alter_column('lead', 'phone',
                    existing_type=sa.String(20),
                    type_=sa.String(50),
                    existing_nullable=False,
                    existing_server_default='')


def downgrade():
    # Revert phone column size back to 20 characters
    op.alter_column('lead', 'phone',
                    existing_type=sa.String(50),
                    type_=sa.String(20),
                    existing_nullable=False,
                    existing_server_default='') 