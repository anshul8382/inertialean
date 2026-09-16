"""add ai_insights_learning table

Revision ID: c3a8420e4bd8
Revises: make_call_log_client_id_nullable
Create Date: 2026-01-11 13:23:24.650298

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3a8420e4bd8'
down_revision = 'make_call_log_client_id_nullable'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'ai_insights_learning',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('client_id', sa.Integer(), sa.ForeignKey('client.id'), nullable=True),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('period_key', sa.String(length=50), nullable=True),
        sa.Column('original_summary', sa.Text(), nullable=True),
        sa.Column('original_performance_summary', sa.Text(), nullable=True),
        sa.Column('original_good_aspects', sa.Text(), nullable=True),
        sa.Column('original_areas_for_improvement', sa.Text(), nullable=True),
        sa.Column('edited_summary', sa.Text(), nullable=True),
        sa.Column('edited_performance_summary', sa.Text(), nullable=True),
        sa.Column('edited_good_aspects', sa.Text(), nullable=True),
        sa.Column('edited_areas_for_improvement', sa.Text(), nullable=True),
        sa.Column('performance_context', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )

    # Helpful indexes for matching / filtering
    op.create_index('ix_ai_insights_learning_user_id', 'ai_insights_learning', ['user_id'])
    op.create_index('ix_ai_insights_learning_client_id', 'ai_insights_learning', ['client_id'])
    op.create_index('ix_ai_insights_learning_period', 'ai_insights_learning', ['start_date', 'end_date'])


def downgrade():
    op.drop_index('ix_ai_insights_learning_period', table_name='ai_insights_learning')
    op.drop_index('ix_ai_insights_learning_client_id', table_name='ai_insights_learning')
    op.drop_index('ix_ai_insights_learning_user_id', table_name='ai_insights_learning')
    op.drop_table('ai_insights_learning')
