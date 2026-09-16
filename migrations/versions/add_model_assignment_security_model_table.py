"""add model_assignment_security_model table

Revision ID: add_model_assignment_security_model_table
Revises: add_schedule_notes_to_workflow
Create Date: 2025-12-15 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_model_assignment_security_model_table'
down_revision = 'add_schedule_notes_to_workflow'
branch_labels = None
depends_on = None


def upgrade():
    # Create model_assignment_security_model table
    op.create_table(
        'model_assignment_security_model',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('model_assignment_id', sa.Integer(), nullable=False),
        sa.Column('asset_class_id', sa.Integer(), nullable=False),
        sa.Column('security_model_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'), nullable=True),
        sa.ForeignKeyConstraint(['asset_class_id'], ['asset_class.id'], ),
        sa.ForeignKeyConstraint(['model_assignment_id'], ['model_assignment.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['security_model_id'], ['security_allocation_model.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('model_assignment_id', 'asset_class_id', name='uq_model_assignment_asset_class')
    )
    
    # Create indexes
    op.create_index('idx_masm_model_assignment_id', 'model_assignment_security_model', ['model_assignment_id'])
    op.create_index('idx_masm_asset_class_id', 'model_assignment_security_model', ['asset_class_id'])


def downgrade():
    op.drop_index('idx_masm_asset_class_id', table_name='model_assignment_security_model')
    op.drop_index('idx_masm_model_assignment_id', table_name='model_assignment_security_model')
    op.drop_table('model_assignment_security_model')

