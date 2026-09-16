"""restore_client_related_tables

Revision ID: 9025ee670eee
Revises: None
Create Date: 2025-06-03 19:39:59.844772

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = '9025ee670eee'
down_revision = None
branch_labels = None
depends_on = None


def table_exists(table_name):
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade():
    # Create missing client-related tables if they don't exist
    if not table_exists('portfolio'):
        op.create_table('portfolio',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('client_id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
            sa.PrimaryKeyConstraint('id')
        )

    if not table_exists('meeting'):
        op.create_table('meeting',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('client_id', sa.Integer(), nullable=False),
            sa.Column('title', sa.String(length=200), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('meeting_date', sa.DateTime(), nullable=False),
            sa.Column('duration', sa.Integer(), nullable=True),
            sa.Column('status', sa.String(length=20), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
            sa.PrimaryKeyConstraint('id')
        )

    if not table_exists('lead'):
        op.create_table('lead',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('client_id', sa.Integer(), nullable=False),
            sa.Column('source', sa.String(length=50), nullable=True),
            sa.Column('status', sa.String(length=20), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
            sa.PrimaryKeyConstraint('id')
        )

    if not table_exists('call_log'):
        op.create_table('call_log',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('client_id', sa.Integer(), nullable=False),
            sa.Column('call_date', sa.DateTime(), nullable=False),
            sa.Column('duration', sa.Integer(), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
            sa.PrimaryKeyConstraint('id')
        )

    if not table_exists('document'):
        op.create_table('document',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('client_id', sa.Integer(), nullable=False),
            sa.Column('title', sa.String(length=200), nullable=False),
            sa.Column('file_path', sa.String(length=500), nullable=False),
            sa.Column('document_type', sa.String(length=50), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
            sa.PrimaryKeyConstraint('id')
        )

    if not table_exists('cashflow'):
        op.create_table('cashflow',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('client_id', sa.Integer(), nullable=False),
            sa.Column('amount', sa.Numeric(precision=15, scale=2), nullable=False),
            sa.Column('type', sa.String(length=20), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('date', sa.DateTime(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
            sa.PrimaryKeyConstraint('id')
        )


def downgrade():
    # Only drop tables if they exist
    if table_exists('cashflow'):
        op.drop_table('cashflow')
    if table_exists('document'):
        op.drop_table('document')
    if table_exists('call_log'):
        op.drop_table('call_log')
    if table_exists('lead'):
        op.drop_table('lead')
    if table_exists('meeting'):
        op.drop_table('meeting')
    if table_exists('portfolio'):
        op.drop_table('portfolio')
