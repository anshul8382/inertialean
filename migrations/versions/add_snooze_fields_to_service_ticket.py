"""Add snooze fields to service_ticket

Revision ID: add_snooze_fields_to_service_ticket
Revises: None
Create Date: 2026-01-13 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_snooze_fields_to_service_ticket'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('service_ticket', sa.Column('pre_snooze_status', sa.String(length=20), nullable=True))
    op.add_column('service_ticket', sa.Column('snoozed_until', sa.DateTime(), nullable=True))
    op.add_column('service_ticket', sa.Column('snoozed_at', sa.DateTime(), nullable=True))
    op.add_column('service_ticket', sa.Column('snoozed_by', sa.Integer(), nullable=True))
    op.add_column('service_ticket', sa.Column('snooze_reason', sa.Text(), nullable=True))

    op.create_index('ix_service_ticket_snoozed_by', 'service_ticket', ['snoozed_by'], unique=False)

    bind = op.get_bind()
    if bind is not None and bind.dialect.name != 'sqlite':
        op.create_foreign_key(
            'service_ticket_ibfk_5',
            'service_ticket',
            'user',
            ['snoozed_by'],
            ['id'],
            ondelete='SET NULL'
        )


def downgrade():
    bind = op.get_bind()
    if bind is not None and bind.dialect.name != 'sqlite':
        op.drop_constraint('service_ticket_ibfk_5', 'service_ticket', type_='foreignkey')

    op.drop_index('ix_service_ticket_snoozed_by', table_name='service_ticket')
    op.drop_column('service_ticket', 'snooze_reason')
    op.drop_column('service_ticket', 'snoozed_by')
    op.drop_column('service_ticket', 'snoozed_at')
    op.drop_column('service_ticket', 'snoozed_until')
    op.drop_column('service_ticket', 'pre_snooze_status')





