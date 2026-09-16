"""Make agreement template_id nullable for existing agreements

Revision ID: make_agreement_template_nullable
Revises: 
Create Date: 2025-10-15 03:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'make_agreement_template_nullable'
down_revision = 'add_generic_workflow_tables'
branch_labels = None
depends_on = None


def upgrade():
    # Make template_id nullable in agreement table
    op.alter_column('agreement', 'template_id',
                    existing_type=sa.Integer(),
                    nullable=True)


def downgrade():
    # Revert template_id to not nullable
    op.alter_column('agreement', 'template_id',
                    existing_type=sa.Integer(),
                    nullable=False)




