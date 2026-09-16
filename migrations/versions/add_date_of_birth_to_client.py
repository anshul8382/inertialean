"""Add date_of_birth to client table

Revision ID: add_date_of_birth_to_client
Revises: 9025ee670eee
Create Date: 2024-01-15 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_date_of_birth_to_client'
down_revision = '9025ee670eee'
branch_labels = None
depends_on = None


def upgrade():
    # Add date_of_birth column to client table
    op.add_column('client', sa.Column('date_of_birth', sa.Date(), nullable=True))


def downgrade():
    # Remove date_of_birth column from client table
    op.drop_column('client', 'date_of_birth')
