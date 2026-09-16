"""add recommended trade table

Revision ID: add_recommended_trade_table
Revises: update_models_to_match_database
Create Date: 2025-01-27 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'add_recommended_trade_table'
down_revision = 'update_models_to_match_database'
branch_labels = None
depends_on = None


def upgrade():
    # Create recommended_trade table
    op.create_table('recommended_trade',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('security_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(20), nullable=False),
        sa.Column('quantity', sa.Numeric(15, 4), nullable=False),
        sa.Column('recommended_price', sa.Numeric(15, 2), nullable=False),
        sa.Column('actual_price', sa.Numeric(15, 2), nullable=True),
        sa.Column('status', sa.String(20), nullable=True, default='pending'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.func.current_timestamp()),
        sa.Column('executed_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('executed_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
        sa.ForeignKeyConstraint(['security_id'], ['security.id'], ),
        sa.ForeignKeyConstraint(['created_by'], ['user.id'], ),
        sa.ForeignKeyConstraint(['executed_by'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    # Drop recommended_trade table
    op.drop_table('recommended_trade') 