"""Create corporate actions table

Revision ID: create_corporate_actions_table
Revises: 
Create Date: 2025-10-18 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime

# revision identifiers, used by Alembic.
revision = 'create_corporate_actions_table'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    """Create corporate_actions table"""
    
    # Create corporate_actions table
    op.create_table('corporate_actions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('security_id', sa.Integer(), nullable=False),
        sa.Column('action_type', sa.String(20), nullable=False),  # SPLIT, BONUS, DIVIDEND, etc.
        sa.Column('action_date', sa.Date(), nullable=False),
        sa.Column('ratio', sa.Numeric(10, 6), nullable=False),  # Split ratio, bonus ratio, etc.
        sa.Column('description', sa.String(255), nullable=True),
        sa.Column('source', sa.String(50), nullable=False, default='MANUAL'),
        sa.Column('is_active', sa.Boolean(), nullable=False, default=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, default=datetime.utcnow),
        sa.Column('updated_at', sa.DateTime(), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['security_id'], ['security.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('security_id', 'action_date', 'action_type', name='uq_security_date_type'),
        sa.Index('idx_security_date', 'security_id', 'action_date'),
        sa.Index('idx_action_date', 'action_date'),
        sa.Index('idx_action_type', 'action_type')
    )
    
    # Migrate existing corporate actions from Transaction table
    op.execute("""
        INSERT INTO corporate_actions (security_id, action_type, action_date, ratio, description, source, is_active, created_at, updated_at)
        SELECT DISTINCT 
            security_id,
            type as action_type,
            DATE(transaction_date) as action_date,
            quantity as ratio,
            CONCAT(type, ' - ratio ', quantity) as description,
            'MIGRATED' as source,
            TRUE as is_active,
            NOW() as created_at,
            NOW() as updated_at
        FROM transaction 
        WHERE type IN ('SPLIT', 'BONUS')
        ORDER BY security_id, action_date, type
    """)
    
    print("✅ Corporate actions table created and populated")

def downgrade():
    """Drop corporate_actions table"""
    op.drop_table('corporate_actions')
    print("❌ Corporate actions table dropped")





