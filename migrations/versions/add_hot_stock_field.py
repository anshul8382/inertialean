"""Add is_hot_stock field to Security model

Revision ID: add_hot_stock_field
Revises: 9025ee670eee
Create Date: 2025-01-27 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_hot_stock_field'
down_revision = '9025ee670eee'
branch_labels = None
depends_on = None


def upgrade():
    # Add is_hot_stock column to security table
    op.add_column('security', sa.Column('is_hot_stock', sa.Boolean(), nullable=False, server_default='0'))


def downgrade():
    # Remove is_hot_stock column from security table
    op.drop_column('security', 'is_hot_stock') 