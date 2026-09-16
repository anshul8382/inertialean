"""fix lead cascade delete constraints

Revision ID: fix_lead_cascade_delete
Revises: add_security_daily_update
Create Date: 2024-01-15 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'fix_lead_cascade_delete'
down_revision = 'add_security_daily_update'
branch_labels = None
depends_on = None


def upgrade():
    # Drop existing foreign key constraints
    op.drop_constraint('lead_call_log_ibfk_1', 'lead_call_log', type_='foreignkey')
    op.drop_constraint('meeting_ibfk_2', 'meeting', type_='foreignkey')
    
    # Recreate foreign key constraints with CASCADE DELETE
    op.create_foreign_key(
        'lead_call_log_ibfk_1', 'lead_call_log', 'lead',
        ['lead_id'], ['id'], ondelete='CASCADE'
    )
    op.create_foreign_key(
        'meeting_ibfk_2', 'meeting', 'lead',
        ['lead_id'], ['id'], ondelete='CASCADE'
    )


def downgrade():
    # Drop CASCADE constraints
    op.drop_constraint('lead_call_log_ibfk_1', 'lead_call_log', type_='foreignkey')
    op.drop_constraint('meeting_ibfk_2', 'meeting', type_='foreignkey')
    
    # Recreate original constraints without CASCADE
    op.create_foreign_key(
        'lead_call_log_ibfk_1', 'lead_call_log', 'lead',
        ['lead_id'], ['id']
    )
    op.create_foreign_key(
        'meeting_ibfk_2', 'meeting', 'lead',
        ['lead_id'], ['id']
    ) 