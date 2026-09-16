"""add security daily update tracking

Revision ID: add_security_daily_update
Revises: make_lead_email_nullable
Create Date: 2024-01-15 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_security_daily_update'
down_revision = 'make_lead_email_nullable'
branch_labels = None
depends_on = None


def upgrade():
    # Create security_daily_update table
    op.create_table('security_daily_update',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('update_date', sa.Date(), nullable=False),
        sa.Column('update_type', sa.String(length=50), nullable=False),
        sa.Column('updated_securities_count', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('update_date')
    )


def downgrade():
    # Drop security_daily_update table
    op.drop_table('security_daily_update') 