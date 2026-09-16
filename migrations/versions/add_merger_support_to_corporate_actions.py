"""Add merger/conversion support to corporate actions

Revision ID: add_merger_support
Revises: 
Create Date: 2025-10-19 14:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_merger_support'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Add new columns for merger/conversion support
    op.add_column('corporate_actions', sa.Column('source_security_id', sa.Integer(), nullable=True))
    op.add_column('corporate_actions', sa.Column('source_quantity', sa.Numeric(15, 4), nullable=True))
    
    # Add foreign key constraint for source_security_id
    op.create_foreign_key(
        'fk_corporate_action_source_security',
        'corporate_actions', 'security',
        ['source_security_id'], ['id']
    )
    
    # Add index for source_security_id
    op.create_index('idx_source_security', 'corporate_actions', ['source_security_id'])
    
    print("✅ Added merger/conversion support to corporate_actions table")
    print("   - source_security_id: Security being merged FROM")
    print("   - source_quantity: Quantity being converted")


def downgrade():
    op.drop_index('idx_source_security', table_name='corporate_actions')
    op.drop_constraint('fk_corporate_action_source_security', 'corporate_actions', type_='foreignkey')
    op.drop_column('corporate_actions', 'source_quantity')
    op.drop_column('corporate_actions', 'source_security_id')




