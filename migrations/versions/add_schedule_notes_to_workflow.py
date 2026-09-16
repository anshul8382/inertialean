"""add schedule_notes field to workflow

Revision ID: add_schedule_notes_to_workflow
Revises: cf4203006e60
Create Date: 2025-12-15 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_schedule_notes_to_workflow'
down_revision = 'make_call_log_client_id_nullable'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('workflow', sa.Column('schedule_notes', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('workflow', 'schedule_notes')


