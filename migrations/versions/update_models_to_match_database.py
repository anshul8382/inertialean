"""update models to match database

Revision ID: update_models_to_match_database
Revises: update_portfolio_item_foreign_key
Create Date: 2024-03-19 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'update_models_to_match_database'
down_revision = 'update_portfolio_item_foreign_key'
branch_labels = None
depends_on = None

def upgrade():
    # Update Holding table
    op.add_column('holding', sa.Column('allocation_percentage', sa.Numeric(5, 2), nullable=True))
    op.add_column('holding', sa.Column('min_allocation', sa.Numeric(5, 2), nullable=True))
    op.add_column('holding', sa.Column('max_allocation', sa.Numeric(5, 2), nullable=True))
    op.add_column('holding', sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True))
    op.add_column('holding', sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), onupdate=sa.text('CURRENT_TIMESTAMP'), nullable=True))
    op.drop_column('holding', 'average_price')

    # Update Transaction table
    op.add_column('transaction', sa.Column('amount', sa.Numeric(15, 2), nullable=False))
    op.add_column('transaction', sa.Column('portfolio_id', sa.Integer(), nullable=True))
    op.create_foreign_key(None, 'transaction', 'portfolio', ['portfolio_id'], ['id'])
    op.drop_column('transaction', 'stock_id')
    op.drop_column('transaction', 'status')
    op.drop_column('transaction', 'notes')

    # Update Lead table
    op.alter_column('lead', 'source', type_=sa.String(50), existing_type=sa.String(100))
    op.alter_column('lead', 'status', type_=sa.String(20), existing_type=sa.String(50))
    op.alter_column('lead', 'name', server_default='', existing_server_default=None)
    op.alter_column('lead', 'email', server_default='', existing_server_default=None)
    op.alter_column('lead', 'phone', server_default='', existing_server_default=None)
    op.alter_column('lead', 'user_id', server_default='1', existing_server_default=None)

    # Update Security table
    op.alter_column('security', 'meta_data', type_=sa.Text(), existing_type=sa.JSON)
    op.alter_column('security', 'refresh_interval', server_default='300', existing_server_default=None)

    # Create ModelAllocation table
    op.create_table('model_allocation',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('customization_id', sa.Integer(), nullable=False),
        sa.Column('security_model_id', sa.Integer(), nullable=False),
        sa.Column('allocation_percentage', sa.Numeric(5, 2), nullable=False),
        sa.Column('min_allocation', sa.Numeric(5, 2), nullable=True),
        sa.Column('max_allocation', sa.Numeric(5, 2), nullable=True),
        sa.ForeignKeyConstraint(['customization_id'], ['asset_class_customization.id'], ),
        sa.ForeignKeyConstraint(['security_model_id'], ['security_allocation_model.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

def downgrade():
    # Drop ModelAllocation table
    op.drop_table('model_allocation')

    # Revert Security table changes
    op.alter_column('security', 'meta_data', type_=sa.JSON(), existing_type=sa.Text)
    op.alter_column('security', 'refresh_interval', server_default=None, existing_server_default='300')

    # Revert Lead table changes
    op.alter_column('lead', 'source', type_=sa.String(100), existing_type=sa.String(50))
    op.alter_column('lead', 'status', type_=sa.String(50), existing_type=sa.String(20))
    op.alter_column('lead', 'name', server_default=None, existing_server_default='')
    op.alter_column('lead', 'email', server_default=None, existing_server_default='')
    op.alter_column('lead', 'phone', server_default=None, existing_server_default='')
    op.alter_column('lead', 'user_id', server_default=None, existing_server_default='1')

    # Revert Transaction table changes
    op.add_column('transaction', sa.Column('stock_id', sa.Integer(), nullable=True))
    op.add_column('transaction', sa.Column('status', sa.String(20), nullable=False))
    op.add_column('transaction', sa.Column('notes', sa.Text(), nullable=True))
    op.drop_constraint(None, 'transaction', type_='foreignkey')
    op.drop_column('transaction', 'portfolio_id')
    op.drop_column('transaction', 'amount')

    # Revert Holding table changes
    op.add_column('holding', sa.Column('average_price', sa.Numeric(15, 2), nullable=False))
    op.drop_column('holding', 'updated_at')
    op.drop_column('holding', 'created_at')
    op.drop_column('holding', 'max_allocation')
    op.drop_column('holding', 'min_allocation')
    op.drop_column('holding', 'allocation_percentage') 