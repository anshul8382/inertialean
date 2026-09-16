"""add mprofit symbol map table

Revision ID: add_mprofit_symbol_map
Revises: add_monthly_schedule
Create Date: 2025-11-13 12:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_mprofit_symbol_map'
down_revision = 'add_monthly_schedule'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'mprofit_symbol_map',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('raw_name', sa.String(length=200), nullable=False),
        sa.Column('normalized_name', sa.String(length=200), nullable=False),
        sa.Column('security_id', sa.Integer(), nullable=True),
        sa.Column('nse_symbol', sa.String(length=50), nullable=True),
        sa.Column('is_manual', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('notes', sa.String(length=500), nullable=True),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP'), server_onupdate=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['security_id'], ['security.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('raw_name', name='uq_mprofit_symbol_map_raw_name')
    )
    op.create_index('ix_mprofit_symbol_map_normalized_name', 'mprofit_symbol_map', ['normalized_name'], unique=False)


def downgrade():
    op.drop_index('ix_mprofit_symbol_map_normalized_name', table_name='mprofit_symbol_map')
    op.drop_table('mprofit_symbol_map')


